"""Разбор мёртвых обходов: продолжить осиротевших, закрыть безнадёжных.

Против настоящей базы; «сейчас» передаётся явно — молчание обхода задаётся
временем прохода, а не ожиданием.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import CrawlStatus
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl import lifecycle
from backend.features.crawl.repository import queue_crawl
from backend.features.runs.lifecycle import MAX_RESUMES, RESUME_AFTER_SEC, STALE_AFTER_SEC
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_send_race import committed_sessions

pytestmark = pytest.mark.asyncio


def _later(seconds: float) -> datetime:
    return datetime.now(UTC) + timedelta(seconds=seconds)


class _Alerts:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def __call__(self, text: str) -> bool:
        self.sent.append(text)
        return True


@pytest.fixture
def alerts(monkeypatch: pytest.MonkeyPatch) -> _Alerts:
    sent = _Alerts()
    monkeypatch.setattr(lifecycle, "send_alert", sent)
    return sent


async def _running(session: AsyncSession, host: str, *, resumes: int = 0) -> CrawlRunModel:
    run = await queue_crawl(session, host, by=None)
    run.status, run.job_id, run.resumes = CrawlStatus.RUNNING, f"job-{host}", resumes
    await session.flush()
    return run


async def test_dead_crawl_is_resumed_from_its_checkpoint(
    session: AsyncSession, alerts: _Alerts
) -> None:
    run = await _running(session, "dead.example.test")

    outcome = await lifecycle.recover(
        session,
        alive=lambda _job: False,
        enqueue=lambda run_id: f"next-{run_id}",
        failure=lambda _job: "RuntimeError: страница не разобралась",
        now=_later(RESUME_AFTER_SEC + 5),
    )

    await session.refresh(run)
    assert outcome.resumed == [run.id]
    assert (run.status, run.job_id, run.resumes) == (CrawlStatus.QUEUED, f"next-{run.id}", 1)
    assert run.stats is not None
    assert "обход продолжен с чекпоинта" in run.stats["reason"]
    assert run.stats["failure"] == "RuntimeError: страница не разобралась"
    assert alerts.sent == []


async def test_killed_task_is_named_in_words(session: AsyncSession, alerts: _Alerts) -> None:
    """Нехватка памяти убивает процесс задачи — так и написать, а не «техническая
    ошибка» (так выглядел живой обход, убитый `kill -9`)."""
    run = await _running(session, "oom.example.test")

    await lifecycle.recover(
        session,
        alive=lambda _job: False,
        enqueue=lambda run_id: f"next-{run_id}",
        failure=lambda _job: "Work-horse terminated unexpectedly; waitpid returned 9 (signal 9);",
        now=_later(RESUME_AFTER_SEC + 5),
    )

    assert run.stats is not None
    assert run.stats["reason"].startswith("процесс задачи убит (нехватка памяти")
    assert "signal 9" in run.stats["failure"]


async def test_alive_quiet_and_unknown_are_left_alone(
    session: AsyncSession, alerts: _Alerts
) -> None:
    alive = await _running(session, "alive.example.test")
    unknown = await _running(session, "unknown.example.test")
    verdicts = {alive.job_id: True, unknown.job_id: None}

    outcome = await lifecycle.recover(
        session,
        alive=lambda job: verdicts[job],
        enqueue=lambda _run_id: "лишняя",
        now=_later(RESUME_AFTER_SEC + 5),
    )

    assert outcome.resumed == []
    assert outcome.stopped == []
    assert outcome.unknown == [unknown.id]
    assert {alive.status, unknown.status} == {CrawlStatus.RUNNING}


async def test_recent_silence_is_not_death(session: AsyncSession, alerts: _Alerts) -> None:
    await _running(session, "slow.example.test")

    outcome = await lifecycle.recover(
        session, alive=lambda _job: False, enqueue=lambda _run_id: "x", now=_later(10)
    )

    assert outcome.resumed == []


async def test_queue_silence_is_not_a_resume(session: AsyncSession, alerts: _Alerts) -> None:
    """Очередь не ответила — обход остаётся как был: следующий проход спросит снова."""
    run = await _running(session, "noqueue.example.test")

    outcome = await lifecycle.recover(
        session, alive=lambda _job: False, enqueue=lambda _run_id: None, now=_later(200)
    )

    assert outcome.unknown == [run.id]
    assert run.resumes == 0


async def test_out_of_resumes_waits_then_stops_with_an_alert(
    session: AsyncSession, alerts: _Alerts
) -> None:
    run = await _running(session, "hopeless.example.test", resumes=MAX_RESUMES)

    early = await lifecycle.recover(
        session, alive=lambda _job: False, enqueue=lambda _run_id: "x", now=_later(200)
    )
    assert early.stopped == []

    late = await lifecycle.recover(
        session,
        alive=lambda _job: False,
        enqueue=lambda _run_id: "x",
        now=_later(STALE_AFTER_SEC + 5),
    )

    await session.refresh(run)
    assert late.stopped == [run.id]
    assert run.status is CrawlStatus.STOPPED
    assert run.finished_at is not None
    assert run.stats is not None
    assert "смотрит человек" in run.stats["reason"]
    assert "воркер умер" in run.stats["reason"]
    assert len(alerts.sent) == 1
    assert alerts.sent[0].startswith(f"обход hopeless.example.test (№{run.id}) остановлен")


async def test_finished_crawls_are_not_looked_at(session: AsyncSession, alerts: _Alerts) -> None:
    run = await _running(session, "done.example.test")
    run.status = CrawlStatus.DONE
    await session.flush()

    outcome = await lifecycle.recover(
        session, alive=lambda _job: False, enqueue=lambda _run_id: "x", now=_later(5_000)
    )

    assert outcome.resumed == []
    assert outcome.stopped == []
    assert outcome.unknown == []


async def test_heartbeat_moves_the_mark_and_survives_a_failure() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            run = await queue_crawl(session, "beat.example.test", by=None)
            await session.commit()
            before = run.updated_at

        beat = asyncio.create_task(lifecycle.heartbeat(factory, run.id, interval=0.05))
        await asyncio.sleep(0.3)
        beat.cancel()
        with pytest.raises(asyncio.CancelledError):
            await beat

        async with factory() as session:
            after = await session.scalar(
                select(CrawlRunModel.updated_at).where(CrawlRunModel.id == run.id)
            )
    assert after is not None
    assert after > before


async def test_heartbeat_failure_is_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    class _Broken:
        def __call__(self) -> _Broken:
            return self

        async def __aenter__(self) -> None:
            raise RuntimeError("база легла")

        async def __aexit__(self, *_: object) -> None:
            return None

    beat = asyncio.create_task(lifecycle.heartbeat(_Broken(), 7, interval=0.01))  # type: ignore[arg-type]
    await asyncio.sleep(0.1)
    beat.cancel()
    with pytest.raises(asyncio.CancelledError):
        await beat

    assert "Обход 7: отметка о жизни не записалась" in caplog.text
