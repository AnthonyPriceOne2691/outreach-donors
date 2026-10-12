"""Что уходит и приходит по маршрутам перезапроса цены."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

from backend.features.core.domain import PriceSource
from backend.features.letters.price_recheck import BATCH_MAX, Asked, Recheck, Refused, Stale
from backend.features.letters.recheck_answer import SamePrice
from backend.features.letters.recheck_target import Way


class RecheckRow(BaseModel):
    """Донор с протухшей ценой: можно ли перезапросить и куда уйдёт письмо.

    Цена — числом с валютой, как у строки обхода (`crawls.DonorRow`): конвертации нет.
    """

    donor_id: int
    host: str
    price: float | None = None
    currency: str | None = None
    #: Из ответа донора или вписана человеком; пусто — цена до 07.10.2026, из ответа.
    source: PriceSource | None = None
    priced_at: datetime | None = None
    #: Когда последний перезапрос ушёл донору. Пусто — не перезапрашивали.
    asked_at: datetime | None = None
    #: Почему перезапрос сейчас не уйдёт — словами сервера. Пусто — уйдёт.
    refusal: str | None = None
    #: Куда уйдёт: `thread` — в переписку, где донор отвечал, `first` — новым первым
    #: письмом. Пусто — не уйдёт (`refusal`).
    way: Way | None = None
    thread_id: int | None = None
    #: На какой адрес и с какого ящика. Ящик первого письма выбирает отправка — пусто.
    email: str | None = None
    mailbox: str | None = None

    @classmethod
    def of(cls, found: Recheck) -> RecheckRow:
        plan = found.plan
        return cls(
            donor_id=found.donor_id,
            host=found.host,
            price=float(found.price) if found.price is not None else None,
            currency=found.currency,
            source=found.source,
            priced_at=found.priced_at,
            asked_at=found.asked_at,
            refusal=found.refusal,
            way=plan.way if plan is not None else None,
            thread_id=plan.thread_id if plan is not None else None,
            email=plan.email if plan is not None else None,
            mailbox=plan.mailbox if plan is not None else None,
        )


class RecheckCard(RecheckRow):
    """Перезапрос одного донора — и текст письма целиком: его читают до нажатия."""

    subject: str | None = None
    body: str | None = None

    @classmethod
    def of(cls, found: Recheck) -> RecheckCard:
        return cls(**RecheckRow.of(found).model_dump(), subject=found.subject, body=found.body)


class RechecksView(BaseModel):
    """Доноры с протухшей ценой — и сроки, о которых говорят слова экрана."""

    rows: list[RecheckRow]
    #: Сколько их всего: строк бывает меньше — по потолку списка.
    total: int
    #: Срок свежести цены, дней.
    ttl_days: int
    #: Перезапрос одному донору — не чаще раза в столько дней.
    every_days: int
    #: Сколько доноров перезапрашивается одним нажатием — число сервера, а не копия экрана.
    batch_max: int = BATCH_MAX

    @classmethod
    def of(cls, found: Stale, *, ttl_days: int, every_days: int) -> RechecksView:
        return cls(
            rows=[RecheckRow.of(row) for row in found.rows],
            total=found.total,
            ttl_days=ttl_days,
            every_days=every_days,
        )


class RecheckBody(BaseModel):
    """Кому перезапросить цену: номера доноров — одного с карточки, нескольких с панели."""

    donor_ids: list[int] = Field(min_length=1)

    @field_validator("donor_ids")
    @classmethod
    def _not_too_many(cls, donor_ids: list[int]) -> list[int]:
        if len(donor_ids) > BATCH_MAX:
            raise PydanticCustomError(
                "too_many_donors",
                f"За раз перезапрашиваем не больше {BATCH_MAX} доноров, выбрано "
                f"{len(donor_ids)}: письма уходят тут же, по одному",
            )
        return donor_ids


class SentRow(BaseModel):
    """Перезапрос ушёл."""

    donor_id: int
    host: str
    message_id: int
    thread_id: int | None = None
    way: Way
    sender_email: str
    #: Ушло ли письмо на самом деле. У нулевого транспорта — нет.
    real: bool


class RefusedRow(BaseModel):
    """Перезапрос не ушёл — почему, словами."""

    donor_id: int
    host: str | None = None
    reason: str


class RecheckResult(BaseModel):
    """Что ушло и что нет — по каждому донору."""

    sent: list[SentRow]
    refused: list[RefusedRow]

    @classmethod
    def of(cls, done: list[Asked | Refused]) -> RecheckResult:
        return cls(
            sent=[
                SentRow(
                    donor_id=one.donor_id,
                    host=one.host,
                    message_id=one.message_id,
                    thread_id=one.thread_id,
                    way=one.way,
                    sender_email=one.sender_email,
                    real=one.real,
                )
                for one in done
                if isinstance(one, Asked)
            ],
            refused=[
                RefusedRow(donor_id=one.donor_id, host=one.host, reason=one.reason)
                for one in done
                if isinstance(one, Refused)
            ],
        )


class SamePriceCard(BaseModel):
    """«Цена та же»: прежняя цена донора, подтверждённая ответом на перезапрос."""

    reply_id: int
    price: Decimal
    currency: str | None = None
    #: Когда донор ответил — с этой минуты цена снова свежая.
    at: datetime
    #: Легла ли цена в карточку: слово донора новее этого ответа её не пускает.
    stored: bool

    @classmethod
    def of(cls, done: SamePrice) -> SamePriceCard:
        return cls(
            reply_id=done.reply_id,
            price=done.price,
            currency=done.currency,
            at=done.at,
            stored=done.stored,
        )
