"""Что принимает и отдаёт подтверждение разбора — и список ответов без письма."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, model_validator

from backend.api.threads.schemas import AttachmentCard
from backend.features.core.domain import ReplyKind
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies import unbound


class ReviewBody(BaseModel):
    """Что человек решил про цену в ответе.

    Поля приходят целиком, а не правкой отдельных: «поправить серую цену»
    и «серой цены нет» на частичной правке неразличимы, а разница между
    ними — целая строка в базе.
    """

    price_white: Decimal | None = None
    price_grey: Decimal | None = None
    currency: str | None = None
    payment_methods: list[str] = []
    #: Донор ответил «не продаём размещения». Для гест-постинга это ответ
    #: на главный вопрос письма, и он ложится на домен — поля цены при этом
    #: обязаны быть пустыми: «не продаём» с ценой — противоречие.
    declines: bool = False

    @model_validator(mode="after")
    def _declines_without_price(self) -> ReviewBody:
        if self.declines and (self.price_white is not None or self.price_grey is not None):
            raise ValueError(
                "«Не продаёт размещения» и цена вместе не бывают: уберите цену или снимите отметку"
            )
        return self


class Reviewed(BaseModel):
    """Итог подтверждения."""

    id: int
    reviewed_by: str
    #: Попала ли цена в карточку донора.
    stored_price: bool
    #: Что легло на домен как ответ донора: `sells`, `declines` или ничего.
    seller_answer: str | None = None


class LeadTaken(BaseModel):
    """Лид взят в работу: кто и когда — и ушёл ли он в CRM."""

    id: int
    reviewed_by: str
    reviewed_at: datetime
    #: «queued» — передача стоит в очереди; «off» — адрес вебхука не задан,
    #: лид остаётся в диалогах и в выгрузке CSV.
    handoff: str = "off"


class LeadSent(BaseModel):
    """Передача лида поставлена в очередь заново."""

    id: int
    job_id: str


class VersionCalibration(BaseModel):
    """Калибровка одной версии промпта разбора."""

    version: str
    reviewed: int
    as_is: int
    edited: int
    #: Поле → сколько раз человек его поправил.
    wrong: dict[str, int]
    #: Цена положена сама, без человека: сверять не с чем.
    auto_stored: int
    #: Ждут человека: решения ещё нет.
    waiting: int


class Calibration(BaseModel):
    """Что предложила модель против того, что решил человек, по версиям."""

    versions: list[VersionCalibration]


class UnboundCard(BaseModel):
    """Ответ, который не привязался ни к одному нашему письму.

    Всё, по чему человек ищет донора сам: от кого, на какой адрес, тема,
    текст и файлы — и почему приём не нашёл письма. Адрес отправителя —
    целиком: это внутренний экран, и по домену отправителя донора и ищут.
    """

    id: int
    received_at: datetime
    from_email: str | None
    #: На какие адреса пришло: конверт, «кому», копия — наш первым. Пусто
    #: у ответов, принятых до того, как адреса стали хранить.
    to: list[str]
    subject: str | None
    kind: ReplyKind
    #: Начало того, что написал человек, — одной строкой, без цитаты.
    preview: str
    #: Текст целиком — строка списка раскрывается в него.
    text: str
    attachments: list[AttachmentCard]
    #: Почему не привязан — код (`replies/binding.Unbound`). Пусто — причина
    #: не записана: ответ принят раньше, чем её стали сохранять.
    reason: str | None
    #: То же словами — и что с таким ответом делать.
    reason_text: str

    @classmethod
    def of(cls, reply: ReplyModel, files: Sequence[ReplyAttachmentModel] = ()) -> UnboundCard:
        return cls(
            id=reply.id,
            received_at=reply.created_at,
            from_email=reply.from_email,
            to=list(reply.to_addresses or []),
            subject=reply.subject,
            kind=reply.kind,
            preview=unbound.preview(reply.raw_body),
            text=reply.raw_body,
            attachments=list(map(AttachmentCard.of, files)),
            reason=reply.unbound_reason,
            reason_text=unbound.explain(reply.unbound_reason, reply.to_addresses),
        )


class UnboundView(BaseModel):
    """Страница ответов без письма."""

    rows: list[UnboundCard]
    #: Сколько их всего, а не на этой странице: по нему экран считает
    #: страницы и отличает «ответов нет» от «на этой странице пусто».
    total: int
    #: Какая это страница (с единицы) и сколько на ней ответов: размер
    #: страницы задаёт сервер.
    page: int
    limit: int
