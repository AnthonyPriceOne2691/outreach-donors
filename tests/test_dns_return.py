"""Письмо, отбитое молчащим DNS получателя, возвращается в работу (`letters/dns_return.py`).

Чужой сбой не должен стоить разговора: отказ гасит цепочку, а лид жив. Письма здесь настоящие —
ушли отправкой, добивка рождена проходом, отказ пришёл событием; возврат — тем же путём, что
«Вернуть в очередь». Держат три вещи: письмо возвращается одно и один раз, после событий своей
попытки; цепочка, которая кончилась, не оживает; проход разбора от возврата не падает.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.config import storage
from backend.features.core import stages
from backend.features.core.domain import AuditAction, MessageStatus, Stage, ThreadStatus
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.outreach import MessageModel
from backend.features.core.stages import MailPolicy
from backend.features.letters import dns_return, recipient_dns
from backend.features.letters.chain import ANSWER_STEP, RECHECK_STEP
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.letters.followups import send_due
from backend.features.letters.sending import RecipientDnsError, Sending
from backend.features.letters.transport import NullTransport
from backend.features.outreach import senders
from backend.features.outreach.health import SoftSignals
from backend.workers import reaper
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN, make_sender
from tests.test_delivery_events import _keypair
from tests.test_delivery_events_repeat import _deliver, _event
from tests.test_recipient_dns_hold import Dns, Postman
from tests.test_sales_soft_signals import NOW, _box
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_send_race import MEETING, committed_sessions
from tests.thread_letters import Conversation, conversation, first_letter

OUTAGE = (
    "unable to get mx info: failed to get IPs from PTR record: lookup <nil>: unrecognized address"
)
HOST = "silent.example.test"
EDITOR = f"editor@{HOST}"
SENT = NOW - timedelta(hours=2)


@pytest.fixture(autouse=True)
def ruled(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Модуль продаж подключён: окно ящика — порогами по умолчанию."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(soft=SoftSignals(), watch=True))
    stages.register_sales(lambda: found)
    return found


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> Dns:
    found = Dns()
    monkeypatch.setattr(recipient_dns, "mail_route", found)
    return found


@pytest.fixture
async def talk(session: AsyncSession, filled_legal: None) -> Conversation:
    """Первое письмо ушло два часа назад."""
    return await conversation(session, sent_at=SENT, host=HOST)


async def _refused(
    session: AsyncSession, letter: MessageModel, reason: str = OUTAGE, *, soft: bool = True
) -> None:
    """Отказ настоящим событием: минуту спустя после отправки письма."""
    assert letter.sent_at is not None
    at = letter.sent_at + timedelta(minutes=1)
    event = DeliveryEvent("bounce", letter.id, EDITOR, reason=reason, soft=soft, at=at)
    await apply_events(session, [event], now=at)
    await session.flush()


async def _followup(session: AsyncSession, talk: Conversation) -> MessageModel:
    """Добивка ушла проходом добивок; срок первого письма погашен её захватом."""
    assert talk.first.next_action_at is not None
    due = talk.first.next_action_at
    report = await send_due(session, transport=NullTransport(), limit=5, now=due)
    assert report.sent == 1
    found = await session.scalar(
        select(MessageModel).where(MessageModel.thread_id == talk.thread.id, MessageModel.step == 1)
    )
    assert found is not None
    return found


class TestFirstLetter:
    async def test_it_goes_back_to_the_queue(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        letter = talk.first
        await _refused(session, letter)

        returned = await dns_return.return_letters(session, now=NOW)

        assert returned == 1
        assert letter.status is MessageStatus.QUEUED
        assert letter.sent_at is None
        assert letter.delivered_at is None
        assert letter.failure_reason is None
        assert letter.deferred_since == SENT  # отсрочек не было: след поставлен от отправки

    async def test_the_journal_says_why(self, session: AsyncSession, talk: Conversation) -> None:
        await _refused(session, talk.first)

        await dns_return.return_letters(session, now=NOW)
        await session.flush()

        row = await session.scalar(
            select(AuditLogModel).where(
                AuditLogModel.target == f"message:{talk.first.id}",
                AuditLogModel.action == AuditAction.USER_UPDATED,
            )
        )
        assert row is not None
        assert row.user_id is None
        assert "молчания DNS" in str(row.details)

    async def test_its_deferrals_do_not_stay_on_the_new_attempt(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        """Отсрочки той попытки — не об этой: письмо снова без причины, остаётся только след."""
        letter = talk.first
        at = SENT + timedelta(seconds=30)
        deferred = DeliveryEvent("deferred", letter.id, EDITOR, reason=OUTAGE, at=at, attempt=2)
        await apply_events(session, [deferred], now=at)
        await _refused(session, letter)

        await dns_return.return_letters(session, now=NOW)

        assert (letter.deferred_at, letter.deferred_kind, letter.deferred_reason) == (None,) * 3
        assert letter.deferred_attempts is None
        assert letter.deferred_since == at

    async def test_not_before_the_events_of_the_attempt_are_over(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        """Письмо в очереди с событием считается ушедшим: запоздавшая отсрочка сняла бы его."""
        letter = talk.first
        await _refused(session, letter)

        early = await dns_return.return_letters(
            session, now=SENT + dns_return.SETTLE - timedelta(seconds=1)
        )

        assert early == 0
        assert letter.status is MessageStatus.BOUNCED

    @pytest.mark.parametrize(
        "reason",
        [
            "550 5.1.1 The email account that you tried to reach does not exist",
            "error dialing remote address: dial tcp 192.0.2.10:25: i/o timeout",
            "dial tcp: lookup mx.example.com: no such host",
            "550 5.7.1 Message rejected under suspicion of SPAM",
        ],
    )
    async def test_only_dns_silence_comes_back(
        self, session: AsyncSession, talk: Conversation, reason: str
    ) -> None:
        await _refused(session, talk.first, reason)

        assert await dns_return.return_letters(session, now=NOW) == 0
        assert talk.first.status is MessageStatus.BOUNCED

    async def test_the_platform_giving_up_is_read_by_the_last_deferral(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        letter = talk.first
        at = SENT + timedelta(seconds=30)
        deferred = DeliveryEvent("deferred", letter.id, EDITOR, reason=OUTAGE, at=at)
        await apply_events(session, [deferred], now=at)
        await _refused(session, letter, "550 4.4.7 QUEUE.Expired; message expired")

        assert await dns_return.return_letters(session, now=NOW) == 1

    async def test_letters_refused_before_the_fix_come_back_too(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        """Вид в письме — от прежней таблицы, судит текст: те письма возвращаются первым проходом."""
        letter = talk.first
        await _refused(session, letter)
        letter.deferred_kind, letter.deferred_reason = "no_mail_server", OUTAGE
        letter.deferred_since = SENT + timedelta(seconds=20)
        await session.flush()

        assert await dns_return.return_letters(session, now=NOW) == 1


class TestOnceAndNotForLong:
    async def test_the_second_refusal_stays(
        self, session: AsyncSession, talk: Conversation, dns: Dns
    ) -> None:
        """DNS отвечает, а платформа отказывает снова: сломанная запись MX, письмо не ходит по кругу."""
        letter = talk.first
        await _refused(session, letter)
        await dns_return.return_letters(session, now=NOW)
        await session.commit()
        retry = NOW + timedelta(hours=1)
        await Sending(session, Postman(), now=retry).send(letter.id)
        await _refused(session, letter)

        again = await dns_return.return_letters(session, now=retry + timedelta(hours=1))

        assert again == 0
        assert letter.status is MessageStatus.BOUNCED

    async def test_three_days_after_the_trouble_began_it_stays(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        letter = talk.first
        await _refused(session, letter)

        late = await dns_return.return_letters(session, now=SENT + dns_return.WINDOW)
        almost = await dns_return.return_letters(
            session, now=SENT + dns_return.WINDOW - timedelta(minutes=1)
        )

        assert (late, almost) == (0, 1)


class TestChain:
    async def test_a_followup_gets_its_date_back_and_goes_again(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        """Строка добивки та же, ящик тот же: заберёт её проход добивок."""
        followup = await _followup(session, talk)
        await _refused(session, followup)
        assert followup.sent_at is not None
        moment = followup.sent_at + timedelta(hours=1)

        returned = await dns_return.return_letters(session, now=moment)

        assert returned == 1
        assert followup.status is MessageStatus.QUEUED
        assert talk.first.next_action_at == moment

        report = await send_due(session, transport=NullTransport(), limit=5, now=moment)

        assert report.sent == 1
        await session.refresh(followup)
        assert followup.status is MessageStatus.SENT
        assert followup.sender_id == talk.sender.id
        steps = await session.scalar(
            select(func.count())
            .select_from(MessageModel)
            .where(MessageModel.thread_id == talk.thread.id, MessageModel.step == 1)
        )
        assert steps == 1

    @pytest.mark.parametrize("status", [ThreadStatus.REPLIED, ThreadStatus.UNSUBSCRIBED])
    async def test_a_chain_that_ended_stays_ended(
        self, session: AsyncSession, talk: Conversation, status: ThreadStatus
    ) -> None:
        """Адресат ответил или отписался, пока письмо лежало отказом: добивке не идти."""
        followup = await _followup(session, talk)
        await _refused(session, followup)
        talk.thread.status = status
        await session.flush()
        assert followup.sent_at is not None

        returned = await dns_return.return_letters(
            session, now=followup.sent_at + timedelta(hours=1)
        )

        assert returned == 0
        assert followup.status is MessageStatus.BOUNCED
        assert talk.first.next_action_at is None

    @pytest.mark.parametrize("step", [ANSWER_STEP, RECHECK_STEP])
    async def test_what_a_human_sent_is_his_to_send_again(
        self, session: AsyncSession, talk: Conversation, step: int
    ) -> None:
        talk.first.step = step
        await _refused(session, talk.first)

        assert await dns_return.return_letters(session, now=NOW) == 0
        assert talk.first.status is MessageStatus.BOUNCED


async def test_the_box_can_work_again_after_the_return(session: AsyncSession) -> None:
    """Сегодняшний случай: ящик на паузе, отказы молчания вернулись в работу, ящик включили —
    следующий настоящий отказ паузы не ставит: в окне только он."""
    box = await _box(session, Stage.SALES, total=20)
    letters = (
        await session.scalars(
            select(MessageModel)
            .where(MessageModel.sender_id == box.id)
            .order_by(MessageModel.sent_at.desc())
        )
    ).all()
    for letter in letters[:3]:
        letter.status, letter.failure_reason = MessageStatus.BOUNCED, OUTAGE
    await session.flush()
    senders.disable(box, "отказов 3 в окне 50 писем — порог 5.0%: пауза", now=NOW)

    assert await dns_return.return_letters(session, now=NOW + timedelta(hours=10)) == 3

    senders.enable(box, now=NOW)
    real = DeliveryEvent(
        "bounce",
        letters[3].id,
        "x@to.example.test",
        reason="550 5.1.1 The email account that you tried to reach does not exist",
        at=NOW,
    )
    report = await apply_events(session, [real], now=NOW)

    assert (report.paused_domains, box.enabled) == ([], True)


async def test_the_whole_way(
    client: AsyncClient,
    session: AsyncSession,
    filled_legal: None,
    dns: Dns,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Весь путь настоящего сбоя: отказ вебхуком → проход вернул письмо → DNS молчит, письмо
    ждёт → DNS ответил, письмо ушло и дошло. Ящик не встал, журнал здоровья пуст."""
    private, public = _keypair()
    monkeypatch.setattr("backend.config.outreach.EVENTS_PUBLIC_KEY", public)
    box = await make_sender(session, "outreach1@mail.example.test")
    letter = await first_letter(session, host=HOST)
    await session.commit()
    postman = Postman()
    await Sending(session, postman, now=SENT).send(letter.id)
    await session.commit()

    batch = [
        _event("deferred", letter.id, "sg-1", response=OUTAGE, attempt="1", email=EDITOR),
        _event("bounce", letter.id, "sg-2", reason=OUTAGE, type="blocked", email=EDITOR),
    ]
    answer = await _deliver(client, private, batch)

    assert answer.status_code == 200, answer.text
    assert (answer.json()["bounced"], answer.json()["paused"]) == (1, [])
    await session.refresh(letter)
    assert letter.status is MessageStatus.BOUNCED

    assert await dns_return.return_letters(session, now=NOW) == 1
    await session.commit()
    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED

    dns.silent.add(HOST)
    with pytest.raises(RecipientDnsError):
        await Sending(session, postman, now=NOW).send(letter.id)
    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED
    assert postman.sent == [EDITOR]

    dns.silent.clear()
    await Sending(session, postman, now=NOW + recipient_dns.RECHECK).send(letter.id)
    delivered = _event("delivered", letter.id, "sg-3", email=EDITOR)
    await _deliver(client, private, [delivered])

    await session.refresh(letter)
    await session.refresh(box)
    assert letter.status is MessageStatus.DELIVERED
    assert postman.sent == [EDITOR, EDITOR]
    assert box.enabled


async def test_two_passes_at_once_return_the_letter_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Два прохода прочли письмо отказом и встретились перед возвратом: возвращает один условный
    `UPDATE`, второй ждёт его фиксации и видит письмо уже в очереди, вторая строка журнала не
    появляется. На настоящих фиксациях: фикстура теста блокировки строки не даёт."""
    async with committed_sessions() as factory:
        async with factory() as session:
            box = await make_sender(session, "race@mail.example.test")
            letter = await first_letter(session, host=HOST)
            letter.status, letter.sender_id = MessageStatus.BOUNCED, box.id
            letter.sent_at, letter.failure_reason = SENT, OUTAGE
            await session.commit()
            number = letter.id
        barrier = asyncio.Barrier(2)
        real = dns_return._return

        async def met(session: AsyncSession, message: MessageModel, now: datetime) -> bool:
            await asyncio.wait_for(barrier.wait(), timeout=MEETING)
            return await real(session, message, now)

        monkeypatch.setattr(dns_return, "_return", met)

        async def one_pass() -> int:
            async with factory() as session:
                returned = await dns_return.return_letters(session, now=NOW)
                await session.commit()
                return returned

        results = await asyncio.gather(one_pass(), one_pass())
        async with factory() as session:
            after = await session.get(MessageModel, number)
            rows = await session.scalar(
                select(func.count())
                .select_from(AuditLogModel)
                .where(AuditLogModel.target == f"message:{number}")
            )

    assert sorted(results) == [0, 1]
    assert after is not None
    assert after.status is MessageStatus.QUEUED
    assert rows == 1


class TestThePass:
    @pytest.fixture(autouse=True)
    def _base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(storage, "DSN", TEST_DSN)
        monkeypatch.setattr(reaper, "job_alive", lambda _job: False)
        monkeypatch.setattr(reaper, "job_failure", lambda _job: None)

    @pytest.fixture
    def clock(self, monkeypatch: pytest.MonkeyPatch) -> list[float]:
        moment = [1000.0]
        monkeypatch.setattr(
            reaper, "DNS_RETURN", reaper.Sparse(reaper.DNS_RETURN_INTERVAL_SEC, lambda: moment[0])
        )
        return moment

    async def test_the_sweep_returns_letters_once_in_a_while(
        self, monkeypatch: pytest.MonkeyPatch, clock: list[float]
    ) -> None:
        calls: list[Any] = []

        async def counted(_session: AsyncSession, *, now: Any) -> int:
            calls.append(now)
            return 0

        monkeypatch.setattr(dns_return, "return_letters", counted)

        async with committed_sessions():
            await reaper.sweep()
            clock[0] += reaper.POLL_INTERVAL_SEC
            await reaper.sweep()
            clock[0] += reaper.DNS_RETURN_INTERVAL_SEC
            await reaper.sweep()

        assert len(calls) == 2

    async def test_a_falling_return_does_not_fail_the_sweep(
        self,
        monkeypatch: pytest.MonkeyPatch,
        clock: list[float],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        async def falling(_session: AsyncSession, *, now: Any) -> int:
            raise RuntimeError("база не ответила")

        monkeypatch.setattr(dns_return, "return_letters", falling)

        async with committed_sessions():
            await reaper.sweep()

        assert "не вернулись" in caplog.text

    async def test_the_pass_commits(self, clock: list[float]) -> None:
        async with committed_sessions() as factory:
            async with factory() as session:
                box = await make_sender(session, "commit@mail.example.test")
                letter = await first_letter(session, host=HOST)
                letter.status, letter.sender_id = MessageStatus.BOUNCED, box.id
                letter.sent_at = datetime.now(UTC) - timedelta(hours=1)
                letter.failure_reason = OUTAGE
                await session.commit()
                number = letter.id

            await reaper.sweep()

            async with factory() as session:
                after = await session.get(MessageModel, number)

        assert after is not None
        assert after.status is MessageStatus.QUEUED
