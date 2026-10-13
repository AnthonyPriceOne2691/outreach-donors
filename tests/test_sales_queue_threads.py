"""Задачи rq из async-путей продаж ставятся и читаются в потоке, а не в цикле событий сервера.

Клиент Redis синхронный: вызов в цикле событий держал бы, пока Redis думает (до
`REDIS_TIMEOUT_SEC`), все запросы сервиса, а не только этот, — как у сборки писем
(`api/letters/routes._enqueue_once`). Сборка очереди, очистка, сбор базы и его продолжение,
строка автосборки на экране очереди, проход автосборки и крючки агента продаж («Написать
заново» зовёт их из общего маршрута переписки) — через `asyncio.to_thread`. Отказ Redis — 503
словами, как был: поток пробрасывает исключение как есть.

Очередь — подделка с правилами rq (`test_sales_clean_api._Jobs`), которая помнит поток каждого
вызова. Тексты и адреса выдуманы.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.api.errors import QUEUE_DOWN
from backend.config import sales as sales_cfg
from backend.features.agent.stages import DraftNotice
from backend.features.core.domain import DraftStatus, Stage
from backend.features.core.models.access import UserModel
from backend.features.sales import autobuild
from backend.features.sales.agent import handover, moves, notify, parts
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.queue_start import build_job_id
from backend.features.sales.segment_models import SalesSegmentRunModel
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_autobuild import _AutoJobs
from tests.test_sales_clean_api import _Job, _Jobs, waiting
from tests.test_sales_segment_estimate import KEYWORDS, _settings
from tests.test_sales_segment_run import hypothesis, make_run

__all__ = ["_settings", "waiting"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"


class _Threads(_AutoJobs):
    """Очередь с правилами rq и метой задачи (`test_sales_autobuild._AutoJobs`), которая помнит,
    в каком потоке её звали."""

    def __init__(self) -> None:
        super().__init__()
        self.threads: list[int] = []

    def fetch_job(self, job_id: str) -> _Job | None:
        self.threads.append(threading.get_ident())
        return super().fetch_job(job_id)

    def enqueue(self, *args: object, **kwargs: object) -> _Job:
        self.threads.append(threading.get_ident())
        return super().enqueue(*args, **kwargs)


class _Down(_Jobs):
    """Redis не отвечает."""

    def fetch_job(self, job_id: str) -> _Job | None:
        raise RedisConnectionError("очередь не отвечает")

    def enqueue(self, *args: object, **kwargs: object) -> _Job:
        raise RedisConnectionError("очередь не отвечает")


class _Placed:
    """Очередь продаж крючков агента: помнит путь задачи и поток вызова."""

    def __init__(self) -> None:
        self.paths: list[str] = []
        self.threads: list[int] = []

    def enqueue(self, path: str, *_args: object, **_options: object) -> None:
        self.threads.append(threading.get_ident())
        self.paths.append(path)


def _off_the_loop(jobs: _Threads | _Placed) -> None:
    """Каждый вызов очереди — не в потоке цикла событий (этот тест идёт в нём)."""
    assert jobs.threads, "очередь не звали"
    assert threading.get_ident() not in jobs.threads, "вызов Redis — в потоке цикла событий"


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Продавец с правами запуска и отправки: сборка — `sales` и `send`, очистка и сбор — `run`."""
    await make_user(SELLER, permissions={"send": True})
    return bearer(await sign_in(SELLER))


def _queue_of(monkeypatch: pytest.MonkeyPatch, route: str, jobs: _Jobs) -> None:
    monkeypatch.setattr(f"backend.api.sales.{route}.sales_bulk_queue", lambda: jobs)


# --- сборка очереди и строка автосборки ---------------------------------------------------------


async def test_build_is_queued_off_the_event_loop(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await w.world(session, monkeypatch)
    jobs = _Threads()
    _queue_of(monkeypatch, "queue", jobs)

    response = await client.post(
        "/api/sales/queue", json={"hypothesis_id": world.hypothesis_id}, headers=headers
    )

    assert response.status_code == 200, response.text
    _off_the_loop(jobs)


async def test_build_with_redis_down_is_503_in_words(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await w.world(session, monkeypatch)
    _queue_of(monkeypatch, "queue", _Down())

    response = await client.post(
        "/api/sales/queue", json={"hypothesis_id": world.hypothesis_id}, headers=headers
    )

    assert (response.status_code, response.json()) == (503, {"detail": QUEUE_DOWN})


async def test_queue_screen_reads_the_autobuild_jobs_off_the_event_loop(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Строка автосборки читает задачи сборок (идёт ли, встала ли на серии, что собрала
    последняя сама) — тоже в потоке. Проход автосборки ставит сборку — тоже."""
    world = await w.world(session, monkeypatch)
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    await session.commit()
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", True)
    jobs = _Threads()
    _queue_of(monkeypatch, "queue", jobs)

    await autobuild.run(session, jobs, now=datetime.now(UTC))
    _off_the_loop(jobs)
    jobs.threads.clear()
    jobs.known[build_job_id(world.hypothesis_id)].finish({"prepared": 3, "refreshed": 0})
    response = await client.get(
        f"/api/sales/queue?hypothesis={world.hypothesis_id}", headers=headers
    )

    assert response.status_code == 200, response.text
    assert str(response.json()["autobuild"]["line"]).endswith(": собрано 3 письма.")
    _off_the_loop(jobs)


# --- очистка ------------------------------------------------------------------------------------


async def test_clean_is_queued_off_the_event_loop(
    client: AsyncClient,
    headers: dict[str, str],
    waiting: SalesHypothesisModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = _Threads()
    _queue_of(monkeypatch, "clean", jobs)

    response = await client.post(
        "/api/sales/clean", json={"hypothesis_id": waiting.id}, headers=headers
    )

    assert response.status_code == 202, response.text
    _off_the_loop(jobs)


async def test_clean_with_redis_down_is_503_in_words(
    client: AsyncClient,
    headers: dict[str, str],
    waiting: SalesHypothesisModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue_of(monkeypatch, "clean", _Down())

    response = await client.post(
        "/api/sales/clean", json={"hypothesis_id": waiting.id}, headers=headers
    )

    assert (response.status_code, response.json()) == (503, {"detail": QUEUE_DOWN})


# --- сбор базы и его продолжение ----------------------------------------------------------------


def _ask(hypothesis_id: int) -> dict[str, Any]:
    return {"hypothesis_id": hypothesis_id, "keywords": KEYWORDS, "country": "us"}


async def test_segment_run_and_its_resume_are_queued_off_the_event_loop(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    jobs = _Threads()
    _queue_of(monkeypatch, "segment", jobs)
    owner = await hypothesis(session)
    await session.commit()

    started = await client.post("/api/sales/segment/runs", json=_ask(owner.id), headers=headers)
    _off_the_loop(jobs)
    jobs.threads.clear()
    run = await make_run(session, owner)
    resumed = await client.post(f"/api/sales/segment/runs/{run.id}/resume", headers=headers)

    assert (started.status_code, resumed.status_code) == (202, 202), started.text + resumed.text
    _off_the_loop(jobs)


async def test_segment_run_and_resume_with_redis_down_are_503_and_leave_no_run(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _queue_of(monkeypatch, "segment", _Down())
    owner = await hypothesis(session)
    await session.commit()

    started = await client.post("/api/sales/segment/runs", json=_ask(owner.id), headers=headers)
    runs = await session.scalar(select(func.count()).select_from(SalesSegmentRunModel))
    run = await make_run(session, owner)
    resumed = await client.post(f"/api/sales/segment/runs/{run.id}/resume", headers=headers)

    assert (started.status_code, started.json()) == (503, {"detail": QUEUE_DOWN})
    assert runs == 0, "прогон без задачи остался «в очереди»"
    assert (resumed.status_code, resumed.json()) == (503, {"detail": QUEUE_DOWN})


# --- крючки агента продаж -----------------------------------------------------------------------


def _notice(status: DraftStatus, reason: str | None = None) -> DraftNotice:
    return DraftNotice(
        draft_id=3, reply_id=5, thread_id=7, stage=Stage.SALES, status=status, reason=reason
    )


async def test_agent_hooks_queue_off_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сообщение о черновике и передача лида по пропуску «хочет говорить» — задачами очереди
    продаж; крючки зовёт и общий маршрут «Написать заново», в запросе сервера."""
    jobs = _Placed()
    monkeypatch.setattr(notify, "sales_queue", lambda: jobs)
    monkeypatch.setattr(handover, "sales_queue", lambda: jobs)

    await parts.on_draft(_notice(DraftStatus.DRAFTED))
    await parts.on_skip(_notice(DraftStatus.SKIPPED, moves.LEAD_SKIP))

    assert jobs.paths == [notify.NOTICE_JOB, handover.HANDOVER_JOB]
    _off_the_loop(jobs)
