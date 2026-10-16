"""Месячный кап юнитов Ahrefs: действующий, последние смены и смена с экрана «Расход».

**Кап задают на экране, окружение — умолчание** (решение владельца 16.10.2026). До этого
кап жил только в переменной `AHREFS_UNITS_CAP`: смета прогона советовала «поднимите кап»,
а поднять его было нечем — только правкой окружения на сервере. Теперь действующий кап —
последняя смена на экране «Расход» (`ahrefs_caps`); смен нет — то, что в окружении.

**Одна функция действующего капа** (`ahrefs_units_cap`) — у всех, кто его читает: смета
и потолок прогона, остаток капа (`spending.cap_left`), экран «Расход» и «Обзор», тревога
сторожа, пакет DR Этапа 2, консольный прогон. Кап, прочитанный мимо неё, разошёлся бы
с экраном молча: экран говорил бы «кап 150 000», а прогон считал бы от ста тысяч.

**Границы — от единицы до лимита тарифа** (`AHREFS_MONTHLY_UNITS`). Кап выше лимита
тарифа ничего не ограничивает — то же правило, что у проверки умолчания при старте
(`config/startup_checks.check_collect`); нулевой — не потолок трат, а их запрет без
единого слова о том, почему прогоны стоят. За границей — отказ словами: что не так
и какое допустимо. Те же границы экран получает в ответе и проверяет поле ими до нажатия.

**Смена — под замком.** Прежний кап для журнала («было → стало») читается под
рекомендательной блокировкой транзакции: две смены разом иначе обе записали бы одно
и то же «было», и журнал врал бы о второй. Замок снимается фиксацией или откатом.

**Смена не правит прошлое.** Строка заводится новая, прежние остаются — по ним видно,
кто и когда менял кап. Прогон, поставленный до смены, держит потолок, обещанный при
нажатии, но не больше остатка капа к своему старту (`budget.ceiling_at_start`), — уже
по новому капу.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import ahrefs as ahrefs_cfg
from backend.features.core.models.access import UserModel
from backend.features.core.models.ops import AhrefsCapModel

#: Ключ замка смены капа — «AHRCAP» байтами. Смены редки и коротки: замок один на все.
CAP_LOCK = 0x414852434150

#: Наименьший кап. Ноль — не кап, а запрет тратить.
MIN_CAP = 1

#: Сколько последних смен отдаёт экран: «кто и когда менял» — о недавнем. Вся история —
#: в журнале действий, с «было → стало».
RECENT = 5


class CapRangeError(ValueError):
    """Кап за границами. Текст говорит, что не так и какое допустимо."""


@dataclass(frozen=True, slots=True)
class CapChange:
    """Смена капа: что поставили, кто и когда."""

    id: int
    units_cap: int
    #: Почта учётки. Пусто — учётку удалили, а строка смены осталась.
    set_by: str | None
    set_at: datetime


def cap_limits() -> tuple[int, int]:
    """Допустимый кап — целое от `MIN_CAP` до лимита тарифа включительно.

    Лимит тарифа читается при вызове, а не при импорте: он живёт в `config/ahrefs.py`,
    и правка окружения (или подмена в тесте) видна сразу."""
    return MIN_CAP, ahrefs_cfg.MONTHLY_UNITS


def _spaced(number: int) -> str:
    """Число с разрядами, как на экране: «1 000 000»."""
    return f"{number:,}".replace(",", " ")


def cap_refusal(units_cap: int) -> str | None:
    """Почему этот кап не годится — словами; `None` — годится."""
    low, high = cap_limits()
    if low <= units_cap <= high:
        return None
    why = (
        "выше лимита тарифа Ahrefs кап ничего не ограничивает"
        if units_cap > high
        else "кап меньше единицы — не потолок трат, а запрет на них"
    )
    return (
        f"Кап {_spaced(units_cap)} не годится: допустимо от {_spaced(low)} "
        f"до {_spaced(high)} юнитов в месяц — {why}"
    )


async def ahrefs_units_cap(session: AsyncSession) -> int:
    """Действующий месячный кап: последняя смена на экране «Расход», смен нет — умолчание
    окружения (`AHREFS_UNITS_CAP`). Умолчание читается при вызове, как и лимит тарифа."""
    latest = await session.scalar(
        select(AhrefsCapModel.units_cap).order_by(AhrefsCapModel.id.desc()).limit(1)
    )
    return ahrefs_cfg.UNITS_CAP if latest is None else latest


async def cap_changes(session: AsyncSession, *, limit: int = RECENT) -> list[CapChange]:
    """Последние смены капа, новые первыми: что поставили, кто и когда."""
    rows = await session.execute(
        select(AhrefsCapModel.id, AhrefsCapModel.units_cap, UserModel.email, AhrefsCapModel.set_at)
        .outerjoin(UserModel, UserModel.id == AhrefsCapModel.set_by)
        .order_by(AhrefsCapModel.id.desc())
        .limit(limit)
    )
    return [
        CapChange(id=row_id, units_cap=cap, set_by=email, set_at=at)
        for row_id, cap, email, at in rows.tuples().all()
    ]


@dataclass(frozen=True, slots=True)
class CapSet:
    """Итог смены: какой кап действовал до неё и сама смена."""

    was: int
    change: CapChange


async def set_ahrefs_cap(session: AsyncSession, units_cap: int, *, author: UserModel) -> CapSet:
    """Новый месячный кап — с этой минуты: строка смены и прежний кап для журнала.

    Без коммита: смена и запись журнала ложатся одной транзакцией вызывающего. За
    границами — `CapRangeError` до записи. Тот же кап, что действует, — тоже смена:
    так умолчание окружения закрепляют на экране, и правка окружения его больше не сдвинет.
    """
    refusal = cap_refusal(units_cap)
    if refusal is not None:
        raise CapRangeError(refusal)
    await session.execute(select(func.pg_advisory_xact_lock(CAP_LOCK)))
    was = await ahrefs_units_cap(session)
    row = AhrefsCapModel(units_cap=units_cap, set_by=author.id)
    session.add(row)
    await session.flush()
    change = CapChange(id=row.id, units_cap=row.units_cap, set_by=author.email, set_at=row.set_at)
    return CapSet(was=was, change=change)
