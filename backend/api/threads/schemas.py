"""Что отдают маршруты диалогов."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel

from backend.features.core.domain import MessageStatus, ReplyKind
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.outreach.repository import ThreadDetail, ThreadRow
from backend.features.outreach.threads import ThreadState


class ThreadCard(BaseModel):
    """Строка списка диалогов.

    Состояние приходит вычисленным, а не хранимым: отдельное поле
    рассинхронизируется с письмами при первом сбое, и список врёт именно
    там, где по нему принимают решения.
    """

    id: int
    host: str
    contact_email: str | None
    campaign: str
    state: ThreadState
    messages_sent: int
    last_event_at: datetime | None
    last_reply_at: datetime | None
    price_white: Decimal | None
    price_grey: Decimal | None
    currency: str | None

    @classmethod
    def of(cls, row: ThreadRow) -> ThreadCard:
        return cls(
            id=row.thread.id,
            host=row.host,
            contact_email=row.contact_email,
            campaign=row.campaign_name,
            state=row.summary.state,
            messages_sent=row.summary.messages_sent,
            last_event_at=row.summary.last_event_at,
            last_reply_at=row.summary.last_reply_at,
            price_white=row.summary.price_white,
            price_grey=row.summary.price_grey,
            currency=row.summary.currency,
        )


class LetterCard(BaseModel):
    """Наше письмо в переписке."""

    id: int
    step: int
    status: MessageStatus
    subject: str | None
    body: str | None
    sent_at: datetime | None
    uniqueness_pct: float | None

    @classmethod
    def of(cls, message: MessageModel) -> LetterCard:
        return cls(
            id=message.id,
            step=message.step,
            status=message.status,
            subject=message.subject,
            body=message.body,
            sent_at=message.sent_at,
            uniqueness_pct=message.uniqueness_pct,
        )


class IncomingCard(BaseModel):
    """Входящее письмо и то, что из него распознали.

    Исходный текст отдаётся всегда и рядом с разобранным: человек должен
    видеть, откуда взялось число, иначе проверить его нечем.
    """

    id: int
    kind: ReplyKind
    raw_body: str
    received_at: datetime
    price_white: Decimal | None
    price_grey: Decimal | None
    currency: str | None
    payment_methods: list[str] | None
    confidence: float | None

    @classmethod
    def of(cls, reply: ReplyModel) -> IncomingCard:
        return cls(
            id=reply.id,
            kind=reply.kind,
            raw_body=reply.raw_body,
            received_at=reply.created_at,
            price_white=reply.price_white,
            price_grey=reply.price_grey,
            currency=reply.currency,
            payment_methods=reply.payment_methods,
            confidence=reply.confidence,
        )


class ThreadView(BaseModel):
    """Переписка целиком."""

    card: ThreadCard
    letters: list[LetterCard]
    incoming: list[IncomingCard]

    @classmethod
    def of(cls, detail: ThreadDetail) -> ThreadView:
        return cls(
            card=ThreadCard.of(detail.row),
            letters=[LetterCard.of(m) for m in detail.messages],
            incoming=[IncomingCard.of(r) for r in detail.replies],
        )
