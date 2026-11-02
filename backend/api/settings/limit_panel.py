"""Сборка панели лимитов: строки всех источников, просчёт и смена по ключу строки.

Панель одна на сервис, источники — разные (`core/limit_rows.py`): общие строки стоят в
`features/ops/limit_*.py`, строки продаж — в `features/sales/limits.py`. Сборка живёт здесь, а не в
ядре: модуль продаж импортируется без моста, как договорено, а почта о продажах не знает.

**Новый источник — одна строка списка** (`EDITABLE`, `READ_ONLY`): строки у него есть сразу, сборка
о его ключах не знает ничего, кроме того, что он их назвал.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.features.core.limit_rows import (
    LimitRefusedError,
    LimitRow,
    LimitSource,
    Quote,
    RowSource,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.core.models.access import UserModel

#: Порядок групп на экране: задача, которой здесь нет, встаёт за ними.
TASKS = ("Продажи", "Аутрич")

#: Источники со сменой с экрана — по порядку на экране. Модуль продаж (`features/sales/limits.py`)
#: встаёт первой строкой: `from backend.features.sales import limits as sales_limits`.
EDITABLE: tuple[LimitSource, ...] = ()

#: Источники только для чтения: у модуля одна функция `rows`.
READ_ONLY: tuple[RowSource, ...] = ()


def _rank(row: LimitRow) -> int:
    return TASKS.index(row.task) if row.task in TASKS else len(TASKS)


async def rows(session: AsyncSession) -> list[LimitRow]:
    """Все строки панели: по задачам, внутри задачи — по порядку источников."""
    found = [row for source in (*EDITABLE, *READ_ONLY) for row in await source.rows(session)]
    return sorted(found, key=_rank)


async def _owner(session: AsyncSession, key: str, field: str) -> tuple[LimitSource, LimitRow]:
    """Чья строка и можно ли менять это поле. Ключи читаются из самих строк: у сборки нет их
    списка, и источник, поменявший `editable` по состоянию, меняет его здесь же."""
    for source in EDITABLE:
        for row in await source.rows(session):
            if row.key == key:
                if field not in row.editable:
                    raise LimitRefusedError(row.now or "Это значение с экрана не меняется")
                return source, row
    raise LimitRefusedError("Эта строка панели с экрана не меняется")


async def quote(session: AsyncSession, key: str, field: str, value: int) -> Quote:
    """Просчёт нового значения. Ничего не пишет."""
    source, _ = await _owner(session, key, field)
    return await source.quote(session, key, field, value)


async def change(
    session: AsyncSession, key: str, field: str, value: int, author: UserModel
) -> Quote:
    """Смена — с последним просчётом: источник повторяет его сам, а ответ говорит, что встало. Без
    фиксации: смена и журнал ложатся одной транзакцией вызывающего."""
    source, _ = await _owner(session, key, field)
    quoted = await source.quote(session, key, field, value)
    if quoted.refusal is not None:
        raise LimitRefusedError(quoted.refusal)
    await source.change(session, key, field, value, author)
    return quoted
