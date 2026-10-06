"""Задачи Этапа 2: обход донора и пересчёт кандидатов — телом задачи.

Задачи открывают свою базу, поэтому здесь настоящие фиксации и чистка после
(`committed_sessions`). Очередь подменена: живую постановку забрали бы старые
воркеры на машине разработчика. Сайт поддельный (`FakeSite`), порядок настоящий.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from backend.config import ahrefs as ahrefs_cfg
from backend.config import crawl as crawl_cfg
from backend.config import storage
from backend.features.core.domain import CrawlOutcome, CrawlStatus
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.crawl.repository import queue_crawl
from backend.features.crawl.walk import DonorCrawler
from backend.shared.queue import JUDGE_CRAWL_JOB
from backend.workers import crawl_jobs
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import TEST_DSN
from tests.test_advertisers_cli import _FakeAhrefs
from tests.test_crawl_progress import MAP_ROBOTS, _article
from tests.test_crawl_walk import HOST, FakeSite, _page, _urlset
from tests.test_send_race import committed_sessions


class _Queue:
    """Очередь в памяти: что и под каким номером поставлено."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def enqueue(self, path: str, *args: Any, **kwargs: Any) -> Any:
        self.jobs.append((path, args, kwargs))
        return type("Job", (), {"id": f"job-{len(self.jobs)}"})()


@pytest.fixture(autouse=True)
def _wiring(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(crawl_cfg, "DELAY_SEC", 0.0)
    monkeypatch.setattr(crawl_cfg, "BROWSER_ENABLED", False)
    crawl_jobs._STOP.clear()
    yield
    crawl_jobs._STOP.clear()


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch) -> _Queue:
    queue = _Queue()
    monkeypatch.setattr(crawl_jobs, "runs_queue", lambda: queue)
    return queue


@pytest.fixture
def continued(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str | None]]:
    calls: list[tuple[int, str | None]] = []

    def enqueue(run_id: int, job_id: str | None = None) -> str:
        calls.append((run_id, job_id))
        return job_id or "x"

    monkeypatch.setattr(crawl_jobs, "enqueue_crawl", enqueue)
    return calls


def _site(monkeypatch: pytest.MonkeyPatch, count: int = 3) -> FakeSite:
    pages = {f"/p{n}": _article(f"https://adv{n}.example/offer") for n in range(count)}
    pages["/"] = _page("https://home-adv.example/")
    site = FakeSite(
        pages, robots=MAP_ROBOTS, sitemap=_urlset(*[f"https://{HOST}/p{n}" for n in range(count)])
    )

    def client(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(site.handler))

    monkeypatch.setattr(crawl_jobs, "guarded_client", client)
    return site


async def _queued(factory: async_sessionmaker[AsyncSession], host: str = HOST) -> int:
    async with factory() as session:
        run = await queue_crawl(session, host, by="op@t.test")
        run.job_id = "first"
        await session.commit()
        return run.id


async def _row(factory: async_sessionmaker[AsyncSession], run_id: int) -> CrawlRunModel:
    async with factory() as session:
        run = await session.get(CrawlRunModel, run_id)
        assert run is not None
        return run


async def _links(factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with factory() as session:
        return list((await session.execute(select(OutLinkModel.url))).scalars().all())


async def test_crawl_runs_to_the_end_and_hands_over_to_the_judge(
    monkeypatch: pytest.MonkeyPatch, runs: _Queue
) -> None:
    _site(monkeypatch)
    async with committed_sessions() as factory:
        run_id = await _queued(factory)

        done = await crawl_jobs._crawl(run_id)

        run = await _row(factory, run_id)
        links = await _links(factory)
    assert done == {
        "crawl": run_id,
        "host": HOST,
        "outcome": "ok",
        "pages": 4,
        "links": 4,
        "judge_job": "job-1",
    }
    assert (run.status, run.outcome, run.checkpoint) == (CrawlStatus.DONE, CrawlOutcome.OK, None)
    assert run.stats is not None
    assert run.stats["links_found"] == 4
    assert sorted(links) == sorted(
        ["https://home-adv.example/", *[f"https://adv{n}.example/offer" for n in range(3)]]
    )
    assert runs.jobs[0][:2] == (JUDGE_CRAWL_JOB, (run_id,))


async def test_stop_request_requeues_and_the_next_job_finishes(
    monkeypatch: pytest.MonkeyPatch, runs: _Queue, continued: list[tuple[int, str | None]]
) -> None:
    """Выкатка посреди обхода: пачка дописана, продолжение поставлено, и вторая
    задача доводит обход до конца без двойных ссылок."""
    site = _site(monkeypatch)
    async with committed_sessions() as factory:
        run_id = await _queued(factory)
        crawl_jobs.ask_to_stop()

        first = await crawl_jobs._crawl(run_id)
        paused = await _row(factory, run_id)
        crawl_jobs._STOP.clear()
        second = await crawl_jobs._crawl(run_id)

        run = await _row(factory, run_id)
        links = await _links(factory)
    assert first["interrupted"] is True
    assert first["pages"] == 1
    assert continued == [(run_id, first["next_job"])]
    assert paused.status is CrawlStatus.QUEUED
    assert paused.job_id == first["next_job"]
    assert paused.stats is not None
    assert "продолжится" in paused.stats["reason"]
    assert second["outcome"] == "ok"
    assert run.status is CrawlStatus.DONE
    assert len(links) == len(set(links)) == 4
    assert site.asked.count("/p0") == 1


async def test_failure_is_written_to_the_crawl_and_raised(
    monkeypatch: pytest.MonkeyPatch, runs: _Queue
) -> None:
    _site(monkeypatch)

    async def broken(self: DonorCrawler, host: str, progress: object = None) -> None:
        raise RuntimeError("selectolax упал на странице")

    monkeypatch.setattr(DonorCrawler, "crawl", broken)
    async with committed_sessions() as factory:
        run_id = await _queued(factory)

        with pytest.raises(RuntimeError, match="selectolax"):
            await crawl_jobs._crawl(run_id)

        run = await _row(factory, run_id)
    assert run.status is CrawlStatus.RUNNING, "разбор мёртвых продолжит его"
    assert run.stats is not None
    assert run.stats["reason"].startswith("сбой, будет продолжен")
    assert "RuntimeError" in run.stats["failure"]
    assert runs.jobs == []


async def test_finished_and_missing_crawls_are_left_alone(runs: _Queue) -> None:
    async with committed_sessions() as factory:
        run_id = await _queued(factory)
        async with factory() as session:
            run = await session.get(CrawlRunModel, run_id)
            assert run is not None
            run.status = CrawlStatus.STOPPED
            await session.commit()

        stopped = await crawl_jobs._crawl(run_id)
        missing = await crawl_jobs._crawl(run_id + 100)

    assert stopped == {"crawl": run_id, "skipped": "stopped"}
    assert missing == {"crawl": run_id + 100, "missing": True}


async def test_judge_counts_candidates_and_records_the_dr_spend(
    monkeypatch: pytest.MonkeyPatch, runs: _Queue
) -> None:
    site = _site(monkeypatch)
    site.pages["/p0"] = _article("https://marketplace.example/")
    site.pages["/p0"] = site.pages["/p0"].replace('href="', 'rel="sponsored" href="')
    monkeypatch.setattr(ahrefs_cfg, "API_KEY", "test-key")
    monkeypatch.setattr(crawl_jobs, "AhrefsClient", _FakeAhrefs)
    _FakeAhrefs.asked = []
    async with committed_sessions() as factory:
        run_id = await _queued(factory)
        await crawl_jobs._crawl(run_id)

        judged = await crawl_jobs._judge(run_id)

        async with factory() as session:
            candidates = (await session.execute(select(func.count(CandidateModel.id)))).scalar()
            spent = (await session.execute(select(UsageRecordModel))).scalars().all()
    assert judged["crawl"] == run_id
    assert judged["candidates"] == candidates == 4
    assert judged["by_verdict"].get("blocked") == 1, "marketplace — DR 96 > 80"
    assert _FakeAhrefs.asked == [["marketplace.example"]]
    assert [(row.operation, row.units) for row in spent] == [("batch_metrics", 50)]


def test_job_wrappers_set_up_the_process(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def crawl(run_id: int) -> dict[str, Any]:
        calls.append(f"crawl {run_id}")
        return {"crawl": run_id}

    async def judge(run_id: int) -> dict[str, Any]:
        calls.append(f"judge {run_id}")
        return {"crawl": run_id}

    monkeypatch.setattr(crawl_jobs, "_crawl", crawl)
    monkeypatch.setattr(crawl_jobs, "_judge", judge)

    assert crawl_jobs.crawl_donor(5) == {"crawl": 5}
    assert crawl_jobs.judge_crawl(6) == {"crawl": 6}
    assert calls == ["crawl 5", "judge 6"]


def test_stop_flag_is_per_process_and_starts_clean() -> None:
    assert crawl_jobs.stop_requested() is False
    crawl_jobs.ask_to_stop(15, None)
    assert crawl_jobs.stop_requested() is True
