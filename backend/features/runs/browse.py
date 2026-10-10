"""Чтение прогонов: что запускали и чем кончилось.

Отчёт прогона нужен не ради истории, а ради двух чисел рядом: смета
и факт. Их расхождение — единственная проверка сметы; без неё оценка
расхода ничем не подтверждается, и разговор «почему кончились юниты»
не с чего начинать.

**Проверка судьи человеком — одним правилом с рассмотрением прогона**
(`tally_reviews`). До 10.10.2026 колонка знала только «Отбор», и прогоны
с 26 и 7 принятыми стояли с «человек не смотрел» (проверка прода).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import RunStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.donors.selection import human_advice
from backend.features.review.candidates import DECISION_ADVICE, Decision
from backend.shared.database.ids import storable

#: Сколько прогонов на странице истории. Число живёт только здесь: экран
#: узнаёт его из ответа и сам не хранит — второй экземпляр на фронте
#: разошёлся бы с этим при первой правке.
PAGE_SIZE = 10

#: Больше за раз не отдаём: страница истории — чтение глазами, а не выгрузка.
MAX_PAGE_SIZE = 50


class UnknownRunError(ValueError):
    """Прогона с таким номером нет."""


@dataclass(frozen=True, slots=True)
class ReviewTally:
    """Проверка судьи человеком по доменам прогона: о скольких человек сказал
    своё и сколько раз разошёлся с судьёй. «Посмотри» судьи — просьба, а не
    мнение, и расхождением не считается."""

    reviewed: int = 0
    disagreements: int = 0


#: Слова о доменах прогона: домен → (совет человека, совет судьи).
Words = dict[str, tuple[str | None, str | None]]

#: Советы, которые можно сравнить: «площадка» и «не площадка».
ADVICES = ("accept", "reject")


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
    #: Сколько прогонов стоит в очереди — по всей истории, а не на этой
    #: странице. По нему экран говорит «задачу некому взять»: прогон в очереди
    #: на первой странице не виден тому, кто смотрит вторую, а предупреждение
    #: касается и его.
    queued: int = 0


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
        queued = await self._session.scalar(
            select(func.count()).select_from(RunModel).where(RunModel.status == RunStatus.QUEUED)
        )
        rows = await self._session.execute(
            select(RunModel).order_by(RunModel.id.desc()).limit(size).offset((number - 1) * size)
        )
        return RunsPage(
            rows=await self._rows(list(rows.scalars().all())),
            total=int(total or 0),
            queued=int(queued or 0),
        )

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
        run = await self._session.get(RunModel, run_id) if storable(run_id) else None
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


async def tally_reviews(
    session: AsyncSession, runs: dict[int, list[str]]
) -> dict[int, ReviewTally]:
    """Проверка судьи человеком по прогонам — прогон → его домены выдачи.

    Слово человека о домене прогона — решение в очереди этого прогона (принят —
    «площадка», отклонён — «не площадка», правилом `review.candidates.DECISION_ADVICE`;
    перенесённое из прошлого прогона — тоже, оно и стоит во вкладке), а без него —
    тип сайта, названный на «Отборе». Решения очереди так же считает рассмотрение
    прогона (`RunReview.accuracy` по прогону), и без слов «Отбора» колонка истории
    и плитки рассмотрения называют одни числа. «Отбор» добавляет домены, о которых
    человек сказал там, — и отрезанные судьёй, до очереди не дошедшие.

    Считается при чтении, а не в конце прогона: человек решает после.
    """
    if not runs:
        return {}
    words: dict[int, Words] = {run_id: {} for run_id in runs}
    for run_id, host, status, judge in await _queue_words(session, list(runs)):
        words[run_id][host] = (DECISION_ADVICE.get(status), judge)
    typed = await _selection_words(session, {host for hosts in runs.values() for host in hosts})
    for run_id, hosts in runs.items():
        _add_typed(words[run_id], hosts, typed)
    return {run_id: _tally(said) for run_id, said in words.items()}


async def _queue_words(
    session: AsyncSession, run_ids: Sequence[int]
) -> Sequence[tuple[int, str, str, str | None]]:
    """Решения очереди прогонов: прогон, домен, решение, совет судьи."""
    rows = await session.execute(
        select(
            RunCandidateModel.run_id,
            DomainModel.host,
            RunCandidateModel.status,
            DomainModel.judge_recommendation,
        )
        .join(DomainModel, DomainModel.id == RunCandidateModel.domain_id)
        .where(RunCandidateModel.run_id.in_(run_ids))
        .where(RunCandidateModel.status != Decision.PENDING.value)
    )
    return rows.tuples().all()


async def _selection_words(session: AsyncSession, hosts: set[str]) -> Words:
    """Тип сайта, названный человеком на «Отборе»: домен → (совет, совет судьи)."""
    if not hosts:
        return {}
    rows = await session.execute(
        select(DomainModel.host, DomainModel.human_intent, DomainModel.judge_recommendation)
        .where(DomainModel.host.in_(hosts))
        .where(DomainModel.human_intent.is_not(None))
    )
    return {host: (human_advice(intent), judge) for host, intent, judge in rows.tuples() if intent}


def _add_typed(words: Words, hosts: Sequence[str], typed: Words) -> None:
    """Слова «Отбора» — тем доменам прогона, о которых очередь молчит: решение
    в очереди сильнее, с ним сходятся плитки рассмотрения."""
    for host in hosts:
        if host in typed:
            words.setdefault(host, typed[host])


def _tally(words: Words) -> ReviewTally:
    return ReviewTally(
        reviewed=len(words),
        disagreements=sum(
            1
            for human, judge in words.values()
            if human in ADVICES and judge in ADVICES and human != judge
        ),
    )
