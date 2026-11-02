"""Сборка панели лимитов: строки всех источников, права строки, просчёт и смена по ключу.

Панель одна на сервис, источники — разные (`core/limit_rows.py`): общие строки стоят в
`features/ops/limit_*.py`, строки продаж — в `features/sales/limits.py`. Сборка живёт здесь, а не в
ядре: модуль продаж импортируется без моста, как договорено, а почта о продажах не знает.

**Новый источник — одна строка списка** (`EDITABLE`, `READ_ONLY`): строки у него есть сразу, сборка
о его ключах не знает ничего, кроме того, что он их назвал.

**Панель — правом «менять потолки расхода», строка — ещё и правом своего этапа** (`stage_of`): без
права «Продажи» панель без продаж, как и весь «Расход» (П2б). Договор строки этапа не несёт, поэтому
этап читается из начала ключа. Кап Ahrefs на своей карточке меняют правом «править пороги», и панель
этого права не обходит (`EXTRA_RIGHTS`).

**Смена — только за просчётом.** Сервер просчётов не хранит: смена приносит число, которое видел
человек (`quoted`), и считает заново. Нет числа — не просчитывали; разошлось — просчёт устарел, и
слова называют новое число. Стало недопустимым — отказ самого просчёта. Источник повторяет его ещё
раз под своим замком, потому что смена с устаревшим просчётом не должна писать ничего.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from backend.features.access.permissions import Actor, require, require_stage
from backend.features.core.domain import Permission, Stage
from backend.features.core.limit_rows import (
    LimitRefusedError,
    LimitRow,
    LimitSource,
    Quote,
    RowSource,
)
from backend.features.ops import limit_ahrefs, limit_mail, limit_model, limit_services
from backend.shared.numbers import spaced

if TYPE_CHECKING:
    from collections.abc import Collection

    from sqlalchemy.ext.asyncio import AsyncSession

    from backend.features.core.models.access import UserModel

#: Порядок групп на экране: задача, которой здесь нет, встаёт за ними.
TASKS = ("Продажи", "Аутрич")

#: Источники со сменой с экрана — по порядку на экране. Модуль продаж (`features/sales/limits.py`)
#: встаёт первой строкой: `from backend.features.sales import limits as sales_limits`.
EDITABLE: tuple[LimitSource, ...] = (limit_model, limit_ahrefs)

#: Источники только для чтения: у модуля одна функция `rows`.
READ_ONLY: tuple[RowSource, ...] = (limit_mail, limit_services)

#: Этап строки — по началу ключа; не названо — строка общая. Общий потолок модели — этапом продаж:
#: его выбранное — сумма всех частей, и расход продаж виден был бы вычитанием.
STAGES_BY_KEY: tuple[tuple[str, Stage], ...] = (
    ("sales.", Stage.SALES),
    (limit_model.GENERAL_KEY, Stage.SALES),
    ("outreach.donors.", Stage.DONORS),
    ("outreach.advertisers.", Stage.ADVERTISERS),
)

#: Право сверх права панели — у строки, которую меняют другим правом на её карточке.
EXTRA_RIGHTS = {limit_ahrefs.KEY: Permission.SETTINGS}

#: Что закрыто без права этапа — началом фразы отказа.
CLOSED_ROW = "Эта строка панели лимитов"

NOT_QUOTED = "Сначала просчитайте новое значение: без просчёта смена не идёт"


def stage_of(key: str) -> Stage | None:
    """Этап строки по её ключу; `None` — строка общая."""
    return next((stage for start, stage in STAGES_BY_KEY if key.startswith(start)), None)


def visible(found: list[LimitRow], stages: Collection[Stage]) -> list[LimitRow]:
    """Строки этапов, которые видит учётка, и общие."""
    allowed = {None, *stages}
    return [row for row in found if stage_of(row.key) in allowed]


def guard(actor: Actor, key: str) -> None:
    """Пустить к строке: право её этапа и право её карточки. Отказ — словами права."""
    require_stage(actor, stage_of(key), CLOSED_ROW)
    extra = EXTRA_RIGHTS.get(key)
    if extra is not None:
        require(actor, extra)


def _rank(row: LimitRow) -> int:
    return TASKS.index(row.task) if row.task in TASKS else len(TASKS)


async def rows(session: AsyncSession) -> list[LimitRow]:
    """Все строки панели: по задачам, внутри задачи — по порядку источников."""
    found = [row for source in (*EDITABLE, *READ_ONLY) for row in await source.rows(session)]
    return sorted(found, key=_rank)


async def _owner(session: AsyncSession, key: str, field: str) -> LimitSource:
    """Чья строка и можно ли менять это поле. Ключи читаются из самих строк: у сборки нет их
    списка, и источник, поменявший `editable` по состоянию, меняет его здесь же."""
    for source in EDITABLE:
        for row in await source.rows(session):
            if row.key == key:
                if field not in row.editable:
                    raise LimitRefusedError(row.now or "Это значение с экрана не меняется")
                return source
    raise LimitRefusedError("Эта строка панели с экрана не меняется")


async def quote(session: AsyncSession, key: str, field: str, value: int) -> Quote:
    """Просчёт нового значения. Ничего не пишет."""
    source = await _owner(session, key, field)
    return await source.quote(session, key, field, value)


async def change(
    session: AsyncSession, key: str, field: str, value: int, quoted: int | None, author: UserModel
) -> Quote:
    """Смена — за просчётом: `quoted` — что человек видел как подтверждаемое. Возвращает последний
    просчёт: что встало на деле. Без фиксации: смена и журнал ложатся транзакцией вызывающего."""
    if quoted is None:
        raise LimitRefusedError(NOT_QUOTED)
    source = await _owner(session, key, field)
    fresh = await source.quote(session, key, field, value)
    if fresh.refusal is not None:
        raise LimitRefusedError(fresh.refusal)
    if fresh.accepted != quoted:
        raise LimitRefusedError(
            f"Просчёт устарел: теперь подтвердится {spaced(fresh.accepted)}, "
            f"а не {spaced(quoted)} — просчитайте заново"
        )
    await source.change(session, key, field, value, author)
    return fresh
