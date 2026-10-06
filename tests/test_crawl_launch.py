"""Постановка обходов: строка до задачи, замок донора, отказ очереди.

Порядок общий у кнопки и консоли (`crawl/launch.py`); здесь он проверяется
на настоящей базе, а очередь подменена — живую постановку забрали бы старые
воркеры на машине разработчика.
"""

from __future__ import annotations

import argparse

import pytest
from backend.cli import crawl_probe
from backend.config import storage
from backend.features.core.domain import CrawlStatus
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl.launch import launch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN
from tests.test_send_race import committed_sessions


class _Enqueue:
    """Очередь в памяти; `broken` — обходы, на которых она не отвечает."""

    def __init__(self, *, broken: frozenset[int] = frozenset()) -> None:
        self.broken = broken
        self.queued: list[tuple[int, str]] = []

    def __call__(self, run_id: int, job_id: str) -> None:
        if run_id in self.broken:
            raise ConnectionError("Redis недоступен")
        self.queued.append((run_id, job_id))


def _ids(run_id: int) -> str:
    return f"crawl-{run_id}-test"


async def _rows(session: AsyncSession) -> dict[str, CrawlRunModel]:
    rows = (await session.execute(select(CrawlRunModel))).scalars().all()
    return {f"{row.host}#{row.id}": row for row in rows}


async def test_each_donor_once_with_its_job_number_written_first() -> None:
    async with committed_sessions() as factory:
        enqueue = _Enqueue()
        async with factory() as session:
            done = await launch(
                session,
                ["WWW.One.example", "one.example", " two.example ", ""],
                by="op@t.test",
                job_id=_ids,
                enqueue=enqueue,
            )
        # Строка видна другому соединению — то есть зафиксирована.
        async with factory() as other:
            rows = (await other.execute(select(CrawlRunModel))).scalars().all()

    assert list(done.queued) == ["one.example", "two.example"]
    assert done.busy == []
    assert done.failed == []
    assert sorted((row.host, row.status, row.job_id) for row in rows) == [
        ("one.example", CrawlStatus.QUEUED, _ids(done.queued["one.example"])),
        ("two.example", CrawlStatus.QUEUED, _ids(done.queued["two.example"])),
    ]
    assert enqueue.queued == [(run_id, _ids(run_id)) for run_id in done.queued.values()]
    assert {row.requested_by for row in rows} == {"op@t.test"}


async def test_busy_donor_is_named_not_doubled() -> None:
    async with committed_sessions() as factory, factory() as session:
        await launch(session, ["one.example"], by=None, job_id=_ids, enqueue=_Enqueue())
        again = await launch(
            session, ["one.example", "two.example"], by=None, job_id=_ids, enqueue=_Enqueue()
        )

    assert again.busy == ["one.example"]
    assert list(again.queued) == ["two.example"]


async def test_silent_queue_closes_the_crawl_and_frees_the_donor() -> None:
    """Очередь не ответила: обход не висит «в очереди» без задачи, а закрыт
    с причиной — и донора можно поставить снова."""
    async with committed_sessions() as factory, factory() as session:
        refused = await launch(
            session,
            ["one.example"],
            by=None,
            job_id=_ids,
            enqueue=_Enqueue(broken=frozenset({1})),
        )
        retried = await launch(session, ["one.example"], by=None, job_id=_ids, enqueue=_Enqueue())
        rows = await _rows(session)

    assert refused.failed == ["one.example"]
    assert refused.queued == {}
    first = rows["one.example#1"]
    assert first.status is CrawlStatus.STOPPED
    assert first.stats is not None
    assert first.stats["reason"].startswith("не поставлен: очередь не ответила")
    assert list(retried.queued) == ["one.example"]


async def test_console_queue_uses_the_same_order(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    queued: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        crawl_probe, "enqueue_crawl", lambda run_id, job_id=None: queued.append((run_id, job_id))
    )
    monkeypatch.setattr(crawl_probe, "workers_alive", lambda **_: 0)
    args = argparse.Namespace(domains=["one.example", "one.example"], from_base=None, queue=True)

    async with committed_sessions():
        first = await crawl_probe.cmd_crawl(args)
        second = await crawl_probe.cmd_crawl(args)

    out = capsys.readouterr().out
    assert (first, second) == (0, crawl_probe.EXIT_NOTHING_CRAWLED)
    assert len(queued) == 1
    assert "one.example: обход №1 в очереди" in out
    assert "обходчиков нет" in out
    assert "one.example: обход уже идёт или ждёт" in out


async def test_console_queue_says_when_the_queue_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)

    def refuse(run_id: int, job_id: str | None = None) -> str:
        raise ConnectionError("Redis недоступен")

    monkeypatch.setattr(crawl_probe, "enqueue_crawl", refuse)
    args = argparse.Namespace(domains=["one.example"], from_base=None, queue=True)

    async with committed_sessions():
        code = await crawl_probe.cmd_crawl(args)

    assert code == crawl_probe.EXIT_NOTHING_CRAWLED
    assert "очередь не ответила" in capsys.readouterr().out
