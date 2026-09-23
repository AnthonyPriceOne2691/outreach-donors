"""Чтение прогонов: что запускали и чем кончилось.

Отчёт прогона нужен не ради истории, а ради двух чисел рядом: смета
и факт. Их расхождение — единственная проверка сметы; без неё оценка
расхода ничем не подтверждается, и разговор «почему кончились юниты»
не с чего начинать.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.donors.selection import ReviewTally, tally_reviews


class UnknownRunError(ValueError):
    """Прогона с таким номером нет."""


@dataclass(frozen=True, slots=True)
class RunRow:
    """Строка списка прогонов."""

    run: RunModel
    #: Проверка человеком доменов этого прогона — на момент чтения.
    review: ReviewTally = field(default_factory=ReviewTally)
    #: Очередь рассмотрения прогона: статус → сколько. Пусто — прогон
    #: сделан до очереди и в неё не положен.
    queue: dict[str, int] = field(default_factory=dict)

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
        queues = await self._queues([run.id for run in runs])
        return [
            RunRow(run=run, review=tallies[run.id], queue=queues.get(run.id, {})) for run in runs
        ]

    async def one(self, run_id: int) -> RunRow:
        run = await self._session.get(RunModel, run_id)
        if run is None:
            raise UnknownRunError(f"Прогона №{run_id} нет")
        tallies = await tally_reviews(self._session, {run.id: _hosts(run)})
        queues = await self._queues([run.id])
        return RunRow(run=run, review=tallies[run.id], queue=queues.get(run.id, {}))

    async def _queues(self, run_ids: list[int]) -> dict[int, dict[str, int]]:
        rows = await self._session.execute(
            select(RunCandidateModel.run_id, RunCandidateModel.status, func.count())
            .where(RunCandidateModel.run_id.in_(run_ids))
            .group_by(RunCandidateModel.run_id, RunCandidateModel.status)
        )
        queues: dict[int, dict[str, int]] = {}
        for run_id, status, count in rows.all():
            queues.setdefault(run_id, {})[status] = int(count)
        return queues


def _hosts(run: RunModel) -> list[str]:
    hosts = (run.candidates or {}).get("hosts")
    return [str(host) for host in hosts] if isinstance(hosts, list) else []
