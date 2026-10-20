"""«Взять на рассмотрение» и решение по домену — под одним замком (ревью 20.10.2026).

«Взять» читал, где домен ждёт решения и решали ли о нём, без замка. Решение того же домена в
другом прогоне ложилось рядом, а два «взять» одного домена из резервов двух прогонов клали
его ждать в обе очереди. Теперь «взять» берёт замок решения по домену до всех проверок
(`domain_lock.lock_domains`) — и сам домен: строк очереди у него может не быть вовсе.

Сессии настоящие, на своих соединениях (`committed_sessions`): замок держит транзакция,
а внешняя транзакция обычного теста сделала бы обе сессии одной.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.review.candidates import Decision, RunReview
from backend.features.review.reserve import Reserve, ReserveRow, TakeReport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_reserve import _domain_id, _run
from tests.test_send_race import committed_sessions

HOST = "far-away.test"
#: Сколько «взять» ждёт замка, прежде чем тест скажет, что он не ждёт.
HELD = 0.3

#: Вторая сессия — запуском: задача заводится, когда первая уже держит замок.
Starter = Callable[[], Awaitable[object]]


async def _take(
    factory: async_sessionmaker[AsyncSession], run: RunModel, domain_id: int
) -> TakeReport:
    async with factory() as session:
        report = await Reserve(session).take([(run.id, domain_id)], by="op@team.example.test")
        await session.commit()
        return report


async def _held_meanwhile(gate: asyncio.Event, first: asyncio.Task[object], second: Starter) -> Any:
    """Первая сессия держит замок у ворот; вторая стартует и через `HELD` ещё должна ждать.
    Ворота открываются в любом случае и обе дожидаются: иначе упавшая проверка оставила бы
    первую сессию с замком, и чистка базы (`committed_sessions`) ждала бы её вечно."""
    waiting: asyncio.Task[object] | None = None
    try:
        await asyncio.sleep(HELD)
        waiting = asyncio.create_task(second())
        await asyncio.sleep(HELD)
        waited = not waiting.done()
    finally:
        gate.set()
        pending = [task for task in (first, waiting) if task is not None]
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=15)
    assert waited, "вторая сессия не ждёт замка первой"
    assert waiting is not None
    first.result()
    return waiting.result()


async def _queued(factory: async_sessionmaker[AsyncSession], domain_id: int) -> list[str]:
    async with factory() as session:
        rows = await session.execute(
            select(RunCandidateModel.status).where(RunCandidateModel.domain_id == domain_id)
        )
        return list(rows.scalars().all())


async def test_a_take_waits_for_a_decision_on_the_same_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Домен ждёт решения в очереди первого прогона и лежит в резерве второго. Человек
    принимает его в первом, а второй тем временем берёт из резерва: «взять» ждёт решение и
    видит его — «уже донор», а не «ждёт решения», прочитанное до замка."""
    async with committed_sessions() as factory:
        async with factory() as session:
            first, second = await _run(session), await _run(session)
            far = await _domain_id(session, HOST)
            await Reserve(session).take([(first.id, far)], by="op@team.example.test")
            await session.commit()
            page = await RunReview(session).page(
                first.id, status=Decision.PENDING, show_doubtful=True
            )
            candidate = next(row.candidate.id for row in page.rows if row.domain.host == HOST)

        gate = asyncio.Event()
        copy = RunReview._copy_elsewhere

        async def held(
            self: RunReview, run_id: int, domains: Sequence[int], decision: Decision
        ) -> int:
            await gate.wait()
            return await copy(self, run_id, domains, decision)

        monkeypatch.setattr(RunReview, "_copy_elsewhere", held)

        async def decide() -> None:
            async with factory() as session:
                review = RunReview(session)
                await review.decide(
                    first.id, [candidate], Decision.ACCEPTED, by="op", now=datetime.now(UTC)
                )
                await session.commit()

        report = await _held_meanwhile(
            gate, asyncio.create_task(decide()), lambda: _take(factory, second, far)
        )

        assert report.refused == {HOST: "уже донор — принят человеком"}
        assert await _queued(factory, far) == [Decision.ACCEPTED.value]


async def test_two_takes_of_one_domain_from_two_runs_lay_it_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Домен в резерве двух прогонов, в очередях его нет. Два человека берут его разом —
    каждый из своего прогона: второй ждёт первого и получает отказ словами, а домен ждёт
    решения в одной очереди, а не в двух."""
    async with committed_sessions() as factory:
        async with factory() as session:
            first, second = await _run(session), await _run(session)
            far = await _domain_id(session, HOST)
            await session.commit()

        gate = asyncio.Event()
        lay = Reserve._lay

        async def held(self: Reserve, row: ReserveRow, *, by: str, moment: datetime) -> bool:
            if row.run_id == first.id:
                await gate.wait()
            return await lay(self, row, by=by, moment=moment)

        monkeypatch.setattr(Reserve, "_lay", held)

        early = asyncio.create_task(_take(factory, first, far))
        refused = await _held_meanwhile(gate, early, lambda: _take(factory, second, far))

        assert early.result().taken == {first.id: [HOST]}
        assert refused.refused == {HOST: f"ждёт решения в очереди прогона №{first.id}"}
        assert await _queued(factory, far) == [Decision.PENDING.value]
