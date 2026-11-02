"""Панель лимитов на экране «Расход»: строки, просчёт нового значения и смена.

Своим файлом, как кап Ahrefs и потолки модели: схемы расхода — у предела длины. Договор строки и
просчёта — `core/limit_rows.py`; здесь только его форма в ответе. Числа экран называет, а не считает:
чего нет — `null`, и почему — словами сервера.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Literal, Self

from pydantic import BaseModel, field_validator
from pydantic_core import PydanticCustomError

from backend.features.core.limit_rows import LimitRow, Quote


class LimitBody(BaseModel):
    """Какое значение какой строки: ключ, поле и новое значение. Границы называет сам просчёт
    (`Quote.refusal`) — у каждой строки свои."""

    key: str
    field: Literal["per_session", "per_day", "per_month"]
    value: int

    @field_validator("value", mode="before")
    @classmethod
    def _whole(cls, value: object) -> object:
        """Лимит — целое число: пусто, дробь, строка и «да/нет» — отказ словами, а не именем поля."""
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise PydanticCustomError("limit_not_whole", "Лимит — целое число")


class LimitChangeBody(LimitBody):
    """Смена: значение и то, что человек видел как подтверждаемое (`QuoteCard.accepted`). Пусто —
    просчёта не было, и смена отказывает словами (`limit_panel.change`)."""

    quoted: int | None = None


class LimitRowCard(BaseModel):
    """Строка панели — поля договора; `editable` — списком, по порядку."""

    key: str
    task: str
    service: str
    action: str
    scope: str
    unit: str
    per_session: int | None
    session_hours: int | None
    per_day: int | None
    per_month: int | None
    used_day: int | None
    used_month: int | None
    spent_month_usd: Decimal | None
    cost_usd: Decimal | None
    cost_note: str | None
    now: str | None
    source: str
    editable: list[str]

    @classmethod
    def of(cls, row: LimitRow) -> Self:
        return cls(
            key=row.key,
            task=row.task,
            service=row.service,
            action=row.action,
            scope=row.scope,
            unit=row.unit,
            per_session=row.per_session,
            session_hours=row.session_hours,
            per_day=row.per_day,
            per_month=row.per_month,
            used_day=row.used_day,
            used_month=row.used_month,
            spent_month_usd=row.spent_month_usd,
            cost_usd=row.cost_usd,
            cost_note=row.cost_note,
            now=row.now,
            source=row.source,
            editable=sorted(row.editable),
        )


class LimitPanelView(BaseModel):
    """Панель: все строки, которые видит учётка, по задачам."""

    rows: list[LimitRowCard]


class QuoteCard(BaseModel):
    """Ответ просчёта: стоимость нового лимита, слова, отказ и что подтверждается на деле."""

    cost_usd: Decimal | None
    words: str
    refusal: str | None
    accepted: int

    @classmethod
    def of(cls, quote: Quote) -> Self:
        return cls(
            cost_usd=quote.cost_usd,
            words=quote.words,
            refusal=quote.refusal,
            accepted=quote.accepted,
        )
