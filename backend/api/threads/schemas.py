"""Что отдают маршруты диалогов."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.outreach.repository import ThreadDetail, ThreadRow
from backend.features.outreach.threads import ThreadState
from backend.features.replies.outcome import waiting_for_review


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
    #: Этап рассылки: донору писали о цене, рекламодателю — оффер.
    stage: Stage
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
            stage=row.stage,
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
    #: Адрес, с которого ответили. Может отличаться от того, кому писали:
    #: на общий ящик смотрит секретарь и пересылает письмо редактору.
    from_email: str | None
    subject: str | None
    #: Что пришло файлами. Прайс приходит вложением чаще, чем текстом,
    #: и ответ с вложением не должен выглядеть пустым.
    attachments: list[dict[str, Any]] | None
    price_white: Decimal | None
    price_grey: Decimal | None
    currency: str | None
    payment_methods: list[str] | None
    confidence: float | None
    #: Продаёт ли донор размещение по разбору: `sells`, `declines`, `unclear`.
    placement: str | None
    #: Ждёт ли разбор человека. Считается, а не хранится: второе поле
    #: разошлось бы с уверенностью при первой правке порога.
    needs_review: bool
    #: Ответ рекламодателя: не цена, а лид. Его не разбирают, а берут
    #: в работу — `reviewed_by`/`reviewed_at` тогда говорят, кто и когда.
    lead: bool
    reviewed_by: str | None
    reviewed_at: datetime | None

    @classmethod
    def of(cls, reply: ReplyModel, stage: Stage = Stage.DONORS) -> IncomingCard:
        lead = stage is Stage.ADVERTISERS and reply.kind is ReplyKind.HUMAN
        return cls(
            id=reply.id,
            kind=reply.kind,
            raw_body=reply.raw_body,
            received_at=reply.created_at,
            from_email=reply.from_email,
            subject=reply.subject,
            attachments=reply.attachments,
            price_white=reply.price_white,
            price_grey=reply.price_grey,
            currency=reply.currency,
            payment_methods=reply.payment_methods,
            confidence=reply.confidence,
            placement=reply.placement,
            # У лида нечего разбирать: форма цены для него — отказ.
            needs_review=not lead
            and waiting_for_review(
                reply.kind, reply.confidence, reviewed=reply.reviewed_at is not None
            ),
            lead=lead,
            reviewed_by=reply.reviewed_by,
            reviewed_at=reply.reviewed_at,
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
            incoming=[IncomingCard.of(r, detail.row.stage) for r in detail.replies],
        )
