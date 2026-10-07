"""Процесс разбора мёртвых: один проход — прогоны Этапа 1 и обходы Этапа 2.

Правила разбора проверены в своих файлах (`test_run_lifecycle.py`,
`test_crawl_lifecycle.py`); здесь — проводка процесса: своя база, очередь
и то, что отказ очереди не роняет проход. Очередь подменена.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.config import storage
from backend.features.core.domain import CrawlStatus
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl.repository import queue_crawl
from backend.workers import reaper
from sqlalchemy import text
from tests.conftest import TEST_DSN
from tests.test_send_race import committed_sessions


@pytest.fixture(autouse=True)
def _base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)


async def test_sweep_resumes_a_dead_crawl(monkeypatch: pytest.MonkeyPatch) -> None:
    resumed: list[int] = []
    monkeypatch.setattr(reaper, "job_alive", lambda _job: False)
    monkeypatch.setattr(reaper, "job_failure", lambda _job: None)
    monkeypatch.setattr(
        reaper, "enqueue_crawl", lambda run_id: resumed.append(run_id) or f"next-{run_id}"
    )

    async with committed_sessions() as factory:
        async with factory() as session:
            run = await queue_crawl(session, "dead.example.test", by=None)
            run.status, run.job_id = CrawlStatus.RUNNING, "gone"
            await session.commit()
            await session.execute(
                text("UPDATE crawl_runs SET updated_at = now() - interval '10 minutes'")
            )
            await session.commit()

        await reaper.sweep()

        async with factory() as session:
            after = await session.get(CrawlRunModel, run.id)
    assert resumed == [run.id]
    assert after is not None
    assert (after.status, after.job_id, after.resumes) == (
        CrawlStatus.QUEUED,
        f"next-{run.id}",
        1,
    )


def test_silent_queue_is_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_: Any, **__: Any) -> str:
        raise ConnectionError("Redis недоступен")

    class _Queue:
        enqueue = staticmethod(refuse)

    monkeypatch.setattr(reaper, "enqueue_crawl", refuse)
    monkeypatch.setattr(reaper, "runs_queue", _Queue)

    assert reaper._enqueue_crawl(5) is None
    assert reaper._enqueue(5) is None


async def test_watch_reports_silence_and_tells_the_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[object] = []
    told: list[object] = []

    async def report(session: object) -> list[str]:
        seen.append(session)
        return ["тревога"]

    async def tell(found: object) -> None:
        told.append(found)

    monkeypatch.setattr(reaper, "silence_report", report)
    monkeypatch.setattr(reaper.FEED, "tell", tell)

    async with committed_sessions():
        await reaper.watch()

    assert (len(seen), told) == (1, [["тревога"]])


def test_main_runs_both_loops(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[str] = []

    async def every(_interval: float, _work: Any, *, name: str) -> None:
        started.append(name)

    monkeypatch.setattr(reaper, "every", every)
    monkeypatch.setattr(reaper, "check_storage", lambda: None)

    reaper.main()

    assert started == ["Разбор мёртвых прогонов", "Сторож тишины"]
