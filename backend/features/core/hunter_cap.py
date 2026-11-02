"""Месячный потолок проверок адреса Hunter — с экрана «Расход» (слово Anthony 02.11.2026: «заведи
поле в настройки админа, как с Ahrefs и моделью, чтобы можно было руками править»).

До этого потолок жил только в `SALES_VERIFIER_MONTH_CAP`: в октябре он кончился за три дня большой
очистки, а поднять его можно было только правкой сервера. Настройка — строкой истории, как у капа
Ahrefs и потолков модели: кто, когда, «было → стало»; действует последняя строка.

**Разбор действующего потолка — здесь, в одном месте** (`verify_month_cap`): настройка, если её
сохраняли, в том числе ноль, иначе `SALES_VERIFIER_MONTH_CAP`. Ответ — число или слова, почему
числа нет. Читают сторож, экран и очистка продаж: второй разбор разошёлся бы с первым. Снять
настройку нельзя: у строки нет пустого значения, а ноль — решение человека: проверки адресов
остановлены.

**Граница — проверок тарифа, когда они известны** (`refusal`): их называет снимок учётки, и потолок
выше тарифа ничего бы не держал. Снимка ещё нет — граница только от опечатки (`MOST`).

Числа счёта — с первого числа месяца, а учётка Hunter обнуляется в свой день: это разные границы,
и экран говорит это словами.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.features.core.models.access import UserModel
from backend.features.core.models.ops import HunterCapModel
from backend.shared.numbers import spaced

#: Настройка `.env`, которую заменяет сохранённый на экране потолок.
SETTING = "SALES_VERIFIER_MONTH_CAP"

#: Потолка нет ни на экране, ни в настройках сервера.
NOT_SET = "потолок проверки адресов на месяц не задан — задайте его на экране «Расход»"

#: Операция журнала расхода — одна проверка адреса. Имя у модуля продаж
#: (`sales/verifier.OPERATION`): почта и сторож его не импортируют, равенство держит тест.
VERIFY_OPERATION = "sales_verify"

#: Больше этого за месяц не проверить ни на каком тарифе: потолок выше — опечатка, а не план.
MOST = 1_000_000

#: Ключ замка смены — «HUNCAP» байтами. Смены редки и коротки: замок один.
CAP_LOCK = 0x48554E434150

#: Сколько последних смен отдаёт экран; вся история — в журнале действий.
RECENT = 5


class HunterCapError(ValueError):
    """Потолок не годится: отрицательный или выше проверок тарифа. Текст говорит, что допустимо."""


@dataclass(frozen=True, slots=True)
class HunterCapChange:
    """Смена потолка: что поставили, кто и когда. `set_by` пусто — учётку удалили."""

    id: int
    verify_cap: int
    set_by: str | None
    set_at: datetime


@dataclass(frozen=True, slots=True)
class HunterCapSet:
    """Итог смены: что стояло на экране до неё (`None` — не сохраняли) и сама смена."""

    was: int | None
    change: HunterCapChange


def env_cap() -> int | str:
    """Потолок из `SALES_VERIFIER_MONTH_CAP` — числом, или почему его нет — словами. Разбор при
    чтении: опечатка в настройке не роняет общий старт процессов, а останавливает платную часть
    очистки словами."""
    raw = sales_cfg.VERIFIER_MONTH_CAP
    if not raw:
        return NOT_SET
    if not raw.isdigit():
        return f"{SETTING} «{raw}» — нужно целое число проверок адреса в месяц, не меньше нуля"
    return int(raw)


async def stored_verify_cap(session: AsyncSession) -> int | None:
    """Последнее значение настройки с экрана; строк нет — `None`."""
    found: int | None = await session.scalar(
        select(HunterCapModel.verify_cap).order_by(HunterCapModel.id.desc()).limit(1)
    )
    return found


async def verify_month_cap(session: AsyncSession) -> int | str:
    """Действующий потолок проверок адреса на месяц: настройка, в том числе ноль, иначе
    `SALES_VERIFIER_MONTH_CAP`. Числа нет — слова, почему."""
    stored = await stored_verify_cap(session)
    return env_cap() if stored is None else stored


def refusal(wanted: int, ceiling: int | None) -> str | None:
    """Почему потолок не годится — словами; `None` — годится. `ceiling` — проверок тарифа,
    `None` — снимка учётки ещё нет."""
    if wanted < 0:
        return "Потолок — целое число проверок адреса в месяц, не меньше нуля"
    if ceiling is not None and wanted > ceiling:
        return f"Потолок выше проверок тарифа Hunter: {spaced(ceiling)} в цикл учётки"
    if wanted > MOST:
        return f"Потолок не больше {spaced(MOST)} проверок адреса в месяц"
    return None


async def set_verify_cap(
    session: AsyncSession, wanted: int, *, ceiling: int | None, author: UserModel
) -> HunterCapSet:
    """Новый потолок — с этой минуты. Без коммита: смена и запись журнала ложатся одной
    транзакцией вызывающего.

    Не годится — `HunterCapError` до записи. Прежнее «было» читается под рекомендательной
    блокировкой транзакции: две смены разом иначе записали бы одно «было».
    """
    why = refusal(wanted, ceiling)
    if why is not None:
        raise HunterCapError(why)
    await session.execute(select(func.pg_advisory_xact_lock(CAP_LOCK)))
    was = await stored_verify_cap(session)
    row = HunterCapModel(verify_cap=wanted, set_by=author.id)
    session.add(row)
    await session.flush()
    change = HunterCapChange(row.id, wanted, author.email, row.set_at)
    return HunterCapSet(was=was, change=change)


async def cap_changes(session: AsyncSession, *, limit: int = RECENT) -> list[HunterCapChange]:
    """Последние смены потолка, новые первыми."""
    rows = await session.execute(
        select(HunterCapModel.id, HunterCapModel.verify_cap, UserModel.email, HunterCapModel.set_at)
        .outerjoin(UserModel, UserModel.id == HunterCapModel.set_by)
        .order_by(HunterCapModel.id.desc())
        .limit(limit)
    )
    return [
        HunterCapChange(row_id, verify_cap, email, at)
        for row_id, verify_cap, email, at in rows.tuples().all()
    ]


def cap_words(verify_cap: int | None) -> str:
    """Потолок словами журнала: «5 000 проверок в месяц»; не сохраняли — «как в настройках
    сервера»; ноль — «проверки адресов остановлены»."""
    if verify_cap is None:
        return "как в настройках сервера"
    if verify_cap == 0:
        return "0 — проверки адресов остановлены"
    return f"{spaced(verify_cap)} проверок в месяц"
