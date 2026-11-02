"""DNS получателя молчит — письмо ждёт, а не уходит в отказ (`letters/recipient_dns.py`).

Сеть подменена: тест решает, что ответил DNS, а путь письма настоящий — отправка, пачка, проход
автоотправки, проход добивок, ручка. Держат три вещи: письмо не ушло и ящик не занят; домен не
спрашивается на каждое письмо; молчание не держит письмо вечно и не держит очередь, когда спросить
некого.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta

import pytest
from backend.config import contacts as contacts_cfg
from backend.config import outreach as outreach_cfg
from backend.features.contacts import mx
from backend.features.contacts.mx import MailRoute
from backend.features.core.domain import MessageStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import autosend, batch, recipient_dns
from backend.features.letters.followups import send_due
from backend.features.letters.sending import RecipientDnsError, Sending
from backend.features.letters.transport import NullTransport, Outgoing
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_letters_send_queue import _queue
from tests.thread_letters import answer, conversation, first_letter, human_reply

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
ADDRESS = "editor@silent.example.test"
HOST = "silent.example.test"

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


class Postman(NullTransport):
    """Настоящая почта для отправки: запоминает, что ей отдали."""

    name = "postman"
    real = True

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[str] = []

    async def send(self, outgoing: Outgoing) -> str:
        self.sent.append(outgoing.to)
        return f"pm-{outgoing.message_id}"


class Dns:
    """Что отвечает DNS: молчащие домены и сколько раз спросили о каждом."""

    def __init__(self) -> None:
        self.silent: set[str] = set()
        self.asked: list[str] = []
        self.timeouts: list[float | None] = []

    async def __call__(self, host: str, *, dns_timeout_sec: float | None = None) -> MailRoute:
        self.asked.append(host)
        self.timeouts.append(dns_timeout_sec)
        return MailRoute.UNKNOWN if host in self.silent else MailRoute.MX


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> Dns:
    found = Dns()
    monkeypatch.setattr(recipient_dns, "mail_route", found)
    return found


class TestTheQuestion:
    @pytest.mark.parametrize(
        ("route", "held"),
        [
            (MailRoute.UNKNOWN, True),
            (MailRoute.MX, False),
            (MailRoute.IMPLICIT, False),
            (MailRoute.NONE, False),
            (MailRoute.NULL_MX, False),
        ],
    )
    async def test_only_silence_holds_the_letter(
        self, monkeypatch: pytest.MonkeyPatch, route: MailRoute, held: bool
    ) -> None:
        """«Домена нет» и «почты нет» — ответы: такому письму здесь не мешают."""

        async def said(_host: str, *, dns_timeout_sec: float | None = None) -> MailRoute:
            return route

        monkeypatch.setattr(recipient_dns, "mail_route", said)

        assert await recipient_dns.holds(ADDRESS, now=NOW) is held

    async def test_the_domain_is_asked_as_dns_sees_it(self, dns: Dns) -> None:
        await recipient_dns.holds("  Editor@Silent.Example.TEST ", now=NOW)

        assert dns.asked == [HOST]
        assert dns.timeouts == [recipient_dns.ASK_SEC]

    async def test_an_address_without_a_domain_is_not_held(self, dns: Dns) -> None:
        assert await recipient_dns.holds("", now=NOW) is False
        assert dns.asked == []

    async def test_a_silent_domain_is_asked_once_in_a_while(self, dns: Dns) -> None:
        """Проход раз в минуту: на письмо спрашивать нельзя, молчащий домен стоит секунды."""
        dns.silent.add(HOST)

        minutes = [0, 1, 7, 14]
        held = [await recipient_dns.holds(ADDRESS, now=NOW + timedelta(minutes=m)) for m in minutes]
        again = await recipient_dns.holds(
            "other@silent.example.test", now=NOW + recipient_dns.RECHECK
        )

        assert all(held)
        assert again
        assert dns.asked == [HOST, HOST]

    async def test_the_domain_that_answered_is_forgotten(self, dns: Dns) -> None:
        dns.silent.add(HOST)
        assert await recipient_dns.holds(ADDRESS, now=NOW)

        dns.silent.clear()
        later = NOW + recipient_dns.RECHECK

        assert await recipient_dns.holds(ADDRESS, now=later) is False
        assert recipient_dns.MEMORY.silent == {}

    async def test_a_new_silence_counts_from_its_own_start(self, dns: Dns) -> None:
        """Домен ответил и снова замолчал: три дня — от нового молчания, а не от первого."""
        dns.silent.add(HOST)
        await recipient_dns.holds(ADDRESS, now=NOW)
        dns.silent.clear()
        await recipient_dns.holds(ADDRESS, now=NOW + timedelta(days=2))
        dns.silent.add(HOST)

        assert await recipient_dns.holds(ADDRESS, now=NOW + timedelta(days=4))

    async def test_three_days_of_silence_stop_holding(self, dns: Dns) -> None:
        """Домен не лежит, а умер: письмо идёт своим путём и получает свой отказ."""
        dns.silent.add(HOST)
        await recipient_dns.holds(ADDRESS, now=NOW)

        almost = NOW + recipient_dns.LONGEST - timedelta(minutes=1)
        over = NOW + recipient_dns.LONGEST

        assert await recipient_dns.holds(ADDRESS, now=almost)
        assert await recipient_dns.holds(ADDRESS, now=over) is False
        # И между проверками, и на следующем вопросе: срок один.
        assert await recipient_dns.holds(ADDRESS, now=over + timedelta(minutes=1)) is False
        assert await recipient_dns.holds(ADDRESS, now=over + recipient_dns.RECHECK) is False

    async def test_nobody_to_ask_holds_nothing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        dns: Dns,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Системный резолвер выключен на процесс, запасных нет: «молчит» — наша слепота, и держать
        из-за неё очередь нельзя. Журнал говорит это раз на процесс, а не на письмо."""
        dns.silent.add(HOST)
        health = mx._ResolverHealth()
        for _ in range(mx.SYSTEM_FAILURES_BEFORE_SKIP):
            health.note_failure()
        monkeypatch.setattr(mx, "health", health)
        monkeypatch.setattr(contacts_cfg, "DNS_FALLBACK", " , ")

        with caplog.at_level(logging.WARNING, logger=recipient_dns.__name__):
            first = await recipient_dns.holds(ADDRESS, now=NOW)
            second = await recipient_dns.holds(ADDRESS, now=NOW)

        assert (first, second) == (False, False)
        assert dns.asked == []
        assert sum("спросить некого" in row.message for row in caplog.records) == 1


class TestTheLetter:
    async def test_the_letter_waits_and_nothing_is_spent(
        self, session: AsyncSession, filled_legal: None, dns: Dns
    ) -> None:
        """Письма не ушло: в очереди, ящик не занят, почта его не видела, «ушло» нигде нет."""
        await conversation(session, sent_at=NOW - timedelta(days=9), host="other.example.test")
        letter = await first_letter(session, host=HOST)
        await session.commit()
        dns.silent.add(HOST)
        postman = Postman()

        with pytest.raises(RecipientDnsError) as refused:
            await Sending(session, postman, now=NOW).send(letter.id)

        assert postman.sent == []
        await session.refresh(letter)
        assert letter.status is MessageStatus.QUEUED
        assert letter.sender_id is None
        assert letter.sent_at is None
        assert letter.failure_reason is None
        assert refused.value.delay == recipient_dns.WAIT
        assert f"DNS домена {HOST} не отвечает" in str(refused.value)

    async def test_it_goes_when_the_domain_answers(
        self, session: AsyncSession, filled_legal: None, dns: Dns
    ) -> None:
        await conversation(session, sent_at=NOW - timedelta(days=9), host="other.example.test")
        letter = await first_letter(session, host=HOST)
        await session.commit()
        dns.silent.add(HOST)
        postman = Postman()
        with pytest.raises(RecipientDnsError):
            await Sending(session, postman, now=NOW).send(letter.id)

        dns.silent.clear()
        await Sending(session, postman, now=NOW + recipient_dns.RECHECK).send(letter.id)

        await session.refresh(letter)
        assert letter.status is MessageStatus.SENT
        assert postman.sent == [f"editor@{HOST}"]

    async def test_the_null_transport_asks_nothing(
        self, session: AsyncSession, filled_legal: None, dns: Dns
    ) -> None:
        """Нулевой транспорт никуда не пишет: смотреть экран можно и без сети."""
        await conversation(session, sent_at=NOW, host=HOST)

        assert dns.asked == []

    async def test_an_answer_in_a_thread_does_not_wait(
        self, session: AsyncSession, filled_legal: None, dns: Dns
    ) -> None:
        """Собеседник написал только что: ответ уходит, как шёл."""
        talk = await conversation(session, sent_at=NOW - timedelta(days=1), host=HOST)
        reply = await human_reply(session, talk)
        await session.commit()
        dns.silent.add(HOST)
        postman = Postman()

        sent = await answer(session, talk, reply, postman)

        assert sent.status is MessageStatus.SENT
        assert dns.asked == []

    async def test_the_button_says_it_in_words(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        dns: Dns,
        monkeypatch: pytest.MonkeyPatch,
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        """Отправка из карточки: отказ словами, письмо в очереди."""
        await conversation(session, sent_at=NOW - timedelta(days=9), host="other.example.test")
        letter = await first_letter(session, host=HOST)
        await session.commit()
        dns.silent.add(HOST)
        await make_user("admin@example.test", role=UserRole.ADMIN)
        token = await sign_in("admin@example.test")
        monkeypatch.setattr("backend.api.letters.routes.Transports", Postman)

        answered = await client.post(f"/api/letters/{letter.id}/send", headers=bearer(token))

        assert answered.status_code == 409, answered.text
        assert f"DNS домена {HOST} не отвечает" in answered.json()["detail"]
        await session.refresh(letter)
        assert letter.status is MessageStatus.QUEUED


class TestTheBatch:
    async def test_the_batch_goes_past_a_silent_domain(
        self, session: AsyncSession, filled_legal: None, dns: Dns
    ) -> None:
        """Отказ одному письму пачку не останавливает, и назван он словами, а не «почта отказала»."""
        letters = await _queue(session, 3)
        dns.silent.add("donor1.example.test")
        postman = Postman()

        report = await batch.send_queue(session, postman, stage=Stage.DONORS)

        assert report.sent == 2
        assert report.refused == {batch.DNS_WAITS: 1}
        assert report.stopped is None
        assert report.left == 1
        await session.refresh(letters[1])
        assert letters[1].status is MessageStatus.QUEUED

    def test_the_reason_is_named(self) -> None:
        assert batch.why(RecipientDnsError("молчит", delay=recipient_dns.WAIT)) == batch.DNS_WAITS


class TestThePasses:
    @pytest.fixture(autouse=True)
    def _always_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(outreach_cfg, "AUTOSEND_DAYS", frozenset(range(7)))
        monkeypatch.setattr(outreach_cfg, "AUTOSEND_OPENS", time.min)
        monkeypatch.setattr(outreach_cfg, "AUTOSEND_CLOSES", time.max)

    async def test_a_queue_waiting_for_dns_is_not_a_stall(
        self, session: AsyncSession, filled_legal: None, dns: Dns
    ) -> None:
        """Вся очередь окна на молчащих доменах — ожидание, как окно получателя: тревоги нет."""
        await _queue(session, 2)
        dns.silent.update({"donor0.example.test", "donor1.example.test"})
        mode, fresh = await autosend.start(session, Stage.DONORS, "links", by=None, now=NOW)
        assert fresh
        await session.commit()

        passed = await autosend.run(session, Postman(), mode, now=datetime.now(UTC))

        assert passed.stall is None
        assert f"{batch.DNS_WAITS} — 2" in passed.state
        assert mode.stopped_at is None

    def test_a_real_refusal_next_to_it_is_still_a_stall(self) -> None:
        """Контроль: ожидание DNS не прячет настоящий отказ рядом — проход стоит не по делу."""
        report = batch.BatchReport()
        report.refused[batch.DNS_WAITS] += 1
        report.refused["стоп-лист"] += 1

        assert autosend._stalled(report)

    async def test_a_followup_comes_back_later_and_goes(
        self, session: AsyncSession, filled_legal: None, dns: Dns
    ) -> None:
        """Срок погашен захватом и возвращён: цепочка жива, строка добивки одна, ушла с ящика
        переписки, когда домен ответил."""
        talk = await conversation(session, sent_at=NOW - timedelta(days=4), host=HOST)
        await session.commit()
        dns.silent.add(HOST)
        postman = Postman()

        first = await send_due(session, transport=postman, limit=5, now=NOW)

        await session.refresh(talk.first)
        assert (first.sent, first.postponed) == (0, 1)
        assert talk.first.next_action_at == NOW + recipient_dns.WAIT
        assert postman.sent == []

        dns.silent.clear()
        later = NOW + recipient_dns.WAIT
        second = await send_due(session, transport=postman, limit=5, now=later)

        assert (second.sent, second.postponed) == (1, 0)
        assert postman.sent == [f"editor@{HOST}"]
        steps = await session.scalar(
            select(func.count())
            .select_from(MessageModel)
            .where(MessageModel.thread_id == talk.thread.id, MessageModel.step == 1)
        )
        assert steps == 1
