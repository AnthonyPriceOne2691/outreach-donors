"""Добивка и ответ уходят только своим путём и только с ящика своей переписки.

Находка ревью (07.10.2026): добивка и ответ, которые отказ почты вернул
«в очередь», попадали в общую очередь этапа — их было видно в списке «Письма»,
их брала пачка «Отправить очередь», и уходили они с любого свободного ящика:
первым письмом вне своей переписки, с чужого домена и без ветки. Проверяется
каждый путь (`letters/mailbox.py`): возврат после отказа, пачка и экран их
не берут, свой путь берёт со своего ящика и в свою ветку, ящик переписки
не пишет — письмо ждёт его с причиной словами, а не уходит с другого.

Второй ящик в каждом тесте свободнее ящика переписки: письмо, ушедшее не своим
путём, ушло бы с него — и это было бы видно.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import MessageStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import MessageModel, ReplyModel, SenderModel
from backend.features.letters import answers, batch
from backend.features.letters.answers import AnswerRefusedError
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.followups import send_due
from backend.features.letters.repository import LetterRepository
from backend.features.letters.sending import NoSenderError, OwnPathError, Sending
from backend.features.outreach import senders as sender_rules
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_sender
from tests.thread_letters import (
    ANSWERED_FROM,
    BOX,
    INBOUND_ID,
    Conversation,
    Recording,
    Refusing,
    answer,
    conversation,
    first_letter,
    human_reply,
)

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
#: Первое письмо переписки ушло четыре дня назад — срок первой добивки (3 дня) прошёл.
FIRST_SENT = NOW - timedelta(days=4)
DUE = FIRST_SENT + timedelta(days=3)
#: Второй ящик того же этапа — свободнее ящика переписки.
SPARE = "outreach2@mail.example.test"
PARKED = "доля отказов 12% — парковка"


@pytest.fixture
async def talk(session: AsyncSession, filled_legal: None) -> Conversation:
    """Переписка с ящиком `BOX` и рядом свободный ящик с бо́льшим остатком на сегодня."""
    started = await conversation(session, sent_at=FIRST_SENT)
    await make_sender(session, SPARE, cap=50)
    await session.commit()
    return started


async def _followup(session: AsyncSession, talk: Conversation) -> MessageModel | None:
    found: MessageModel | None = await session.scalar(
        select(MessageModel).where(MessageModel.thread_id == talk.thread.id, MessageModel.step == 1)
    )
    return found


async def _refused_followup(session: AsyncSession, talk: Conversation) -> MessageModel:
    """Проход добивок отдал добивку почте, и почта отказала: добивка вернулась назад."""
    report = await send_due(session, transport=Refusing(), limit=1, now=DUE)
    assert report.postponed == 1, report.as_report
    followup = await _followup(session, talk)
    assert followup is not None
    assert (followup.status, followup.sender_id) == (MessageStatus.QUEUED, None)
    return followup


async def _refused_answer(
    session: AsyncSession, talk: Conversation
) -> tuple[MessageModel, ReplyModel]:
    reply = await human_reply(session, talk)
    answered = await answer(session, talk, reply, transport=Refusing())
    assert (answered.status, answered.sender_id) == (MessageStatus.QUEUED, None)
    return answered, reply


async def _box(session: AsyncSession, email: str) -> SenderModel:
    sender = await session.scalar(select(SenderModel).where(SenderModel.email == email))
    assert sender is not None
    return sender


class TestTheGeneralQueueHasOnlyFirstLetters:
    async def test_refused_followup_and_answer_are_not_in_the_queue_nor_in_the_batch(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        followup = await _refused_followup(session, talk)
        answered, _ = await _refused_answer(session, talk)
        newcomer = await first_letter(session, host="newcomer.example.test")
        await session.commit()

        queued = await LetterRepository(session).queued(stage=Stage.DONORS)
        transport = Recording()
        report = await batch.send_queue(session, transport, stage=Stage.DONORS)

        assert [row.message.id for row in queued] == [newcomer.id]
        # Пачка взяла только первое письмо — и с того ящика, что свободнее:
        # у первого письма ящика переписки ещё нет.
        assert [(out.message_id, out.from_email) for out in transport.seen] == [
            (newcomer.id, SPARE)
        ]
        assert (report.sent, report.left) == (1, 0)
        for letter in (followup, answered):
            await session.refresh(letter)
            assert letter.status is MessageStatus.QUEUED

    async def test_the_screen_lists_first_letters_and_the_batch_has_nothing_to_send(
        self,
        client: AsyncClient,
        session: AsyncSession,
        talk: Conversation,
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        followup = await _refused_followup(session, talk)
        await make_user("решает@example.test", role=UserRole.ADMIN)
        headers = bearer(await sign_in("решает@example.test"))

        listed = await client.get("/api/letters", headers=headers)
        batch_refused = await client.post(
            "/api/letters/send-queue", json={"stage": "donors"}, headers=headers
        )
        one = await client.post(f"/api/letters/{followup.id}/send", headers=headers)

        assert listed.json()["letters"] == []
        assert batch_refused.status_code == 409
        assert "писем нет" in batch_refused.text
        assert one.status_code == 409
        assert "уходит только своим путём, проходом добивок" in one.json()["detail"]


class TestTheQueuePathRefusesThreadLetters:
    async def test_followup_is_not_sent_from_the_queue(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        followup = await _refused_followup(session, talk)
        transport = Recording()

        with pytest.raises(OwnPathError, match="добивка 1: оно уходит только своим путём"):
            await Sending(session, transport).send(followup.id)

        assert transport.seen == []
        await session.refresh(followup)
        assert followup.status is MessageStatus.QUEUED

    async def test_answer_is_not_sent_from_the_queue(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        answered, _ = await _refused_answer(session, talk)
        transport = Recording()

        with pytest.raises(OwnPathError, match="из карточки переписки"):
            await Sending(session, transport).send(answered.id)

        assert transport.seen == []


class TestTheOwnPathUsesItsBox:
    async def test_next_followup_pass_sends_it_from_its_box_into_its_thread(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        followup = await _refused_followup(session, talk)
        transport = Recording()

        report = await send_due(session, transport=transport, limit=5, now=DUE + timedelta(hours=1))

        assert report.sent == 1
        [out] = transport.seen
        assert (out.message_id, out.from_email) == (followup.id, BOX)
        assert out.in_reply_to == talk.first.internet_message_id
        await session.refresh(followup)
        assert followup.status is MessageStatus.SENT
        assert followup.sender_id == talk.sender.id

    async def test_answer_goes_again_from_the_card_from_its_box_to_who_answered(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        answered, reply = await _refused_answer(session, talk)
        transport = Recording()

        outcome = await answers.answer_reply(
            session,
            Sending(session, transport),
            thread_id=talk.thread.id,
            reply_id=reply.id,
            body="Thanks! A guide on home repair, 1500 words.",
            author_id=None,
        )

        assert outcome.message_id == answered.id  # та же строка ответа, а не вторая
        [out] = transport.seen
        assert (out.from_email, out.to, out.in_reply_to) == (BOX, ANSWERED_FROM, INBOUND_ID)


class TestWhenTheThreadBoxCannotWrite:
    async def test_followup_waits_for_its_parked_box_and_says_why(
        self, session: AsyncSession, talk: Conversation, caplog: pytest.LogCaptureFixture
    ) -> None:
        box = await _box(session, BOX)
        sender_rules.disable(box, PARKED, now=NOW)
        await session.commit()
        transport = Recording()

        with caplog.at_level(logging.WARNING, logger="backend.features.letters.followups"):
            report = await send_due(session, transport=transport, limit=5, now=DUE)

        # Второй ящик свободен — а добивка ждёт свой.
        assert (report.sent, report.postponed) == (0, 1)
        assert transport.seen == []
        assert f"Ящик {BOX} сейчас не пишет (выключен: {PARKED})" in caplog.text
        assert "С другого ящика письмо не уйдёт — оно ждёт" in caplog.text
        followup = await _followup(session, talk)
        assert followup is not None
        assert followup.status is MessageStatus.QUEUED
        await session.refresh(talk.first)
        assert talk.first.next_action_at == DUE + timedelta(hours=1)

        sender_rules.enable(box, now=NOW)
        await session.commit()
        again = await send_due(session, transport=transport, limit=5, now=DUE + timedelta(hours=1))

        assert again.sent == 1
        assert [out.from_email for out in transport.seen] == [BOX]

    async def test_answer_to_a_paused_box_is_refused_with_the_reason(
        self, session: AsyncSession, talk: Conversation
    ) -> None:
        reply = await human_reply(session, talk)
        box = await _box(session, BOX)
        sender_rules.disable(box, PARKED, now=NOW)
        await session.commit()
        transport = Recording()

        with pytest.raises(NoSenderError, match=f"сейчас не пишет \\(выключен: {PARKED}\\)"):
            await answers.answer_reply(
                session,
                Sending(session, transport),
                thread_id=talk.thread.id,
                reply_id=reply.id,
                body="Thanks!",
                author_id=None,
            )

        assert transport.seen == []
        assert await LetterRepository(session).queued(stage=Stage.DONORS) == []

    async def test_deleted_box_the_followup_waits_and_the_answer_is_refused(
        self, session: AsyncSession, talk: Conversation, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Удалённый ящик уносит свой номер из писем переписки: её ящик не известен,
        и ни добивка, ни ответ не уходят с другого."""
        reply = await human_reply(session, talk)
        await session.delete(await _box(session, BOX))
        await session.commit()
        transport = Recording()

        with caplog.at_level(logging.WARNING, logger="backend.features.letters.followups"):
            report = await send_due(session, transport=transport, limit=5, now=DUE)
        with pytest.raises(AnswerRefusedError, match="нет ящика, с которого ушло первое письмо"):
            await answers.answer_reply(
                session,
                Sending(session, transport),
                thread_id=talk.thread.id,
                reply_id=reply.id,
                body="Thanks!",
                author_id=None,
            )

        assert (report.sent, report.postponed) == (0, 1)
        assert "ящик переписки неизвестен" in caplog.text
        assert transport.seen == []
        # Ответ, которому некуда уйти, и строки не заводит.
        assert (
            await session.scalar(select(MessageModel.id).where(MessageModel.step == ANSWER_STEP))
            is None
        )
