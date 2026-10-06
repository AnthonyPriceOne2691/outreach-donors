"""Сервис обходов: просьба остановиться доходит до задачи.

Настоящий воркер rq здесь не поднимается (в CI нет Redis), проверяется то,
что мы поменяли в нём: обработчики сигналов процесса задачи и передачу
просьбы докера. Живьём — выкаткой с идущим обходом.
"""

from __future__ import annotations

import logging
import signal
from collections.abc import Iterator
from typing import Any

import pytest
from backend.workers import crawl_jobs, crawler


@pytest.fixture
def signals() -> Iterator[None]:
    """Обработчики процесса pytest возвращаются на место."""
    saved = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)
    crawl_jobs._STOP.clear()


def _worker(horse: int) -> crawler.CrawlWorker:
    worker = crawler.CrawlWorker.__new__(crawler.CrawlWorker)
    worker._horse_pid = horse
    worker.log = logging.getLogger("rq.worker")
    return worker


def test_task_process_takes_sigterm_as_stop_not_death(signals: None) -> None:
    _worker(0).setup_work_horse_signals()

    assert signal.getsignal(signal.SIGINT) is signal.SIG_IGN
    assert signal.getsignal(signal.SIGTERM) is crawl_jobs.ask_to_stop
    signal.raise_signal(signal.SIGTERM)
    assert crawl_jobs.stop_requested() is True


def test_shutdown_request_is_passed_to_the_task(monkeypatch: pytest.MonkeyPatch) -> None:
    killed: list[tuple[int, int]] = []
    monkeypatch.setattr(crawler.os, "kill", lambda pid, sig: killed.append((pid, sig)))

    _worker(4242).handle_warm_shutdown_request()
    _worker(0).handle_warm_shutdown_request()

    assert killed == [(4242, signal.SIGTERM)]


def test_main_listens_to_the_crawl_queue_without_a_scheduler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: dict[str, Any] = {}

    class _Worker:
        def __init__(self, queues: list[Any], *, connection: Any) -> None:
            started["queues"] = [queue.name for queue in queues]

        def work(self, **kwargs: Any) -> None:
            started.update(kwargs)

    monkeypatch.setattr(crawler, "CrawlWorker", _Worker)
    monkeypatch.setattr(crawler, "check_storage", lambda: None)

    crawler.main()

    assert started == {"queues": ["crawl"], "with_scheduler": False}
