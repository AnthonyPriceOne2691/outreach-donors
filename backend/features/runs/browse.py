"""Чтение прогонов: что запускали и чем кончилось.

Отчёт прогона нужен не ради истории, а ради двух чисел рядом: смета
и факт. Их расхождение — единственная проверка сметы; без неё оценка
расхода ничем не подтверждается, и разговор «почему кончились юниты»
не с чего начинать.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.run import RunModel
from backend.features.donors.selection import ReviewTally, tally_reviews


class UnknownRunError(ValueError):
    """Прогона с таким номером нет."""


@dataclass(frozen=True, slots=True)
class RunRow:
    """Строка списка прогонов."""

    run: RunModel
    #: Проверка человеком доменов этого прогона — на момент чтения.
    review: ReviewTally = field(default_factory=ReviewTally)

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
        runs = list(rows.scalars().all())
        tallies = await tally_reviews(self._session, {run.id: _hosts(run) for run in runs})
        return [RunRow(run=run, review=tallies[run.id]) for run in runs]

    async def one(self, run_id: int) -> RunRow:
        run = await self._session.get(RunModel, run_id)
        if run is None:
            raise UnknownRunError(f"Прогона №{run_id} нет")
        tallies = await tally_reviews(self._session, {run.id: _hosts(run)})
        return RunRow(run=run, review=tallies[run.id])


def _hosts(run: RunModel) -> list[str]:
    hosts = (run.candidates or {}).get("hosts")
    return [str(host) for host in hosts] if isinstance(hosts, list) else []
