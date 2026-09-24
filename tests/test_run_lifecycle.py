"""Жизнь прогона между нажатием и итогом: строка, живость, разбор мёртвых.

Проверяется то, чего не видно ни в одном зелёном наборе на заглушках:
прогон существует до первой траты, живой отмечается сам, мёртвый
продолжается по сохранённой выдаче и не покупает её второй раз,
а неизвестность про живость не считается смертью.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import RunStatus, Stage
from backend.features.core.models.run import RunModel
from backend.features.donors.repository import DonorRepository
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.lifecycle import (
    MAX_RESUMES,
    RESUME_AFTER_SEC,
    RESUMES_KEY,
    STALE_AFTER_SEC,
    heartbeat,
    recover,
)
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.planning import Candidates
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from backend.shared.queue import last_error_line
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_execute_run import GOOD, FakeSerp, T, _ahrefs


async def _run_row(
    session: AsyncSession,
    *,
    status: RunStatus,
    silent_for: float = 0.0,
    job_id: str | None = "job-1",
    stats: dict[str, object] | None = None,
) -> RunModel:
    """Прогон, который молчит заданное время. Время выставляется отдельным
    запросом: обычная запись сама обновила бы отметку, и «молчащий»
    прогон оказался бы только что живым."""
    repository = RunRepository(session)
    settings = await repository.create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = await repository.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=["crm"],
        country="us",
        status=status,
    )
    run.job_id = job_id
    run.stats = stats
    await session.flush()
    await session.execute(
        update(RunModel)
        .where(RunModel.id == run.id)
        .values(updated_at=datetime.now(UTC) - timedelta(seconds=silent_for))
    )
    await session.refresh(run)
    return run


class Enqueued:
    """Поддельная очередь: помнит, кого в неё положили."""

    def __init__(self, answer: str | None = "job-2") -> None:
        self.calls: list[int] = []
        self._answer = answer

    def __call__(self, run_id: int) -> str | None:
        self.calls.append(run_id)
        return self._answer


class TestRunExistsBeforeItSpends:
    async def test_new_run_is_queued_without_an_estimate(self, session: AsyncSession) -> None:
        """Строка появляется по нажатию. Сметы в ней ещё нет: точное число
        доменов известно только после выдачи, а выдача — уже трата."""
        run = await _run_row(session, status=RunStatus.QUEUED)

        assert run.status is RunStatus.QUEUED
        assert run.estimated_units is None
        assert run.job_id == "job-1"


class TestHeartbeat:
    async def test_beat_moves_the_mark(self, session: AsyncSession) -> None:
        """Удар о жизни двигает отметку, и по ней прогон отличается
        от мёртвого. Без удара медленный прогон выглядит так же."""
        run = await _run_row(session, status=RunStatus.RUNNING, silent_for=600)
        before = run.updated_at

        await RunRepository(session).touch(run.id)
        await session.refresh(run)

        assert run.updated_at > before

    async def test_failed_beat_does_not_kill_the_run(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Сбой записи только логируется: удар не должен ронять прогон,
        ради которого он бьётся."""
        repository = RunRepository(session)

        async def refuse(_run_id: int) -> None:
            raise RuntimeError("база не отвечает")

        monkeypatch.setattr(repository, "touch", refuse)

        beat = asyncio.create_task(heartbeat(repository, 1, interval=0.01))
        await asyncio.sleep(0.05)
        assert not beat.done()
        beat.cancel()


class TestRecovery:
    async def test_dead_run_is_resumed_with_a_new_job(self, session: AsyncSession) -> None:
        run = await _run_row(session, status=RunStatus.RUNNING, silent_for=RESUME_AFTER_SEC + 10)
        enqueue = Enqueued()

        outcome = await recover(RunRepository(session), alive=lambda _: False, enqueue=enqueue)
        await session.refresh(run)

        assert outcome.resumed == [run.id]
        assert enqueue.calls == [run.id]
        assert run.job_id == "job-2"
        assert run.stats[RESUMES_KEY] == 1
        assert run.status is RunStatus.RUNNING

    async def test_unclaimed_queued_run_is_resumed_too(self, session: AsyncSession) -> None:
        """Отличие от соседней системы: там разбор смотрит только
        на «идёт», и задача, которую никто не взял, живёт вечно.
        Ровно это и нашлось в дев-очереди 21.09.2026."""
        run = await _run_row(session, status=RunStatus.QUEUED, silent_for=RESUME_AFTER_SEC + 10)
        enqueue = Enqueued()

        outcome = await recover(RunRepository(session), alive=lambda _: False, enqueue=enqueue)

        assert outcome.resumed == [run.id]

    async def test_live_run_is_left_alone(self, session: AsyncSession) -> None:
        run = await _run_row(session, status=RunStatus.RUNNING, silent_for=RESUME_AFTER_SEC + 10)
        enqueue = Enqueued()

        outcome = await recover(RunRepository(session), alive=lambda _: True, enqueue=enqueue)
        await session.refresh(run)

        assert outcome == outcome.__class__()
        assert enqueue.calls == []
        assert run.job_id == "job-1"

    async def test_recent_run_is_not_touched(self, session: AsyncSession) -> None:
        """Прогон, отметившийся только что, разбору не подлежит, даже
        если его задача считается мёртвой: он ещё бьётся."""
        await _run_row(session, status=RunStatus.RUNNING, silent_for=RESUME_AFTER_SEC / 2)
        enqueue = Enqueued()

        outcome = await recover(RunRepository(session), alive=lambda _: False, enqueue=enqueue)

        assert enqueue.calls == []
        assert outcome.resumed == []

    async def test_unknown_liveness_is_not_death(self, session: AsyncSession) -> None:
        """«Не знаю» — не «мертва»: вторая задача на тот же прогон
        означает второй счёт за метрики."""
        run = await _run_row(session, status=RunStatus.RUNNING, silent_for=STALE_AFTER_SEC + 100)
        enqueue = Enqueued()

        outcome = await recover(RunRepository(session), alive=lambda _: None, enqueue=enqueue)
        await session.refresh(run)

        assert outcome.unknown == [run.id]
        assert enqueue.calls == []
        assert run.status is RunStatus.RUNNING

    async def test_silent_queue_leaves_the_run_for_the_next_pass(
        self, session: AsyncSession
    ) -> None:
        """Очередь не ответила — прогон остаётся неразобранным, а не
        закрытым: закрыть его значит соврать, что работа кончилась."""
        run = await _run_row(session, status=RunStatus.RUNNING, silent_for=RESUME_AFTER_SEC + 10)
        enqueue = Enqueued(answer=None)

        outcome = await recover(RunRepository(session), alive=lambda _: False, enqueue=enqueue)
        await session.refresh(run)

        assert outcome.unknown == [run.id]
        assert run.status is RunStatus.RUNNING
        assert run.job_id == "job-1"

    async def test_hopeless_run_is_closed_with_a_reason(self, session: AsyncSession) -> None:
        """Продолжения исчерпаны и прогон давно молчит — он закрывается,
        и на экране это видно как остановка, а не как вечная работа."""
        run = await _run_row(
            session,
            status=RunStatus.RUNNING,
            silent_for=STALE_AFTER_SEC + 10,
            stats={RESUMES_KEY: MAX_RESUMES, "checked_now": 7},
        )
        enqueue = Enqueued()

        outcome = await recover(RunRepository(session), alive=lambda _: False, enqueue=enqueue)
        await session.refresh(run)

        assert outcome.stopped == [run.id]
        assert run.status is RunStatus.STOPPED
        assert "воркер умер" in run.stats["причина"]
        # То, что прогон успел сделать, из отчёта не пропадает.
        assert run.stats["checked_now"] == 7

    async def test_exhausted_but_fresh_run_waits_instead_of_burial(
        self, session: AsyncSession
    ) -> None:
        """Последняя задача могла успеть начать работу: хоронить рано."""
        run = await _run_row(
            session,
            status=RunStatus.RUNNING,
            silent_for=RESUME_AFTER_SEC + 10,
            stats={RESUMES_KEY: MAX_RESUMES},
        )

        outcome = await recover(RunRepository(session), alive=lambda _: False, enqueue=Enqueued())
        await session.refresh(run)

        assert outcome.stopped == []
        assert run.status is RunStatus.RUNNING

    async def test_broken_counter_does_not_become_an_endless_loop(
        self, session: AsyncSession
    ) -> None:
        """Счётчик продолжений испорчен правкой руками — считаем нулём,
        но громко: молчаливый ноль здесь означает вечное воскрешение."""
        run = await _run_row(
            session,
            status=RunStatus.RUNNING,
            silent_for=RESUME_AFTER_SEC + 10,
            stats={RESUMES_KEY: "много"},
        )
        enqueue = Enqueued()

        await recover(RunRepository(session), alive=lambda _: False, enqueue=enqueue)
        await session.refresh(run)

        assert run.stats[RESUMES_KEY] == 1


class TestSavedSerpIsNotBoughtTwice:
    async def test_resumed_run_reuses_stored_hosts(self, session: AsyncSession) -> None:
        """Главное обещание продолжения. У соседней системы продолжение
        бесплатно, у нас выдача стоит денег — и второй раз за неё
        не платят только потому, что она лежит в строке прогона."""

        class CountingSerp(FakeSerp):
            def __init__(self, urls: list[str]) -> None:
                super().__init__(urls)
                self.searches = 0

            async def search(
                self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
            ) -> dict[str, object]:
                self.searches += 1
                return await super().search(keywords, country, depth_pages=depth_pages)

        serp = CountingSerp(["https://good.com"])
        run = await _run_row(session, status=RunStatus.QUEUED)
        await RunRepository(session).save_candidates(
            run,
            Candidates(
                hosts=["good.com"], keywords=1, results=1, empty_keywords=[], dropped=0
            ).as_dict(),
        )

        await execute_run(
            RunDeps(
                provider=serp,
                client=_ahrefs({"good.com": GOOD}),
                donors=DonorRepository(session),
                runs=RunRepository(session),
                exclusions=Exclusions(session),
            ),
            RunRequest(["crm"], "us", T, run.settings_id, run=run),
        )

        assert serp.searches == 0
        assert run.status is RunStatus.DONE

    async def test_first_attempt_stores_what_it_bought(self, session: AsyncSession) -> None:
        """Выдача фиксируется сразу, а не в конце: следующая смерть
        воркера иначе снова оставит прогон без неё."""
        run = await _run_row(session, status=RunStatus.QUEUED)

        await execute_run(
            RunDeps(
                provider=FakeSerp(["https://good.com"]),
                client=_ahrefs({"good.com": GOOD}),
                donors=DonorRepository(session),
                runs=RunRepository(session),
                exclusions=Exclusions(session),
            ),
            RunRequest(["crm"], "us", T, run.settings_id, run=run),
        )
        await session.refresh(run)

        assert run.candidates["hosts"] == ["good.com"]


class TestFailureIsNamed:
    """Упавшая задача — не умерший воркер. 24.09.2026 прогоны №19 и №20
    падали на MissingGreenlet, а в записи стояло «воркер умер»."""

    TRACE_LINE = "sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called"

    async def test_resume_note_carries_the_exception(self, session: AsyncSession) -> None:
        run = await _run_row(session, status=RunStatus.QUEUED, silent_for=RESUME_AFTER_SEC + 10)

        await recover(
            RunRepository(session),
            alive=lambda _: False,
            enqueue=Enqueued(),
            failure=lambda _: self.TRACE_LINE,
        )
        await session.refresh(run)

        assert f"задача упала: {self.TRACE_LINE}" in run.stats["причина"]
        assert "воркер умер" not in run.stats["причина"]

    async def test_stop_reason_carries_the_exception(self, session: AsyncSession) -> None:
        run = await _run_row(
            session,
            status=RunStatus.QUEUED,
            silent_for=STALE_AFTER_SEC + 10,
            stats={RESUMES_KEY: MAX_RESUMES},
        )

        await recover(
            RunRepository(session),
            alive=lambda _: False,
            enqueue=Enqueued(),
            failure=lambda _: self.TRACE_LINE,
        )
        await session.refresh(run)

        assert run.status is RunStatus.STOPPED
        assert self.TRACE_LINE in run.stats["причина"]

    async def test_dead_worker_is_still_called_so(self, session: AsyncSession) -> None:
        """Нет исключения — значит, правда умер процесс: причина прежняя."""
        run = await _run_row(session, status=RunStatus.RUNNING, silent_for=RESUME_AFTER_SEC + 10)

        await recover(
            RunRepository(session),
            alive=lambda _: False,
            enqueue=Enqueued(),
            failure=lambda _: None,
        )
        await session.refresh(run)

        assert "воркер умер" in run.stats["причина"]


def test_last_error_line_is_the_exception_itself() -> None:
    trace = (
        "Traceback (most recent call last):\n"
        '  File "/app/backend/workers/jobs.py", line 70, in _run\n'
        "    cap=run.settings.units_cap,\n"
        "sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called\n\n"
    )
    assert last_error_line(trace) == (
        "sqlalchemy.exc.MissingGreenlet: greenlet_spawn has not been called"
    )
    assert last_error_line("") is None
    assert last_error_line(None) is None
    assert len(last_error_line("x" * 500) or "") == 200
