"""Решение по домену одно — во всех прогонах, в очередях которых домен стоит.

Решение владельца 11.10.2026: «решение по домену должно быть одно». До него
решение ложилось только на строку своего прогона. Домен, ждавший во втором
прогоне, ждал там и после решения в первом: считался в «Рассмотреть» второго,
и принять его там значило перевернуть решение — побеждал последний щелчок.

Теперь строка прогона, где решали, — само решение, а строки домена в остальных
прогонах — его копия, та же, что кладёт очередь нового прогона. «Вернуть
в „предложен“» снимает решение везде. Строки, оставшиеся с прежних времён,
закрывает ревизия `ee6c0a44cbd4`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timedelta

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.contacts.repository import ContactRepository
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.donors.standing import waiting
from backend.features.review.candidates import Decision, RunReview
from backend.features.runs.browse import RunBrowser
from backend.features.runs.exclusions import Exclusions
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.migration_helpers import load_migration
from tests.test_review import NOW, _candidate_ids, _donor, _judged, _run
from tests.test_send_race import committed_sessions

HOST = "blog.example.test"
LATER = NOW + timedelta(days=1)

#: Строка кандидата глазами теста: статус, копия ли, кто решил, когда, записка.
Row = tuple[str, bool, str | None, datetime | None, str | None]

#: «Предложен»: решения нет — ни своего, ни копии, ни автора.
PROPOSED: Row = (Decision.PENDING.value, False, None, None, None)


def copy_of(decision: Decision) -> Row:
    """Копия решения — как её кладёт очередь прогона: без автора, времени и записки."""
    return decision.value, True, None, None, None


async def _row(session: AsyncSession, run: RunModel, host: str = HOST) -> Row:
    found = await session.execute(
        select(
            RunCandidateModel.status,
            RunCandidateModel.carried,
            RunCandidateModel.decided_by,
            RunCandidateModel.decided_at,
            RunCandidateModel.note,
        )
        .join(DomainModel, DomainModel.id == RunCandidateModel.domain_id)
        .where(RunCandidateModel.run_id == run.id, DomainModel.host == host)
    )
    return found.tuples().one()


async def _to_review(session: AsyncSession, run: RunModel) -> int:
    """«Рассмотреть N» у прогона в истории (`runs/browse.py`)."""
    return (await RunBrowser(session).one(run.id)).queue.get(Decision.PENDING.value, 0)


async def _queued(session: AsyncSession, *runs: RunModel) -> RunReview:
    """Домен, ещё не решённый, — в очередях всех названных прогонов."""
    await _judged(session, HOST, "accept")
    review = RunReview(session)
    for run in runs:
        await review.queue_run(run.id, [HOST])
    return review


async def _decide(
    session: AsyncSession,
    review: RunReview,
    run: RunModel,
    decision: Decision,
    *,
    by: str,
    when: datetime = NOW,
    note: str | None = None,
) -> None:
    candidate = (await _candidate_ids(session, run.id))[HOST]
    await review.decide(run.id, [candidate], decision, by=by, note=note, now=when)


class TestOneDecision:
    async def test_decision_in_one_run_closes_the_domain_in_another(
        self, session: AsyncSession
    ) -> None:
        first, second = await _run(session), await _run(session)
        review = await _queued(session, first, second)
        assert await _to_review(session, second) == 1

        await _decide(session, review, first, Decision.ACCEPTED, by="a", note="продаёт статьи")

        assert await _row(session, first) == ("accepted", False, "a", NOW, "продаёт статьи")
        assert await _row(session, second) == copy_of(Decision.ACCEPTED)
        assert await _to_review(session, second) == 0
        assert (await review.page(second.id, status=Decision.PENDING)).counts["pending"] == 0
        assert (await waiting(session)).domains == 0

    async def test_copy_is_the_one_the_queue_lays(self, session: AsyncSession) -> None:
        """Копия у прогона, положенного до решения, — та же, что у положенного после."""
        first, earlier = await _run(session), await _run(session)
        review = await _queued(session, first, earlier)
        await _decide(session, review, first, Decision.REJECTED, by="a", note="бренд")
        later = await _run(session)
        await review.queue_run(later.id, [HOST])

        assert await _row(session, earlier) == await _row(session, later)
        assert await _row(session, later) == copy_of(Decision.REJECTED)

    async def test_new_decision_elsewhere_turns_the_first_into_a_copy(
        self, session: AsyncSession
    ) -> None:
        """Передумать можно в любом прогоне: решение становится своим там, где нажали,
        прежнее — копией, как и все остальные строки домена, какими бы они ни были."""
        first, second, third = await _run(session), await _run(session), await _run(session)
        review = await _queued(session, first, second, third)
        await _decide(session, review, first, Decision.ACCEPTED, by="a")

        await _decide(session, review, second, Decision.REJECTED, by="b", when=LATER, note="бренд")

        assert await _row(session, second) == ("rejected", False, "b", LATER, "бренд")
        assert await _row(session, first) == copy_of(Decision.REJECTED)
        assert await _row(session, third) == copy_of(Decision.REJECTED)
        donor = await _donor(session, HOST)
        assert (donor.review, donor.review_by, donor.review_at) == ("rejected", "b", LATER)

    async def test_undo_reopens_the_copy_laid_by_the_queue(self, session: AsyncSession) -> None:
        first, second = await _run(session), await _run(session)
        review = await _queued(session, first)
        await _decide(session, review, first, Decision.ACCEPTED, by="a")
        await review.queue_run(second.id, [HOST])

        await _decide(session, review, first, Decision.PENDING, by="a")

        assert await _row(session, second) == PROPOSED
        assert await _to_review(session, second) == 1

    @pytest.mark.parametrize("undone_in", ["decision", "copy"])
    async def test_undo_leaves_the_domain_undecided_everywhere(
        self, session: AsyncSession, undone_in: str
    ) -> None:
        """«Вернуть в „предложен“» — решения о домене нет нигде, где бы ни нажали: у самого
        решения или у его копии. До 11.10.2026 отмена возвращала донору решение другого
        прогона: человек нажимал «вернуть», а домен оставался решённым."""
        first, second = await _run(session), await _run(session)
        review = await _queued(session, first, second)
        await _decide(session, review, first, Decision.ACCEPTED, by="a")
        await _decide(session, review, second, Decision.REJECTED, by="b", when=LATER)

        undone = second if undone_in == "decision" else first
        await _decide(session, review, undone, Decision.PENDING, by="b")

        assert await _row(session, first) == await _row(session, second) == PROPOSED
        assert (await _donor(session, HOST)).review is None
        assert [await _to_review(session, run) for run in (first, second)] == [1, 1]


class TestDonorGates:
    """Поиск контактов и гейт следующего прогона по-прежнему смотрят на донора
    (`donors.review`), и донор держит то одно решение, где бы его ни приняли."""

    async def _gates(self, session: AsyncSession) -> tuple[str | None, bool, list[str]]:
        donor = await _donor(session, HOST)
        excluded = await Exclusions(session).excluded_hosts([HOST], now=NOW)
        contacts = await ContactRepository(session).pending_hosts(now=NOW)
        return donor.review, HOST in excluded, contacts

    async def test_gates_follow_the_decision_across_runs(self, session: AsyncSession) -> None:
        first, second = await _run(session), await _run(session)
        review = await _queued(session, first, second)

        await _decide(session, review, first, Decision.ACCEPTED, by="a")
        assert await self._gates(session) == ("accepted", False, [HOST])

        await _decide(session, review, second, Decision.REJECTED, by="b", when=LATER)
        assert await self._gates(session) == ("rejected", True, [])

        await _decide(session, review, first, Decision.PENDING, by="a", when=LATER)
        assert await self._gates(session) == (None, False, [])

        await _decide(session, review, second, Decision.ACCEPTED, by="b", when=LATER)
        assert await self._gates(session) == ("accepted", False, [HOST])

    async def test_copy_is_not_a_second_opinion(self, session: AsyncSession) -> None:
        """Счёт судьи против человека по всем прогонам: одно решение — один раз."""
        first, second = await _run(session), await _run(session)
        review = await _queued(session, first, second)
        await _decide(session, review, first, Decision.ACCEPTED, by="a")
        await _decide(session, review, second, Decision.ACCEPTED, by="b", when=LATER)

        assert (await review.accuracy()).decided == 1


#: Ревизия, закрывающая строки, оставшиеся с прежних времён.
REVISION = "ee6c0a44cbd4_one_decision_per_domain.py"

#: Домены «как на проде»: решение донора → домен.
BEFORE = {
    "accepted": "taken.example.test",
    "rejected": "brand.example.test",
    None: "open.example.test",
}


def _revision(connection: Connection, *steps: str) -> None:
    """Шаги ревизии (`upgrade`, `downgrade`) на соединении теста — в его транзакции."""
    migration = load_migration(REVISION)
    with Operations.context(MigrationContext.configure(connection)):
        for step in steps:
            getattr(migration, step)()


class TestRowsLeftFromBefore:
    """Ревизия `ee6c0a44cbd4`: ждущая строка домена, у донора которого решение есть,
    становится копией этого решения. Решения, домены без решения и закрытые строки
    она не трогает."""

    async def _as_before(self, session: AsyncSession) -> tuple[RunModel, RunModel]:
        """Как лежит с прежних времён: решение в первом прогоне, домен ждёт во втором."""
        first, second = await _run(session), await _run(session)
        for review, host in BEFORE.items():
            domain = await make_donor(session, host, review=review)
            decided = review is not None
            session.add_all(
                [
                    RunCandidateModel(
                        run_id=first.id,
                        domain_id=domain.id,
                        status=review or Decision.PENDING.value,
                        decided_by="a" if decided else None,
                        decided_at=NOW if decided else None,
                        note="так решили" if decided else None,
                    ),
                    RunCandidateModel(run_id=second.id, domain_id=domain.id),
                ]
            )
        await session.flush()
        return first, second

    async def _rows(self, session: AsyncSession, *runs: RunModel) -> list[Row]:
        return [await _row(session, run, host) for run in runs for host in BEFORE.values()]

    async def test_waiting_rows_of_decided_domains_become_copies(
        self, session: AsyncSession
    ) -> None:
        first, second = await self._as_before(session)
        assert await _to_review(session, second) == 3

        await (await session.connection()).run_sync(_revision, "upgrade")

        assert await self._rows(session, second) == [
            copy_of(Decision.ACCEPTED),
            copy_of(Decision.REJECTED),
            PROPOSED,
        ]
        assert await self._rows(session, first) == [
            ("accepted", False, "a", NOW, "так решили"),
            ("rejected", False, "a", NOW, "так решили"),
            PROPOSED,
        ]
        assert await _to_review(session, second) == 1

    async def test_rerun_and_downgrade_change_nothing(self, session: AsyncSession) -> None:
        """Повтор безопасен: закрытая строка уже не ждёт. Откат пустой."""
        first, second = await self._as_before(session)
        connection = await session.connection()
        await connection.run_sync(_revision, "upgrade")
        once = await self._rows(session, first, second)

        await connection.run_sync(_revision, "upgrade", "downgrade")

        assert await self._rows(session, first, second) == once


async def test_two_decisions_on_one_domain_from_two_runs_wait_instead_of_deadlocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Два человека решают один домен в разных прогонах разом (хвост аудита 11.10.2026).

    Первое решение задержано между своей строкой и копией в чужую. Без общего замка второе
    взяло бы свою строку, оба ждали бы друг друга, и Postgres оборвал бы одно «deadlock
    detected». Под замком второе ждёт первое и ложится после него — побеждает последнее.
    Сессии настоящие, на своих соединениях (`committed_sessions`)."""
    async with committed_sessions() as factory:
        async with factory() as session:
            first, second = await _run(session), await _run(session)
            await _queued(session, first, second)
            await session.commit()
            ids = {run.id: (await _candidate_ids(session, run.id))[HOST] for run in (first, second)}

        gate = asyncio.Event()
        copy = RunReview._copy_elsewhere

        async def held(
            self: RunReview, run_id: int, domains: Sequence[int], decision: Decision
        ) -> int:
            if run_id == first.id:
                await gate.wait()
            return await copy(self, run_id, domains, decision)

        monkeypatch.setattr(RunReview, "_copy_elsewhere", held)

        async def decide(run: RunModel, decision: Decision, by: str) -> None:
            async with factory() as session:
                await RunReview(session).decide(run.id, [ids[run.id]], decision, by=by, now=NOW)
                await session.commit()

        accepting = asyncio.create_task(decide(first, Decision.ACCEPTED, "первый"))
        await asyncio.sleep(0.3)
        rejecting = asyncio.create_task(decide(second, Decision.REJECTED, "второй"))
        await asyncio.sleep(0.3)
        gate.set()
        await asyncio.wait_for(asyncio.gather(accepting, rejecting), timeout=15)

        async with factory() as session:
            assert (await _row(session, second))[:3] == (Decision.REJECTED.value, False, "второй")
            assert await _row(session, first) == copy_of(Decision.REJECTED)
