"""Долгие задачи продаж — в очередь `sales-bulk`; поставленные до выкатки в `sales` находятся.

Сборка очереди, автосборка, очистка и сбор базы идут минутами, сбор — до получаса. В очереди
ответов продаж (`sales`, `worker-sales`) они держали бы разбор ответа лида и черновик агента за
собой; теперь — своя очередь и свой процесс (`sales-bulk`, `worker-sales-bulk`), как у пачки писем
продаж. Короткие задачи — передача лида, сообщения о черновике, письмо одному лиду — остаются
в `sales`. Постановка — в потоке, не в цикле событий сервера.

Номер задачи един на весь Redis, а `Queue.fetch_job` видит только свою очередь. Задача,
поставленная до выкатки в `sales`, находится по номеру (`sales/job_holder.holder`, у сбора —
`api/letters/once._previous`): идущая держит вторую — 409 словами, модель и Hunter не тратятся
дважды; кончившаяся убирается вместе с итогом и номер не держит.

Redis — подставной, с правилами rq 2.12: задачи по номеру общие для всех очередей.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from backend.api.letters import once
from backend.api.sales.clean import CLEAN_RUNNING
from backend.api.sales.segment import RUN_GOING
from backend.config import sales as sales_cfg
from backend.features.agent.stages import DraftNotice
from backend.features.core.domain import DraftStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.sales import autobuild, corridor, handoff, job_holder, queue_jobs
from backend.features.sales.agent import handover, moves, notify
from backend.features.sales.clean_jobs import CLEAN_JOB
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.queue_jobs import QUEUE_JOB
from backend.features.sales.queue_start import AUTO, BUILD_RUNNING, build_job_id
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.shared.queue import QUEUE_NAME, SALES_BULK_QUEUE_NAME, SALES_QUEUE_NAME
from httpx import AsyncClient
from rq.exceptions import DuplicateJobError, NoSuchJobError
from rq.job import JobStatus
from rq.results import Result
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer
from tests.test_sales_clean_api import waiting
from tests.test_sales_segment_estimate import KEYWORDS, _settings
from tests.test_sales_segment_run import hypothesis, make_run

__all__ = ["_settings", "waiting"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"


class _Redis:
    """Redis очередей: задачи по номеру — общие для всех очередей; что стёрто мимо задачи."""

    def __init__(self) -> None:
        self.known: dict[str, _Job] = {}
        self.deleted: list[str] = []

    def delete(self, *keys: str) -> None:
        self.deleted.extend(keys)


class _Job:
    """Задача rq в том, что о ней спрашивают продажи: статус, мета, итог, удаление следа."""

    def __init__(self, redis: _Redis, job_id: str, origin: str, meta: object) -> None:
        self.id = job_id
        self.origin = origin
        self.connection = redis
        self.meta: dict[str, Any] = dict(meta) if isinstance(meta, dict) else {}
        self.status = JobStatus.QUEUED
        self.report: dict[str, Any] | None = None

    def get_status(self, **_options: object) -> JobStatus:
        return self.status

    def latest_result(self) -> SimpleNamespace | None:
        return None if self.report is None else SimpleNamespace(return_value=self.report)

    def delete(self) -> None:
        self.connection.known.pop(self.id, None)


class _Queue:
    """Очередь rq 2.12 в том, что нужно постановке и строке экрана; помнит поток каждого вызова."""

    serializer = None

    def __init__(self, redis: _Redis, name: str) -> None:
        self.connection = redis
        self.name = name
        self.enqueued: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.threads: list[int] = []

    def fetch_job(self, job_id: str) -> _Job | None:
        self.threads.append(threading.get_ident())
        found = self.connection.known.get(job_id)
        return found if found is not None and found.origin == self.name else None

    def enqueue(self, *args: Any, **kwargs: Any) -> _Job:
        self.threads.append(threading.get_ident())
        job_id = str(kwargs.get("job_id") or f"{self.name}-{len(self.enqueued)}")
        if kwargs.get("unique") and job_id in self.connection.known:
            raise DuplicateJobError(f"Job with ID '{job_id}' already exists")
        self.enqueued.append((args, kwargs))
        job = _Job(self.connection, job_id, self.name, kwargs.get("meta"))
        self.connection.known[job_id] = job
        return job


class _Fetched:
    """`rq.job.Job.fetch` на подставном Redis: задача по номеру из любой очереди."""

    def __init__(self, redis: _Redis) -> None:
        self.redis = redis

    def fetch(self, job_id: str, connection: object = None, serializer: object = None) -> _Job:
        found = self.redis.known.get(job_id)
        if found is None:
            raise NoSuchJobError(job_id)
        return found


@pytest.fixture
def redis(monkeypatch: pytest.MonkeyPatch) -> _Redis:
    found = _Redis()
    monkeypatch.setattr(job_holder, "Job", _Fetched(found))
    monkeypatch.setattr(once, "Job", _Fetched(found))
    return found


@pytest.fixture
def bulk(monkeypatch: pytest.MonkeyPatch, redis: _Redis) -> _Queue:
    """Очередь долгих задач продаж — у маршрутов и у прохода автосборки."""
    found = _Queue(redis, SALES_BULK_QUEUE_NAME)
    for module in (
        "api.sales.queue",
        "api.sales.clean",
        "api.sales.segment",
        "features.sales.autobuild",
    ):
        monkeypatch.setattr(f"backend.{module}.sales_bulk_queue", lambda: found)
    return found


@pytest.fixture
def answers(redis: _Redis) -> _Queue:
    """Очередь ответов продаж (`sales`) — где долгие задачи стояли до выкатки."""
    return _Queue(redis, SALES_QUEUE_NAME)


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Продавец с правами запуска и отправки: сборка — `sales` и `send`, очистка и сбор — `run`."""
    await make_user(SELLER, permissions={"send": True})
    return bearer(await sign_in(SELLER))


def _off_the_loop(*queues: _Queue) -> None:
    """Каждый вызов очереди — не в потоке цикла событий (этот тест идёт в нём)."""
    threads = [thread for queue in queues for thread in queue.threads]
    assert threads, "очередь не звали"
    assert threading.get_ident() not in threads, "вызов Redis — в потоке цикла событий"


def _before(answers: _Queue, job_id: str, path: str, *, status: JobStatus, **meta: Any) -> _Job:
    """Задача прежней версии: стоит в очереди ответов продаж, поставлена до выкатки."""
    job = answers.enqueue(path, job_id=job_id, unique=True, meta=meta)
    job.status = status
    return job


async def _build(client: AsyncClient, headers: dict[str, str], hypothesis_id: int) -> Any:
    return await client.post(
        "/api/sales/queue", json={"hypothesis_id": hypothesis_id}, headers=headers
    )


async def _clean(client: AsyncClient, headers: dict[str, str], hypothesis_id: int) -> Any:
    return await client.post(
        "/api/sales/clean", json={"hypothesis_id": hypothesis_id}, headers=headers
    )


# --- куда идут долгие задачи ----------------------------------------------------------------------


async def test_the_build_goes_to_the_bulk_queue_off_the_event_loop(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    bulk: _Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await w.world(session, monkeypatch)

    response = await _build(client, headers, world.hypothesis_id)

    assert response.status_code == 200, response.text
    [(args, kwargs)] = bulk.enqueued
    assert (args[0], kwargs["job_id"]) == (QUEUE_JOB, build_job_id(world.hypothesis_id))
    _off_the_loop(bulk)


async def test_the_clean_goes_to_the_bulk_queue_off_the_event_loop(
    client: AsyncClient, headers: dict[str, str], waiting: SalesHypothesisModel, bulk: _Queue
) -> None:
    response = await _clean(client, headers, waiting.id)

    assert response.status_code == 202, response.text
    [(args, _)] = bulk.enqueued
    assert args[0] == CLEAN_JOB
    _off_the_loop(bulk)


async def test_the_segment_run_and_its_resume_go_to_the_bulk_queue_off_the_event_loop(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], bulk: _Queue
) -> None:
    owner = await hypothesis(session)
    await session.commit()

    started = await client.post(
        "/api/sales/segment/runs",
        json={"hypothesis_id": owner.id, "keywords": KEYWORDS, "country": "us"},
        headers=headers,
    )
    run = await make_run(session, owner)
    resumed = await client.post(f"/api/sales/segment/runs/{run.id}/resume", headers=headers)

    assert (started.status_code, resumed.status_code) == (202, 202), started.text + resumed.text
    assert [args[0] for args, _ in bulk.enqueued] == [SEGMENT_JOB, SEGMENT_JOB]
    _off_the_loop(bulk)


async def test_the_autobuild_pass_builds_through_the_bulk_queue(
    session: AsyncSession, bulk: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Проход автосборки в процессе разбора ставит сборку туда же, что кнопка, — в потоке."""
    world = await w.world(session, monkeypatch)
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    await session.commit()

    await autobuild.run(session, bulk, now=datetime.now(UTC))

    [(args, kwargs)] = bulk.enqueued
    assert (args[0], kwargs["meta"]) == (QUEUE_JOB, {AUTO: True})
    _off_the_loop(bulk)


async def test_the_autobuild_tick_takes_the_bulk_queue(
    bulk: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Расписание (`reaper` → `autobuild.tick`) отдаёт проходу очередь долгих задач продаж."""
    seen: list[object] = []

    async def step(_session: object, jobs: object, *, now: datetime) -> None:
        seen.append(jobs)

    class _Closable:
        async def dispose(self) -> None:
            return None

    class _Session:
        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_exc: object) -> None:
            return None

    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", True)
    monkeypatch.setattr(autobuild, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(autobuild, "async_sessionmaker", lambda *_a, **_k: _Session)
    monkeypatch.setattr(autobuild, "step", step)

    await autobuild.tick()

    assert seen == [bulk]


# --- задача, поставленная до выкатки в очередь ответов, находится --------------------------------


async def test_a_build_from_before_the_rollout_still_running_holds_the_second(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    bulk: _Queue,
    answers: _Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await w.world(session, monkeypatch)
    _before(answers, build_job_id(world.hypothesis_id), QUEUE_JOB, status=JobStatus.STARTED)

    response = await _build(client, headers, world.hypothesis_id)

    assert (response.status_code, response.json()["detail"]) == (409, BUILD_RUNNING)
    assert bulk.enqueued == []


async def test_a_finished_build_from_before_the_rollout_holds_nothing(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    bulk: _Queue,
    answers: _Queue,
    redis: _Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = await w.world(session, monkeypatch)
    job_id = build_job_id(world.hypothesis_id)
    _before(answers, job_id, QUEUE_JOB, status=JobStatus.FINISHED)

    response = await _build(client, headers, world.hypothesis_id)

    assert response.status_code == 200, response.text
    assert redis.known[job_id].origin == SALES_BULK_QUEUE_NAME
    assert redis.deleted == [Result.get_key(job_id)]


async def test_a_clean_from_before_the_rollout_holds_while_it_runs(
    client: AsyncClient,
    headers: dict[str, str],
    waiting: SalesHypothesisModel,
    bulk: _Queue,
    answers: _Queue,
    redis: _Redis,
) -> None:
    job_id = f"sales-clean-{waiting.id}"
    earlier = _before(answers, job_id, CLEAN_JOB, status=JobStatus.STARTED)

    running = await _clean(client, headers, waiting.id)
    earlier.status = JobStatus.FINISHED
    after = await _clean(client, headers, waiting.id)

    assert (running.status_code, running.json()["detail"]) == (409, CLEAN_RUNNING)
    assert after.status_code == 202, after.text
    assert redis.known[job_id].origin == SALES_BULK_QUEUE_NAME
    assert len(bulk.enqueued) == 1


async def test_a_segment_run_from_before_the_rollout_holds_its_resume(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    bulk: _Queue,
    answers: _Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = await make_run(session, await hypothesis(session))
    _before(answers, segment_job_id(run.id), SEGMENT_JOB, status=JobStatus.STARTED)
    # Воркер прежней задачи жив: она идёт, и продолжение её не убирает (`segment_watch.drop_dead`).
    monkeypatch.setattr("backend.features.sales.segment_watch.job_alive", lambda *_a, **_k: True)

    response = await client.post(f"/api/sales/segment/runs/{run.id}/resume", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (409, RUN_GOING)
    assert bulk.enqueued == []


async def test_autobuild_sees_a_build_from_before_the_rollout(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    bulk: _Queue,
    answers: _Queue,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Автосборка до выкатки поставила сборку в очередь ответов: пока идёт — проход второй не
    ставит; кончилась — строка экрана называет, что она собрала."""
    world = await w.world(session, monkeypatch)
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    await session.commit()
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", True)
    await autobuild.run(session, answers, now=datetime.now(UTC))
    earlier = answers.connection.known[build_job_id(world.hypothesis_id)]
    earlier.status = JobStatus.STARTED

    found = await autobuild.run(session, bulk, now=datetime.now(UTC))
    earlier.status, earlier.report = JobStatus.FINISHED, {"prepared": 3, "refreshed": 0}
    screen = await client.get(f"/api/sales/queue?hypothesis={world.hypothesis_id}", headers=headers)

    assert found.why is not None
    assert found.why.startswith("идёт сборка гипотезы «Выдуманная гипотеза»")
    assert bulk.enqueued == []
    assert screen.status_code == 200, screen.text
    assert str(screen.json()["autobuild"]["line"]).endswith(": собрано 3 письма.")


async def test_a_build_from_before_the_rollout_stopped_on_a_streak_still_stops_autobuild(
    session: AsyncSession, bulk: _Queue, answers: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сборка до выкатки встала на серии писем вне коридора: автосборка эту гипотезу не
    собирает, пока человек не соберёт её кнопкой, — и тогда, когда сборка лежит в прежней очереди."""
    world = await w.world(session, monkeypatch)
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    await session.commit()
    earlier = _before(
        answers, build_job_id(world.hypothesis_id), QUEUE_JOB, status=JobStatus.FINISHED
    )
    earlier.report = {"prepared": 0, "refreshed": 0, "stopped": corridor.streak_stop()}

    found = await autobuild.run(session, bulk, now=datetime.now(UTC))

    assert found.why == autobuild.STREAK
    assert bulk.enqueued == []


# --- короткие задачи — в очереди ответов; пачка доноров — в общей --------------------------------


def test_short_sales_tasks_stay_in_the_answers_queue(
    bulk: _Queue, answers: _Queue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Передача лида, сообщение о черновике, передача по «хочет говорить», письмо одному лиду —
    секунды, и их ждёт человек: очередь ответов продаж, а не долгих задач."""
    for module in (handoff, notify, handover, queue_jobs):
        monkeypatch.setattr(module, "sales_queue", lambda: answers)
    notice = DraftNotice(
        draft_id=3,
        reply_id=5,
        thread_id=7,
        stage=Stage.SALES,
        status=DraftStatus.SKIPPED,
        reason=moves.LEAD_SKIP,
    )

    handoff.enqueue_handoff(11)
    notify.queue_notice(3)
    handover.queue_hand_over(notice)
    queue_jobs.build_for(2, 13)

    assert [args[0] for args, _ in answers.enqueued] == [
        handoff.HANDOFF_JOB,
        notify.NOTICE_JOB,
        handover.HANDOVER_JOB,
        QUEUE_JOB,
    ]
    assert bulk.enqueued == []


async def test_donor_letters_stay_in_the_shared_queue(
    client: AsyncClient,
    redis: _Redis,
    bulk: _Queue,
    make_user: MakeUser,
    sign_in: SignIn,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Перенос долгих задач продаж пачку и сборку писем доноров не трогает: общая очередь."""
    runs = _Queue(redis, QUEUE_NAME)
    monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: runs)
    monkeypatch.setattr("backend.api.letters.routes.sales_bulk_queue", lambda: bulk)
    await make_user("admin@team.example.test", role=UserRole.ADMIN)
    headers = bearer(await sign_in("admin@team.example.test"))

    response = await client.post("/api/letters/build", json={"campaign": "Май"}, headers=headers)

    assert response.status_code == 200, response.text
    assert [kwargs["job_id"] for _, kwargs in runs.enqueued] == ["letters-build-donors-links"]
    assert bulk.enqueued == []
