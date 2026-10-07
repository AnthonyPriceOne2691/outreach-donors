"""Письмо, возвращённое в очередь, а на деле ушедшее, второй раз не уходит.

Два пути к повторному письму (07.10.2026):

- человек не нашёл письмо в журнале платформы и вернул его в очередь
  («Вернуть в очередь»), а оно ушло. Событие платформы о приёме — по нашему
  номеру письма в её `custom_args` — записывает его ушедшим так же, как из
  «отправляется», и снимает с очереди: пачка его второй раз не шлёт;
- платформа приняла письмо (202), но не вернула своего номера
  (`test_letters_accepted_without_number.py`).

Гонки события с пачкой — на настоящих фиксациях, в `test_letters_requeued_race.py`.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import AuditAction, MessageStatus, Stage
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import answers, batch, unknown_outcome
from backend.features.letters.answers import AnswerRefusedError
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.letters.followups import send_due
from backend.features.letters.repository import LetterRepository
from backend.features.letters.sending import Sending
from backend.features.letters.unknown_outcome import Outcome, ResolveError
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_delivery_events import _keypair, _sign
from tests.thread_letters import (
    BOX,
    Conversation,
    Recording,
    Refusing,
    Unanswered,
    age,
    answer,
    conversation,
    human_reply,
    stuck_first,
    stuck_followup,
)

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
SINCE = NOW - timedelta(minutes=12)
#: Когда платформа приняла письмо — по её событию: раньше, чем человек его вернул.
AT = NOW - timedelta(minutes=11)
TO = "editor@stuck.example.test"


async def _requeued(session: AsyncSession) -> MessageModel:
    """Письмо, на котором оборвалась связь, — человек вернул его в очередь."""
    letter = await stuck_first(session, since=SINCE)
    await unknown_outcome.resolve(session, letter.id, Outcome.QUEUED, author_id=None, now=NOW)
    await session.commit()
    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED
    return letter


async def _sent_records(session: AsyncSession, letter_id: int) -> list[dict[str, object]]:
    rows = await session.execute(
        select(AuditLogModel.details).where(
            AuditLogModel.action == AuditAction.LETTER_SENT,
            AuditLogModel.target == f"message:{letter_id}",
        )
    )
    return [details or {} for details in rows.scalars().all()]


async def _batch_sends_nothing(session: AsyncSession) -> None:
    transport = Recording()
    report = await batch.send_queue(session, transport, stage=Stage.DONORS)
    assert (transport.seen, report.sent, report.left) == ([], 0, 0)


class TestEventAfterRequeue:
    async def test_first_letter_is_recorded_sent_and_leaves_the_queue(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        letter = await _requeued(session)
        attempt = (letter.sender_id, letter.internet_message_id)
        assert [r.message.id for r in await LetterRepository(session).queued()] == [letter.id]

        report = await apply_events(
            session, [DeliveryEvent("processed", letter.id, TO, at=AT)], now=NOW
        )
        await session.commit()

        assert report.resolved == 1
        await session.refresh(letter)
        assert letter.status is MessageStatus.SENT
        # Ушло, когда приняла платформа: начало передачи затёрто возвратом.
        assert letter.sent_at == AT
        assert letter.next_action_at == AT + timedelta(days=3)
        # Ящик и Message-ID — той попытки, что ушла: с них идут добивка и ветка.
        assert (letter.sender_id, letter.internet_message_id) == attempt
        contact = await session.get(ContactModel, letter.contact_id)
        assert contact is not None
        assert contact.last_contacted_at == AT
        [record] = await _sent_records(session, letter.id)
        assert record["как узнали"] == "событие платформы «processed» после возврата в очередь"
        assert record["от кого"] == BOX
        assert await LetterRepository(session).queued() == []
        await _batch_sends_nothing(session)

    async def test_repeat_refused_by_the_platform_keeps_the_trace_of_the_attempt(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Повтор отказан платформой — но прежняя попытка могла уйти: её ящик
        и Message-ID возвращаются в строку, а не пустеют, и позднее событие
        записывает письмо ушедшим с ними."""
        letter = await _requeued(session)
        attempt = (letter.sender_id, letter.internet_message_id)

        refused = await batch.send_queue(session, Refusing(), stage=Stage.DONORS)
        await session.refresh(letter)

        assert refused.refused == {"почта отказала": 1}
        assert letter.status is MessageStatus.QUEUED
        assert (letter.sender_id, letter.internet_message_id) == attempt

        await apply_events(session, [DeliveryEvent("delivered", letter.id, TO, at=AT)], now=NOW)

        assert letter.status is MessageStatus.DELIVERED
        assert (letter.sender_id, letter.internet_message_id) == attempt

    async def test_dropped_takes_it_off_the_queue_as_not_delivered(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Платформа приняла и не доставила: письмо не ждёт повтора на мёртвый
        адрес, а становится недошедшим — и открывает следующий адрес донора."""
        letter = await _requeued(session)

        report = await apply_events(
            session,
            [DeliveryEvent("dropped", letter.id, TO, reason="Bounced Address", at=AT)],
            now=NOW,
        )

        assert (report.resolved, report.bounced) == (1, 1)
        assert letter.status is MessageStatus.BOUNCED
        assert letter.sent_at == AT
        await _batch_sends_nothing(session)

    async def test_followup_stops_waiting_for_its_repeat(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Добивка, возвращённая в цепочку, ушла: срок повтора на первом письме
        гасится, и проход добивок за ней больше не приходит. Следующий шаг
        считается уже от неё."""
        talk: Conversation = await conversation(session, sent_at=NOW - timedelta(days=4))
        followup = await stuck_followup(session, talk, since=SINCE)
        await unknown_outcome.resolve(session, followup.id, Outcome.QUEUED, author_id=None, now=NOW)
        await session.commit()
        await session.refresh(talk.first)
        assert talk.first.next_action_at == NOW

        await apply_events(session, [DeliveryEvent("processed", followup.id, TO, at=AT)], now=NOW)
        await session.commit()
        await session.refresh(talk.first)
        assert talk.first.next_action_at is None  # срок повтора погашен самим событием
        transport = Recording()
        report = await send_due(session, transport=transport, limit=5, now=NOW + timedelta(hours=2))

        assert (report.sent, report.postponed) == (0, 0)
        assert transport.seen == []
        await session.refresh(talk.first)
        await session.refresh(followup)
        assert talk.first.next_action_at is None
        assert followup.status is MessageStatus.SENT
        assert followup.next_action_at == AT + timedelta(days=7)

    async def test_a_pass_that_already_took_the_step_does_not_bring_it_back(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Проход забрал срок раньше, чем событие записало добивку: он находит её
        ушедшей и срок не возвращает — иначе приходил бы за ней раз в час."""
        talk = await conversation(session, sent_at=NOW - timedelta(days=4))
        followup = await stuck_followup(session, talk, since=SINCE)
        await unknown_outcome.resolve(session, followup.id, Outcome.QUEUED, author_id=None, now=NOW)
        await apply_events(session, [DeliveryEvent("processed", followup.id, TO, at=AT)], now=NOW)
        # Как будто проход прочёл срок до того, как событие его погасило.
        await session.execute(
            update(MessageModel).where(MessageModel.id == talk.first.id).values(next_action_at=NOW)
        )
        await session.commit()
        transport = Recording()

        first = await send_due(session, transport=transport, limit=5, now=NOW)
        second = await send_due(session, transport=transport, limit=5, now=NOW + timedelta(hours=2))

        assert (first.sent, first.postponed, second.sent, second.postponed) == (0, 0, 0, 0)
        assert transport.seen == []
        await session.refresh(talk.first)
        assert talk.first.next_action_at is None

    async def test_answer_is_not_answered_twice(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        talk = await conversation(session, sent_at=NOW - timedelta(days=4))
        reply = await human_reply(session, talk)
        answered = await answer(session, talk, reply, transport=Unanswered())
        await age(session, answered, since=SINCE)
        await unknown_outcome.resolve(session, answered.id, Outcome.QUEUED, author_id=None, now=NOW)

        await apply_events(session, [DeliveryEvent("processed", answered.id, TO, at=AT)], now=NOW)
        await session.commit()

        assert answered.status is MessageStatus.SENT
        with pytest.raises(AnswerRefusedError, match="уже ответили"):
            await answers.answer_reply(
                session,
                Sending(session, Recording()),
                thread_id=talk.thread.id,
                reply_id=reply.id,
                body="Thanks!",
                author_id=None,
            )


class TestEventBeforeRequeue:
    async def test_the_human_is_refused_and_nothing_goes_twice(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Событие пришло раньше решения: письмо уже «принято платформой», вернуть
        его в очередь нельзя, и пачке слать нечего."""
        letter = await stuck_first(session, since=SINCE)
        await apply_events(session, [DeliveryEvent("processed", letter.id, TO, at=AT)], now=NOW)
        await session.commit()

        with pytest.raises(ResolveError, match="уже не «отправляется», а «принято платформой»"):
            await unknown_outcome.resolve(
                session, letter.id, Outcome.QUEUED, author_id=None, now=NOW
            )

        assert letter.status is MessageStatus.SENT
        assert letter.sent_at == SINCE  # из «отправляется» — от начала передачи
        assert len(await _sent_records(session, letter.id)) == 1
        await _batch_sends_nothing(session)


class TestTheRoute:
    async def test_event_time_comes_from_the_platform(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Ручка берёт время события из её `timestamp`: оно и есть время ухода
        письма, вернувшегося в очередь."""
        letter = await _requeued(session)
        private, public = _keypair()
        monkeypatch.setattr("backend.config.outreach.EVENTS_PUBLIC_KEY", public)
        payload = json.dumps(
            [
                {
                    "event": "processed",
                    "message_id": str(letter.id),
                    "email": TO,
                    "timestamp": int(AT.timestamp()),
                }
            ]
        ).encode()
        stamp = str(int(time.time()))

        response = await client.post(
            "/api/events/delivery",
            content=payload,
            headers={
                "X-Twilio-Email-Event-Webhook-Signature": _sign(private, payload, stamp),
                "X-Twilio-Email-Event-Webhook-Timestamp": stamp,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200, response.text
        await session.refresh(letter)
        assert letter.status is MessageStatus.SENT
        assert letter.sent_at == AT
