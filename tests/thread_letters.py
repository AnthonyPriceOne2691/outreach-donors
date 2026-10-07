"""Переписка и зависшие письма — для тестов исхода письма и своего пути.

Строится тем же кодом, что в бою: первое письмо уходит отправкой (`Sending`),
добивка рождается проходом добивок (`followups.send_due`), ответ — ответом
из карточки переписки (`answers.answer_reply`). Зависает письмо так же, как
у настоящей платформы: почта не ответила, когда запрос мог уже дойти
(`MaybeSentError`), и письмо осталось «отправляется».
"""

from __future__ import annotations

import contextlib
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import datetime

import pytest
from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from backend.features.letters import answers
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.followups import send_due
from backend.features.letters.sending import SendError, Sending
from backend.features.letters.transport import (
    MaybeSentError,
    NullTransport,
    Outgoing,
    TransportError,
)
from backend.features.replies.repository import ReplyRepository
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import FILLED, make_donor, make_sender

HOST = "stuck.example.test"
#: Ящик, который ведёт переписку.
BOX = "outreach1@mail.example.test"
#: Сроки добивок рассылки, дней.
CADENCE = [3, 7]
#: Адрес, с которого донор ответил: не тот, на который ушло первое письмо.
ANSWERED_FROM = "boss@stuck.example.test"
#: Его `Message-ID` — к нему ложится наш ответ.
INBOUND_ID = "<in-1@stuck.example.test>"


class Unanswered:
    """Почта не ответила, когда запрос мог уже дойти: ушло ли письмо — неизвестно."""

    name = "sendgrid"
    real = True

    async def send(self, outgoing: Outgoing) -> str:
        raise MaybeSentError(
            f"Почтовая платформа не ответила на письмо №{outgoing.message_id} — письмо могло уйти"
        )


class Refusing:
    """Почта сказала «нет»: письмо точно не ушло, и отправка вернёт его назад."""

    name = "sendgrid"
    real = True

    async def send(self, outgoing: Outgoing) -> str:
        raise TransportError(f"Платформа отказала (503) на письмо №{outgoing.message_id}")


class Recording(NullTransport):
    """Нулевая почта, которая запоминает, что ей отдали."""

    def __init__(self) -> None:
        self.seen: list[Outgoing] = []

    async def send(self, outgoing: Outgoing) -> str:
        self.seen.append(outgoing)
        return await super().send(outgoing)


@dataclass(frozen=True, slots=True)
class Conversation:
    """Первое письмо ушло с ящика переписки — по-настоящему, отправкой."""

    first: MessageModel
    thread: ThreadModel
    sender: SenderModel


async def first_letter(
    session: AsyncSession, *, host: str = HOST, stage: Stage = Stage.DONORS
) -> MessageModel:
    """Первое письмо в очереди: с тредом, адресом и сроками добивок рассылки."""
    domain = await make_donor(session, host, email=f"editor@{host}")
    campaign = CampaignModel(
        name=f"Рассылка {host}", stage=stage, status="draft", followup_days=CADENCE
    )
    session.add(campaign)
    await session.flush()
    contact_id = await session.scalar(
        select(ContactModel.id).where(ContactModel.domain_id == domain.id)
    )
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact_id)
    session.add(thread)
    await session.flush()
    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact_id,
        step=0,
        status=MessageStatus.QUEUED,
        subject="Advertising rates",
        body=f"Hi there,\n\n{FILLED['OUTREACH_POSTAL_ADDRESS']}",
        idempotency_key=f"{stage.value}:{host}:0",
    )
    session.add(message)
    await session.flush()
    return message


async def conversation(
    session: AsyncSession, *, sent_at: datetime, host: str = HOST, box: str = BOX
) -> Conversation:
    """Первое письмо ушло с ящика `box` в `sent_at`: срок добивки — по рассылке."""
    first = await first_letter(session, host=host)
    sender = await session.scalar(select(SenderModel).where(SenderModel.email == box))
    if sender is None:
        sender = await make_sender(session, box)
    await Sending(session, NullTransport(), now=sent_at).send(first.id)
    await session.refresh(first)
    thread = await session.get(ThreadModel, first.thread_id)
    assert thread is not None
    return Conversation(first=first, thread=thread, sender=sender)


async def hang(sending: Awaitable[object]) -> None:
    """Отправка, на которой почта не ответила: письмо остаётся «отправляется»."""
    with pytest.raises(MaybeSentError):
        await sending


async def age(session: AsyncSession, message: MessageModel, *, since: datetime) -> None:
    """Письмо отдали почте в `since` — так оно выглядит спустя минуты после обрыва."""
    await session.execute(
        update(MessageModel).where(MessageModel.id == message.id).values(updated_at=since)
    )
    await session.commit()
    await session.refresh(message)


async def stuck_first(session: AsyncSession, *, since: datetime, host: str = HOST) -> MessageModel:
    """Первое письмо, на котором почта не ответила."""
    message = await first_letter(session, host=host)
    if await session.scalar(select(SenderModel.id).where(SenderModel.email == BOX)) is None:
        await make_sender(session, BOX)
    await hang(Sending(session, Unanswered()).send(message.id))
    await age(session, message, since=since)
    return message


async def stuck_followup(
    session: AsyncSession, talk: Conversation, *, since: datetime
) -> MessageModel:
    """Добивка, на которой почта не ответила: проход добивок её не повторяет."""
    assert talk.first.next_action_at is not None
    report = await send_due(session, transport=Unanswered(), limit=1, now=talk.first.next_action_at)
    assert report.unknown == 1, report.as_report
    followup = await session.scalar(
        select(MessageModel).where(MessageModel.thread_id == talk.thread.id, MessageModel.step == 1)
    )
    assert followup is not None
    await age(session, followup, since=since)
    return followup


async def human_reply(session: AsyncSession, talk: Conversation) -> ReplyModel:
    """Донор ответил на первое письмо — человеком и с другого адреса. Адрес
    ответившего ложится к контактам донора, как его кладёт приём."""
    await ReplyRepository(session).remember_answering_address(
        domain_id=talk.first.domain_id, email=ANSWERED_FROM
    )
    reply = ReplyModel(
        thread_id=talk.thread.id,
        message_id=talk.first.id,
        kind=ReplyKind.HUMAN,
        raw_body="Our price is $90. Which topic?",
        inbound_message_id=INBOUND_ID,
        from_email=ANSWERED_FROM,
        subject="Re: Advertising rates",
    )
    session.add(reply)
    await session.commit()
    return reply


async def answer(
    session: AsyncSession, talk: Conversation, reply: ReplyModel, transport: object
) -> MessageModel:
    """Наш ответ из карточки переписки — той почтой, что дали; строка ответа."""
    # Отказ или обрыв здесь — состояние, которое строится, а не проверка.
    with contextlib.suppress(MaybeSentError, SendError):
        await answers.answer_reply(
            session,
            Sending(session, transport),  # type: ignore[arg-type]
            thread_id=talk.thread.id,
            reply_id=reply.id,
            body="Thanks! A guide on home repair, 1500 words.",
            author_id=None,
        )
    found = await session.scalar(
        select(MessageModel).where(
            MessageModel.thread_id == talk.thread.id, MessageModel.step == ANSWER_STEP
        )
    )
    assert found is not None
    return found
