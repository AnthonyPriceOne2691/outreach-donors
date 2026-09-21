"""Что принимает и отдаёт подтверждение разбора."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel


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


class Reviewed(BaseModel):
    """Итог подтверждения."""

    id: int
    reviewed_by: str
    #: Попала ли цена в карточку донора.
    stored_price: bool
