"""Что отдают маршруты диалогов."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from backend.api.letters.schemas import Corridor
from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.outreach.repository import ThreadDetail, ThreadRow
from backend.features.outreach.threads import ThreadState, review_of


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
    #: Доля изменённых слов относительно шаблона, 0–1 — как у письма
    #: в очереди (`QueuedLetterCard.uniqueness`). Поле базы называется
    #: `uniqueness_pct`, но хранит долю: карточка отдавала его под этим
    #: именем, экран поверил имени и печатал «отличие 0%» у письма
    #: с отличием 19% (25.09.2026). Имя в ответе — по смыслу, не по колонке.
    uniqueness: float | None
    #: Номер входящего ответа, на который это письмо отвечает. Пусто —
    #: первое письмо или добивка (`letters/answers.py`).
    answers_reply_id: int | None = None

    @classmethod
    def of(cls, message: MessageModel) -> LetterCard:
        return cls(
            id=message.id,
            step=message.step,
            status=message.status,
            subject=message.subject,
            body=message.body,
            sent_at=message.sent_at,
            uniqueness=message.uniqueness_pct,
            answers_reply_id=message.answers_reply_id,
        )


class AnswerBody(BaseModel):
    """Наш ответ на входящий ответ собеседника."""

    reply_id: int
    body: str = Field(min_length=1, max_length=20_000)


class AttachmentCard(BaseModel):
    """Вложение ответа: сведения о файле. Сам файл — отдельным запросом
    (`GET /api/replies/{reply_id}/attachments/{id}`), только на скачивание."""

    id: int
    name: str
    #: Байт. Пусто — размер неизвестен: файл назван, но не пришёл.
    size: int | None
    content_type: str | None
    #: Файл сохранён и скачивается.
    accepted: bool
    #: Почему не сохранён — словами, для человека.
    reason: str | None

    @classmethod
    def of(cls, row: ReplyAttachmentModel) -> AttachmentCard:
        return cls(
            id=row.id,
            name=row.name,
            size=row.size,
            content_type=row.content_type,
            accepted=row.accepted,
            reason=row.reason,
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
    attachments: list[AttachmentCard] = Field(default_factory=list)
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
    #: Почему ответ не человека всё равно разбирает человек — словами.
    #: Сейчас это автоответ с суммой в валюте: модель автоответы не разбирает,
    #: и цену из него вписывают руками. Пусто — обычный ответ.
    review_reason: str | None = None
    #: Ответ рекламодателя: не цена, а лид. Его не разбирают, а берут
    #: в работу — `reviewed_by`/`reviewed_at` тогда говорят, кто и когда.
    lead: bool
    reviewed_by: str | None
    reviewed_at: datetime | None

    @classmethod
    def of(
        cls,
        reply: ReplyModel,
        stage: Stage = Stage.DONORS,
        files: Sequence[ReplyAttachmentModel] = (),
    ) -> IncomingCard:
        lead = stage is Stage.ADVERTISERS and reply.kind is ReplyKind.HUMAN
        # У лида нечего разбирать: форма цены для него — отказ (`review_of`).
        review = review_of(reply, stage)
        return cls(
            id=reply.id,
            kind=reply.kind,
            raw_body=reply.raw_body,
            received_at=reply.created_at,
            from_email=reply.from_email,
            subject=reply.subject,
            attachments=list(map(AttachmentCard.of, files)),
            price_white=reply.price_white,
            price_grey=reply.price_grey,
            currency=reply.currency,
            payment_methods=reply.payment_methods,
            confidence=reply.confidence,
            placement=reply.placement,
            needs_review=review.waiting,
            review_reason=review.reason,
            lead=lead,
            reviewed_by=reply.reviewed_by,
            reviewed_at=reply.reviewed_at,
        )


class ThreadView(BaseModel):
    """Переписка целиком."""

    card: ThreadCard
    letters: list[LetterCard]
    incoming: list[IncomingCard]
    #: Коридор отличия — рядом с отличием письма. Отдаёт сервер: на карточке
    #: стояло вшитое «цель 15–25%», которое разошлось бы с настройкой
    #: при первой её правке.
    corridor: Corridor = Field(default_factory=Corridor)

    @classmethod
    def of(
        cls, detail: ThreadDetail, files: Mapping[int, Sequence[ReplyAttachmentModel]]
    ) -> ThreadView:
        return cls(
            card=ThreadCard.of(detail.row),
            letters=[LetterCard.of(m) for m in detail.messages],
            incoming=[
                IncomingCard.of(r, detail.row.stage, files.get(r.id, ())) for r in detail.replies
            ],
        )
