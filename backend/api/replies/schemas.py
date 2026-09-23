"""Что принимает и отдаёт подтверждение разбора."""

from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, model_validator


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
