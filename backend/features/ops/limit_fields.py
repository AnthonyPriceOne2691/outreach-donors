"""Общее у строк панели лимитов (`core/limit_rows.py`): задачи, поля, пустая строка и сутки счёта.

Строка договора — восемнадцать обязательных полей, а у большинства строк половина их пуста: чего нет,
то `None` (нет лимита, нет цены). Источники собирают строку из пустой (`BLANK`) и называют только то,
что у них есть: забытое поле остаётся «нет», а не случайным числом.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from decimal import ROUND_HALF_UP, Decimal

from backend.features.core.limit_rows import LimitRow, Quote

#: Задачи — группы панели.
SALES, OUTREACH = "Продажи", "Аутрич"

#: Поля строки, которые меняются с экрана.
PER_SESSION, PER_DAY, PER_MONTH = "per_session", "per_day", "per_month"

#: Месяц оценки стоимости: лимит дня целиком тридцать дней.
MONTH_DAYS = 30

#: Сколько секунд в часе: сроки обхода в настройках — секундами, а строка говорит часами.
HOUR_SEC = 3600

#: Вписано то, что уже действует: менять нечего, и смена только завела бы строку истории.
SAME = "Это действующее значение — впишите другое"

CENT = Decimal("0.01")

#: Пустая строка: всё пусто, менять нечего. Заполняют через `dataclasses.replace`.
BLANK = LimitRow(
    key="",
    task=OUTREACH,
    service="",
    action="",
    scope="",
    unit="",
    per_session=None,
    session_hours=None,
    per_day=None,
    per_month=None,
    used_day=None,
    used_month=None,
    spent_month_usd=None,
    cost_usd=None,
    cost_note=None,
    now=None,
    source="",
    editable=frozenset(),
)


def day_start(moment: datetime) -> datetime:
    """Начало суток счёта — полночь UTC: тот же день, что у потолков и дневных лимитов писем."""
    return datetime.combine(moment.astimezone(UTC).date(), time.min, tzinfo=UTC)


def usd(value: Decimal | float) -> Decimal:
    """Деньги строки — до цента: плавающая точка в ответе дала бы 0.1 + 0.2."""
    return Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)


def refused(why: str) -> Quote:
    """Просчёт, который отказывает: подтверждать нечего."""
    return Quote(cost_usd=None, words=why, refusal=why, accepted=0)
