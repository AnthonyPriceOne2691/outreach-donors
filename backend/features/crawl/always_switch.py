"""Выключатель обхода 24/7: идёт ли, порог «мало рекламодателей» и «переобход слабых» — строками
истории.

Слово владельца 17.11.2026: обход доноров идёт постоянно, кнопкой «старт / пауза» (`always.py`), а
сайты, где на первых 10 000 страниц меньше X % страниц с платной ссылкой, получают пометку «мало
рекламодателей» и дальше не обходятся — X меняется настройкой, а «переобход даже слабых» её снимает.
**В базе, а не в окружении**, как автокруг (`auto_round_switch.py`): выключатель под рукой у того,
кто видит обход, и по строкам видно, кто пустил обходчики без остановки, кто остановил и кто двигал
порог. Строка на каждую смену, действует последняя; строк нет — на паузе, порог `DEFAULT_THRESHOLD`.

**Порог по умолчанию — 0,1 %** (решение владельца 17.11.2026, данные прода: из 155 сайтов у 102
продающих страниц нет вовсе, у 40 — 0,1–5 %, у 13 — от 5 %; при 5 % отпали бы 142). Шаг — сотая
процента, границы — 0 и 100.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.access import UserModel
from backend.features.core.models.crawl import CrawlAlwaysSwitchModel

#: Порог «мало рекламодателей», % страниц с платной ссылкой, пока его не меняли (шапка).
DEFAULT_THRESHOLD = 0.1
#: Границы порога и его шаг — сотая процента.
LOWEST = 0.0
HIGHEST = 100.0
STEP_DIGITS = 2


@dataclass(frozen=True, slots=True)
class Switch:
    """Обход 24/7 сейчас: идёт ли, порог и переобход слабых, кто так поставил. Строк нет — на
    паузе, порог по умолчанию, слабые пропускаются, ставившего нет."""

    enabled: bool = False
    threshold_pct: float = DEFAULT_THRESHOLD
    recrawl_weak: bool = False
    #: Кто поставил действующую строку — почта и номер учётки; учётку удалили — пусто.
    set_by: str | None = None
    by_id: int | None = None
    set_at: datetime | None = None


async def state(session: AsyncSession) -> Switch:
    """Выключатель как он стоит — по последней строке."""
    row = (
        await session.execute(
            select(CrawlAlwaysSwitchModel, UserModel.email)
            .outerjoin(UserModel, UserModel.id == CrawlAlwaysSwitchModel.set_by)
            .order_by(CrawlAlwaysSwitchModel.id.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return Switch()
    last, email = row
    return Switch(
        enabled=last.enabled,
        threshold_pct=float(last.threshold_pct),
        recrawl_weak=last.recrawl_weak,
        set_by=email,
        by_id=last.set_by,
        set_at=last.set_at,
    )


class ThresholdOutOfRangeError(ValueError):
    """Порог «мало рекламодателей» вне границ или мельче сотой процента."""


def threshold_problem(value: float) -> str | None:
    """Что не так с порогом — словами; `None` — годен. Одна проверка у схемы запроса и здесь."""
    if not LOWEST <= value <= HIGHEST:
        return f"Порог — от {LOWEST:g} до {HIGHEST:g} %, пришло {value:g}"
    if round(value, STEP_DIGITS) != value:
        return f"Порог — с шагом 0,01 %, пришло {value:g}"
    return None


async def set_switch(
    session: AsyncSession,
    *,
    by: int | None,
    enabled: bool | None = None,
    threshold_pct: float | None = None,
    recrawl_weak: bool | None = None,
) -> tuple[Switch, Switch]:
    """Запустить, поставить на паузу, сменить порог или переобход слабых — новой строкой истории.
    Не названное — как стоит. Ответ — как стояло до смены и как стало; то же, что стоит, строки не
    заводит: история — о сменах. Без фиксации: её делает вызывающий вместе с записью журнала."""
    was = await state(session)
    wanted = Switch(
        enabled=was.enabled if enabled is None else enabled,
        threshold_pct=was.threshold_pct if threshold_pct is None else threshold_pct,
        recrawl_weak=was.recrawl_weak if recrawl_weak is None else recrawl_weak,
    )
    problem = threshold_problem(wanted.threshold_pct)
    if problem is not None:
        raise ThresholdOutOfRangeError(problem)
    if _settings(wanted) == _settings(was):
        return was, was
    session.add(
        CrawlAlwaysSwitchModel(
            enabled=wanted.enabled,
            threshold_pct=wanted.threshold_pct,
            recrawl_weak=wanted.recrawl_weak,
            set_by=by,
        )
    )
    await session.flush()
    return was, await state(session)


def _settings(switch: Switch) -> tuple[bool, float, bool]:
    return switch.enabled, switch.threshold_pct, switch.recrawl_weak
