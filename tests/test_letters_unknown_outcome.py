"""Исход неизвестен: письмо «отправляется», а ушло ли оно — неизвестно.

Связь с почтой оборвалась посреди передачи (`Sending._hand_over`): платформа
могла принять письмо, а могла и нет, ключа идемпотентности у неё нет, и повтор
вслепую — второе письмо тому же человеку. Проверяется то, чем из этого
состояния выходят (`letters/unknown_outcome.py`):

- событие платформы записывает письмо ушедшим — один раз и тем же путём, что
  обычная отправка: время ухода, срок добивки, расход, журнал;
- человек решает по журналу платформы: «ушло» или «вернуть в очередь»;
  добивка возвращается в свою цепочку — с ящика переписки и в её ветку;
- решать раньше пяти минут и решать уже решённое нельзя — отказ словами.

Гонки двух путей — на настоящих фиксациях, в `test_letters_unknown_race.py`.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from backend.features.core.domain import (
    AuditAction,
    MessageStatus,
    Stage,
    ThreadStatus,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel, SenderModel, ThreadModel
from backend.features.letters import unknown_outcome
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.letters.followups import send_due
from backend.features.letters.repository import LetterRepository, UnknownLetterError
from backend.features.letters.sending import Sending
from backend.features.letters.transport import NullTransport
from backend.features.letters.unknown_outcome import Outcome, ResolveError
from httpx import AsyncClient
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.thread_letters import (
    BOX,
    Conversation,
    Recording,
    Unanswered,
    age,
    answer,
    conversation,
    first_letter,
    human_reply,
    stuck_first,
    stuck_followup,
)

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
#: Когда письмо отдали почте: дольше пяти минут назад — исход решает человек.
SINCE = NOW - timedelta(minutes=12)
#: Когда ушло первое письмо переписки: срок первой добивки (3 дня) уже прошёл.
FIRST_SENT = NOW - timedelta(days=4)

ROOT = Path(__file__).resolve().parents[1]
FRONT_TYPES = ROOT / "frontend" / "src" / "api" / "unknownOutcome.ts"


async def _count(session: AsyncSession, statement: object) -> int:
    return int(await session.scalar(statement) or 0)  # type: ignore[call-overload]


async def _sent_records(session: AsyncSession, letter_id: int) -> list[AuditLogModel]:
    """Записи «ушло» об одном письме: их должно быть не больше одной."""
    rows = await session.execute(
        select(AuditLogModel).where(
            AuditLogModel.action == AuditAction.LETTER_SENT,
            AuditLogModel.target == f"message:{letter_id}",
        )
    )
    return list(rows.scalars().all())


def _spent() -> object:
    return (
        select(func.count())
        .select_from(UsageRecordModel)
        .where(UsageRecordModel.operation == "letter_send")
    )


async def _talk(session: AsyncSession) -> Conversation:
    return await conversation(session, sent_at=FIRST_SENT)


class TestTheEventDecides:
    """Событие платформы по письму — доказательство, что она его приняла."""

    async def test_processed_records_the_letter_sent_once(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letter = await stuck_first(session, since=SINCE)
        event = DeliveryEvent("processed", letter.id, "editor@stuck.example.test")

        first = await apply_events(session, [event], now=NOW)
        await session.commit()
        repeat = await apply_events(session, [event], now=NOW)
        await session.commit()

        assert (first.resolved, repeat.resolved) == (1, 0)
        await session.refresh(letter)
        assert letter.status is MessageStatus.SENT
        # Ушло, когда его отдали почте, а не когда пришло событие: от этого
        # времени считаются срок добивки и дневной лимит ящика.
        assert letter.sent_at == SINCE
        assert letter.next_action_at == SINCE + timedelta(days=3)
        contact = await session.get(ContactModel, letter.contact_id)
        assert contact is not None
        assert contact.last_contacted_at == SINCE
        [record] = await _sent_records(session, letter.id)
        assert record.details is not None
        assert record.details["как узнали"] == "событие платформы «processed»"
        assert record.details["от кого"] == BOX
        assert await _count(session, _spent()) == 1

    async def test_delivered_is_sent_first_and_delivered_then(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letter = await stuck_first(session, since=SINCE)

        report = await apply_events(
            session, [DeliveryEvent("delivered", letter.id, "editor@stuck.example.test")], now=NOW
        )

        assert (report.resolved, report.delivered) == (1, 1)
        assert letter.status is MessageStatus.DELIVERED
        assert letter.sent_at == SINCE
        assert letter.delivered_at == NOW

    async def test_bounce_counts_against_its_box(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Не дошедшее письмо — тоже ушедшее: без времени ухода оно не вошло бы
        в долю отказов ящика, по которой ящик паркуется."""
        letter = await stuck_first(session, since=SINCE)

        report = await apply_events(
            session,
            [DeliveryEvent("bounce", letter.id, "editor@stuck.example.test", reason="550 no user")],
            now=NOW,
        )

        assert (report.resolved, report.bounced) == (1, 1)
        assert letter.status is MessageStatus.BOUNCED
        assert letter.sent_at == SINCE
        assert letter.next_action_at is None
        assert letter.failure_reason == "550 no user"

    async def test_followup_settled_by_event_plans_the_last_step(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        followup = await stuck_followup(session, await _talk(session), since=SINCE)

        await apply_events(
            session, [DeliveryEvent("delivered", followup.id, "editor@stuck.example.test")], now=NOW
        )

        assert followup.status is MessageStatus.DELIVERED
        assert followup.next_action_at == SINCE + timedelta(days=7)

    async def test_unknown_event_proves_nothing(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Незнакомое событие платформа могла завести для чего угодно — исход
        по нему не решается."""
        letter = await stuck_first(session, since=SINCE)

        report = await apply_events(
            session,
            [DeliveryEvent("account_review", letter.id, "editor@stuck.example.test")],
            now=NOW,
        )

        assert report.resolved == 0
        assert letter.status is MessageStatus.SENDING
        assert await _sent_records(session, letter.id) == []


class TestStuckList:
    async def test_stuck_letters_of_the_stage_oldest_first(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        talk = await _talk(session)
        followup = await stuck_followup(session, talk, since=NOW - timedelta(minutes=40))
        reply = await human_reply(session, talk)
        answered = await answer(session, talk, reply, transport=Unanswered())
        await age(session, answered, since=NOW - timedelta(minutes=20))
        first = await stuck_first(session, since=SINCE, host="first.example.test")
        fresh = await stuck_first(
            session, since=NOW - timedelta(minutes=2), host="fresh.example.test"
        )
        offer = await _sending_offer(session, since=NOW - timedelta(hours=1))

        donors = await unknown_outcome.stuck(session, stage=Stage.DONORS, now=NOW)
        offers = await unknown_outcome.stuck(session, stage=Stage.ADVERTISERS, now=NOW)

        assert [(row.message.id, row.what) for row in donors] == [
            (followup.id, "добивка 1"),
            (answered.id, "ответ в переписке"),
            (first.id, "первое письмо"),
        ]
        assert fresh.id not in {row.message.id for row in donors}
        assert [row.message.id for row in offers] == [offer.id]
        assert {row.sender_email for row in donors} == {BOX}
        assert donors[1].email == "boss@stuck.example.test"  # ответ — тому, кто ответил


class TestHumanDecides:
    async def test_sent_is_recorded_from_the_start_of_handover(
        self, session: AsyncSession, filled_legal: None, make_user: MakeUser
    ) -> None:
        letter = await stuck_first(session, since=SINCE)
        author = await make_user("решает@example.test", role=UserRole.ADMIN)

        done = await unknown_outcome.resolve(
            session, letter.id, Outcome.SENT, author_id=author.id, now=NOW
        )
        await session.commit()

        assert done.status is MessageStatus.SENT
        assert "отмечено ушедшим" in done.said
        await session.refresh(letter)
        assert letter.sent_at == SINCE
        assert letter.next_action_at == SINCE + timedelta(days=3)
        [record] = await _sent_records(session, letter.id)
        assert record.user_id == author.id
        assert record.details is not None
        assert record.details["как узнали"] == "человек нашёл письмо в журнале платформы"
        assert await _count(session, _spent()) == 1

    async def test_first_letter_goes_back_to_the_queue(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letter = await stuck_first(session, since=SINCE)
        attempt = (letter.sender_id, letter.internet_message_id)

        done = await unknown_outcome.resolve(
            session, letter.id, Outcome.QUEUED, author_id=None, now=NOW
        )
        await session.commit()

        assert done.status is MessageStatus.QUEUED
        await session.refresh(letter)
        # Ящик и идентификатор попытки остаются: если письмо всё же ушло,
        # событие платформы запишет его ушедшим с ними. Повтор возьмёт свои.
        assert attempt[0] is not None
        assert (letter.sender_id, letter.internet_message_id) == attempt
        assert letter.sent_at is None
        queued = await LetterRepository(session).queued(stage=Stage.DONORS)
        assert [row.message.id for row in queued] == [letter.id]
        record = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.USER_UPDATED)
        )
        assert record is not None
        assert record.details is not None
        assert (record.details["письмо"], record.details["стало"]) == ("первое письмо", "в очереди")
        assert await _sent_records(session, letter.id) == []
        assert await _count(session, _spent()) == 0

        await Sending(session, NullTransport(), now=NOW).send(letter.id)
        await session.refresh(letter)
        assert letter.status is MessageStatus.SENT
        assert letter.internet_message_id != attempt[1]

    async def test_followup_goes_back_into_its_chain(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Из очереди экрана добивка ушла бы с любого ящика и без ветки — назад
        она возвращается в цепочку, и её забирает проход добивок."""
        talk = await _talk(session)
        followup = await stuck_followup(session, talk, since=SINCE)

        done = await unknown_outcome.resolve(
            session, followup.id, Outcome.QUEUED, author_id=None, now=NOW
        )
        await session.commit()
        transport = Recording()
        report = await send_due(session, transport=transport, limit=5, now=NOW)

        assert "вернулась в цепочку" in done.said
        assert report.sent == 1
        [out] = transport.seen
        assert out.message_id == followup.id
        assert out.from_email == BOX
        assert out.in_reply_to == talk.first.internet_message_id
        await session.refresh(followup)
        assert followup.status is MessageStatus.SENT
        assert followup.sender_id == talk.sender.id

    async def test_followup_of_a_finished_chain_is_stopped(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Пока письмо висело, донор ответил: добивка ему не уходит вовсе."""
        talk = await _talk(session)
        followup = await stuck_followup(session, talk, since=SINCE)
        await session.execute(
            update(ThreadModel)
            .where(ThreadModel.id == talk.thread.id)
            .values(status=ThreadStatus.REPLIED)
        )
        await session.commit()

        done = await unknown_outcome.resolve(
            session, followup.id, Outcome.QUEUED, author_id=None, now=NOW
        )
        await session.commit()
        transport = Recording()
        await send_due(session, transport=transport, limit=5, now=NOW + timedelta(days=30))

        assert done.status is MessageStatus.STOPPED
        assert "добивка не пойдёт" in done.said
        assert transport.seen == []

    async def test_answer_waits_for_the_thread_card(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        talk = await _talk(session)
        reply = await human_reply(session, talk)
        answered = await answer(session, talk, reply, transport=Unanswered())
        await age(session, answered, since=SINCE)

        done = await unknown_outcome.resolve(
            session, answered.id, Outcome.QUEUED, author_id=None, now=NOW
        )

        assert done.status is MessageStatus.QUEUED
        assert "из карточки переписки" in done.said


class TestWhatCannotBeDecided:
    async def test_too_early_is_refused_in_words(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Письмо может быть ещё в пути: «вернуть в очередь» рядом с идущей
        передачей отправило бы его второй раз."""
        letter = await stuck_first(session, since=NOW - timedelta(minutes=2))

        with pytest.raises(ResolveError, match="меньше 5 минут назад"):
            await unknown_outcome.resolve(
                session, letter.id, Outcome.QUEUED, author_id=None, now=NOW
            )
        await session.refresh(letter)
        assert letter.status is MessageStatus.SENDING

    async def test_decided_letter_is_refused(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letter = await stuck_first(session, since=SINCE)
        await unknown_outcome.resolve(session, letter.id, Outcome.SENT, author_id=None, now=NOW)
        await session.commit()

        with pytest.raises(ResolveError, match="уже не «отправляется», а «принято платформой»"):
            await unknown_outcome.resolve(
                session, letter.id, Outcome.QUEUED, author_id=None, now=NOW
            )
        assert len(await _sent_records(session, letter.id)) == 1

    async def test_unknown_letter(self, session: AsyncSession) -> None:
        with pytest.raises(UnknownLetterError, match="№999999"):
            await unknown_outcome.resolve(session, 999_999, Outcome.SENT, author_id=None, now=NOW)


class TestTheScreen:
    """Блок «Исход неизвестен» на экране писем — по HTTP. Время здесь настоящее:
    маршрут решает по часам сервера."""

    @pytest.fixture
    async def admin(self, make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
        await make_user("решает@example.test", role=UserRole.ADMIN)
        return bearer(await sign_in("решает@example.test"))

    async def test_list_names_what_to_look_for_in_the_journal(
        self, client: AsyncClient, session: AsyncSession, filled_legal: None, admin: dict[str, str]
    ) -> None:
        since = datetime.now(UTC) - timedelta(minutes=12)
        letter = await stuck_first(session, since=since)
        await stuck_first(session, since=datetime.now(UTC), host="fresh.example.test")

        response = await client.get("/api/letters/unknown", headers=admin)

        assert response.status_code == 200, response.text
        view = response.json()
        assert view["stage"] == "donors"
        assert view["after_minutes"] == 5
        [card] = view["letters"]
        assert card["id"] == letter.id
        assert card["host"] == "stuck.example.test"
        assert card["email"] == "editor@stuck.example.test"
        assert card["sender_email"] == BOX
        assert card["what"] == "первое письмо"
        assert card["thread_id"] == letter.thread_id
        assert datetime.fromisoformat(card["since"]) == since

    async def test_list_is_by_stage(
        self, client: AsyncClient, session: AsyncSession, filled_legal: None, admin: dict[str, str]
    ) -> None:
        offer = await _sending_offer(session, since=datetime.now(UTC) - timedelta(minutes=30))

        donors = await client.get("/api/letters/unknown", headers=admin)
        offers = await client.get("/api/letters/unknown?stage=advertisers", headers=admin)

        assert donors.json()["letters"] == []
        assert [card["id"] for card in offers.json()["letters"]] == [offer.id]

    @pytest.mark.parametrize(("outcome", "status"), [("sent", "sent"), ("queued", "queued")])
    async def test_resolve_says_what_became_of_the_letter(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        admin: dict[str, str],
        outcome: str,
        status: str,
    ) -> None:
        letter = await stuck_first(session, since=datetime.now(UTC) - timedelta(minutes=12))

        response = await client.post(
            f"/api/letters/{letter.id}/resolve", json={"outcome": outcome}, headers=admin
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["id"], body["status"]) == (letter.id, status)
        assert body["said"]
        await session.refresh(letter)
        assert letter.status.value == status

    async def test_refusals_are_409_in_words(
        self, client: AsyncClient, session: AsyncSession, filled_legal: None, admin: dict[str, str]
    ) -> None:
        fresh = await stuck_first(session, since=datetime.now(UTC) - timedelta(minutes=1))
        old = await stuck_first(
            session, since=datetime.now(UTC) - timedelta(minutes=12), host="old.example.test"
        )
        await client.post(f"/api/letters/{old.id}/resolve", json={"outcome": "sent"}, headers=admin)

        early = await client.post(
            f"/api/letters/{fresh.id}/resolve", json={"outcome": "queued"}, headers=admin
        )
        again = await client.post(
            f"/api/letters/{old.id}/resolve", json={"outcome": "queued"}, headers=admin
        )

        assert early.status_code == 409
        assert "может быть ещё в пути" in early.json()["detail"]
        assert again.status_code == 409
        assert "уже не «отправляется»" in again.json()["detail"]

    async def test_unknown_letter_is_404_and_unknown_outcome_422(
        self, client: AsyncClient, admin: dict[str, str]
    ) -> None:
        missing = await client.post(
            "/api/letters/999999/resolve", json={"outcome": "sent"}, headers=admin
        )
        wrong = await client.post(
            "/api/letters/999999/resolve", json={"outcome": "maybe"}, headers=admin
        )

        assert missing.status_code == 404
        assert wrong.status_code == 422

    def test_front_knows_exactly_the_server_outcomes(self) -> None:
        """Решение экрана — то же значение, что принимает сервер: тип врал бы,
        а кнопка отправляла бы то, что сервер отвергает словом 422."""
        source = FRONT_TYPES.read_text(encoding="utf-8")
        found = re.search(r"export type ResolveOutcome =([^;]+);", source)
        assert found is not None, f"в {FRONT_TYPES.name} нет типа ResolveOutcome"
        assert set(re.findall(r"'([a-z_]+)'", found.group(1))) == {o.value for o in Outcome}


async def _sending_offer(session: AsyncSession, *, since: datetime) -> MessageModel:
    """Оффер рекламодателю, застрявший в «отправляется»: свой этап — свой список."""
    letter = await first_letter(session, host="brand.example.test", stage=Stage.ADVERTISERS)
    box = await session.scalar(select(SenderModel.id).where(SenderModel.email == BOX))
    await session.execute(
        update(MessageModel)
        .where(MessageModel.id == letter.id)
        .values(status=MessageStatus.SENDING, sender_id=box, updated_at=since)
    )
    await session.commit()
    await session.refresh(letter)
    return letter
