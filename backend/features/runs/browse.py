"""Чтение прогонов: что запускали и чем кончилось.

Отчёт прогона нужен не ради истории, а ради двух чисел рядом: смета
и факт. Их расхождение — единственная проверка сметы; без неё оценка
расхода ничем не подтверждается, и разговор «почему кончились юниты»
не с чего начинать.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.run import RunModel


class UnknownRunError(ValueError):
    """Прогона с таким номером нет."""


@dataclass(frozen=True, slots=True)
class RunRow:
    """Строка списка прогонов."""

    run: RunModel

    @property
    def estimate_error(self) -> float | None:
        """На сколько смета разошлась с фактом, в долях. `None` — прогон
        ещё идёт или расхода не было."""
        estimated, actual = self.run.estimated_units, self.run.actual_units
        if not estimated or actual is None:
            return None
        return (actual - estimated) / estimated


class RunBrowser:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def recent(self, *, limit: int = 30) -> list[RunRow]:
        rows = await self._session.execute(
            select(RunModel).order_by(RunModel.id.desc()).limit(limit)
        )
        return [RunRow(run=run) for run in rows.scalars().all()]

    async def one(self, run_id: int) -> RunRow:
        run = await self._session.get(RunModel, run_id)
        if run is None:
            raise UnknownRunError(f"Прогона №{run_id} нет")
        return RunRow(run=run)
