"""«Добавить ключи» гипотезе плана: ключи текстом или книгой одним листом — в реестр ключей.

Кнопка вкладки «Гипотезы» — при «нужны ключи» и когда угодно (`plan_actions.py`: замок, этап и
журнал). Ключи — **той же чисткой, что ключи книги плана** (`plan_file.cleaned`): общая
нормализация, без пустых строк, точных повторов и мусора гигиены, — иначе реестр взял бы ключ,
который выдача не купит.

- **Текст** — ключ в строке, как в поле ключей прогона сбора.
- **Книга** (.xlsx) — тот же разбор, что у книги плана (`plan_file`: предел файла, бомба, битый
  файл — отказ словами), один видимый лист, ключи — в колонке A. Есть строка «Ключи» — ключи под
  ней, как на листе плана: лист плана годится как есть; нет — вся колонка A.
- Ключей за раз — не больше, чем берёт лист плана (`plan_file.MAX_SHEET_KEYS`): дальше чтение не
  идёт, и отказ называет предел.

**В реестр** — на каждом рынке листа, вслед за его ключами (`position` — дальше последнего): волна
берёт их по порядку, и ключ, добавленный позже, сперва догоняет страницу 1 (`plan_waves`). Уже
известные гипотезе (`keyword_key` на рынке) пропускаются вставкой без конфликта — это и правило
реестра, и защита от повтора кнопки. Ни одного нового — отказ словами.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.donors.bulk_file import _shown_name
from backend.features.sales.plan_actions import PlanAskError, hold, journal, keys_back
from backend.features.sales.plan_card import KEYS_LABEL, flat
from backend.features.sales.plan_file import (
    MAX_SHEET_KEYS,
    UNREADABLE_SHEET,
    PlanFileError,
    PlanKeys,
    _check,
    _opened,
    _rows,
    _UnreadableSheetError,
    cleaned,
)
from backend.features.sales.plan_models import SalesPlanKeywordModel
from backend.features.sales.plan_store import CHUNK
from backend.features.sales.segment_spend import number
from backend.features.serp.protocol import keyword_key

logger = logging.getLogger(__name__)

Key = SalesPlanKeywordModel

#: «Действие» записи журнала.
KEYS_ADDED = "ключи гипотезы плана добавлены"
TOO_MANY = f"ключей больше {number(MAX_SHEET_KEYS)} — столько берёт лист плана: добавьте их частями"
NO_KEYS = "ключей нет — впишите по ключу в строке"


@dataclass(frozen=True, slots=True)
class Added:
    """Что дала «Добавить ключи»: новых ключей, уже известных гипотезе и отсеянных чисткой."""

    added: int
    known: int
    rejected: int


def _taken(written: list[str]) -> PlanKeys:
    if len(written) > MAX_SHEET_KEYS:
        raise PlanAskError(TOO_MANY)
    return cleaned(written)


def keys_of_text(text: str) -> PlanKeys:
    """Ключи текстом — по одному в строке."""
    return _taken([line.strip() for line in text.splitlines() if line.strip()])


def keys_of_book(data: bytes, name: str) -> PlanKeys:
    """Ключи из книги одним листом (шапка модуля); не прочитать — `PlanFileError` словами."""
    shown = _shown_name(name)
    _check(data, name, shown)
    book = _opened(data, shown)
    try:
        sheets = [sheet for sheet in book.worksheets if sheet.sheet_state == "visible"]
        if len(sheets) != 1:
            raise PlanFileError(
                f"в книге {shown} листов: {len(sheets)} — ключи берутся из книги одним листом: "
                "ключ в строке, в колонке A"
            )
        return _taken(_column(sheets[0], shown))
    finally:
        book.close()


def _column(sheet: Any, shown: str) -> list[str]:
    """Колонка A: под строкой «Ключи», если она есть, иначе целиком; пустые строки — мимо."""
    found: list[str] = []
    try:
        for _, first, _second in _rows(sheet):
            if flat(first) == flat(KEYS_LABEL):
                found = []
            elif first:
                found.append(first)
            if len(found) > MAX_SHEET_KEYS:
                break
    except _UnreadableSheetError as exc:
        logger.warning("план работ: лист ключей не читается", extra={"error": str(exc)})
        raise PlanFileError(f"файл {shown}: {UNREADABLE_SHEET}") from exc
    return found


async def _next_position(session: AsyncSession, row_id: int) -> int:
    last = await session.scalar(
        select(func.max(Key.position)).where(Key.plan_hypothesis_id == row_id)
    )
    return int(last or 0) + 1


async def add_keys(
    session: AsyncSession, row_id: int, keys: PlanKeys, *, author_id: int | None, now: datetime
) -> Added:
    """«Добавить ключи»: новые строки реестра на каждом рынке листа (шапка модуля); «нужны ключи»
    снимается, и гипотеза, стоявшая из-за них, снова в работе (`plan_actions.keys_back`)."""
    if not keys.taken:
        raise PlanAskError(
            NO_KEYS
            if not keys.rejected
            else f"все ключи отсеяны ({number(len(keys.rejected))}) — выдача их не купит"
        )
    held = await hold(session, row_id)
    row = held.row
    start = await _next_position(session, row.id)
    values = [
        {
            "plan_hypothesis_id": row.id,
            "market": market,
            "keyword": phrase,
            "keyword_key": keyword_key(phrase),
            "position": position,
        }
        for market in row.markets
        for position, phrase in enumerate(keys.taken, start)
    ]
    fresh: set[str] = set()
    for first in range(0, len(values), CHUNK):
        made = await session.scalars(
            insert(Key)
            .values(values[first : first + CHUNK])
            .on_conflict_do_nothing(constraint="uq_sales_plan_keywords_key")
            .returning(Key.keyword_key)
        )
        fresh.update(made.all())
    found = Added(len(fresh), len(keys.taken) - len(fresh), len(keys.rejected))
    if not found.added:
        raise PlanAskError(
            f"новых ключей нет: все {number(found.known)} уже есть у гипотезы «{held.name}»"
        )
    await keys_back(session, held, now)
    await journal(
        session,
        held.plan,
        author_id,
        {
            "действие": KEYS_ADDED,
            "гипотеза": held.name,
            "ключей": found.added,
            "уже были": found.known,
            "отсеяно": found.rejected,
        },
    )
    logger.info("план работ: ключи добавлены", extra={"plan_hypothesis_id": row.id})
    return found
