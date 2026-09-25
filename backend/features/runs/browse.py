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
from backend.features.review.candidates import Decision

#: Сколько прогонов на странице истории. Число живёт только здесь: экран
#: узнаёт его из ответа и сам не хранит — второй экземпляр на фронте
#: разошёлся бы с этим при первой правке.
PAGE_SIZE = 10

#: Больше за раз не отдаём: страница истории — чтение глазами, а не выгрузка.
MAX_PAGE_SIZE = 50


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


@dataclass(frozen=True, slots=True)
class RunsPage:
    """Страница истории и сколько прогонов всего — по нему считают страницы."""

    rows: list[RunRow]
    total: int


class RunBrowser:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def page(self, number: int, *, size: int = PAGE_SIZE) -> RunsPage:
        """Страница истории, новые сверху. Номер — с единицы.

        Страница за концом — пустая, с настоящим `total`, а не отказ: экран
        узнаёт из неё, сколько страниц есть на самом деле, и переходит
        на последнюю. Ссылка на пятую страницу, открытая после чистки базы,
        иначе выглядела бы поломкой.
        """
        total = await self._session.scalar(select(func.count()).select_from(RunModel))
        rows = await self._session.execute(
            select(RunModel).order_by(RunModel.id.desc()).limit(size).offset((number - 1) * size)
        )
        return RunsPage(rows=await self._rows(list(rows.scalars().all())), total=int(total or 0))

    async def with_accepted(self) -> list[RunRow]:
        """Прогоны, в которых человек кого-то принял, — все, новые сверху.

        Из них собирается рассылка. Страницы здесь нет намеренно: первые
        десять прогонов по дате и прогоны, из которых есть что собрать, —
        разные списки, и срез по странице молча прятал бы старый прогон
        с принятыми донорами.
        """
        accepted = select(RunCandidateModel.run_id).where(
            RunCandidateModel.status == Decision.ACCEPTED.value
        )
        rows = await self._session.execute(
            select(RunModel).where(RunModel.id.in_(accepted)).order_by(RunModel.id.desc())
        )
        return await self._rows(list(rows.scalars().all()))

    async def _rows(self, runs: list[RunModel]) -> list[RunRow]:
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
