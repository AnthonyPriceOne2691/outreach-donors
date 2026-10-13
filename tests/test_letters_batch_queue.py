"""Пачка и сборка писем продаж — в очередь продаж (ревью стыков 12.10.2026).

«Отправить очередь» продаж ставилась в общую очередь: пачка — до двухсот писем с повторами
отправки — держала единственный общий воркер, и прогоны доноров, разбор ответов и сборка писем
ждали её. Теперь задача писем продаж встаёт в очередь `sales`, которую слушает `worker-sales`, —
та же задача (`workers/send_jobs.send_letter_queue`), тот же номер, то же «одна за раз»: повтор
той же пачки — 409.

Очередь — подставная, с правилами rq 2.12, на которых стоит постановка: номер задачи един на весь
Redis (`unique=True` смотрит ключ задачи), а `Queue.fetch_job` отдаёт только задачи своей очереди.
Отсюда переход: пачка продаж, поставленная до выкатки в общую очередь, держит номер — кончившаяся
не должна держать новую пачку неделю, идущая — держит.
"""

from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
import yaml
from backend.api.letters import once
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.shared.queue import QUEUE_NAME, SALES_QUEUE_NAME, SEND_QUEUE_JOB
from backend.workers import main as worker_main
from backend.workers import send_jobs
from httpx import AsyncClient, Response
from rq.exceptions import DuplicateJobError, NoSuchJobError
from rq.job import JobStatus
from rq.results import Result
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ROOT = Path(__file__).resolve().parent.parent
SALES_BATCH = once.send_job_id(Stage.SALES, "links")


class _Redis:
    """Redis очередей: задачи по номеру — общие для всех очередей; что стёрто мимо задачи."""

    def __init__(self) -> None:
        self.known: dict[str, _Job] = {}
        self.deleted: list[str] = []

    def delete(self, *keys: str) -> None:
        self.deleted.extend(keys)


class _Job:
    def __init__(self, redis: _Redis, job_id: str, origin: str) -> None:
        self.id = job_id
        self.origin = origin
        self.status = JobStatus.QUEUED
        self.connection = redis
        self._redis = redis

    def get_status(self) -> JobStatus:
        return self.status

    def delete(self) -> None:
        self._redis.known.pop(self.id, None)


class _Queue:
    """Очередь rq 2.12 в том, что нужно постановке."""

    serializer = None

    def __init__(self, redis: _Redis, name: str) -> None:
        self.connection = redis
        self.name = name
        self.enqueued: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def fetch_job(self, job_id: str) -> _Job | None:
        found = self.connection.known.get(job_id)
        return found if found is not None and found.origin == self.name else None

    def enqueue(self, *args: Any, **kwargs: Any) -> _Job:
        job_id = str(kwargs["job_id"])
        if kwargs.get("unique") and job_id in self.connection.known:
            raise DuplicateJobError(f"Job with ID '{job_id}' already exists")
        self.enqueued.append((args, kwargs))
        self.connection.known[job_id] = _Job(self.connection, job_id, self.name)
        return self.connection.known[job_id]


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
    monkeypatch.setattr(once, "Job", _Fetched(found))
    return found


@pytest.fixture
def runs(monkeypatch: pytest.MonkeyPatch, redis: _Redis) -> _Queue:
    found = _Queue(redis, QUEUE_NAME)
    monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: found)
    return found


@pytest.fixture
def sales(monkeypatch: pytest.MonkeyPatch, redis: _Redis) -> _Queue:
    found = _Queue(redis, SALES_QUEUE_NAME)
    monkeypatch.setattr("backend.api.letters.routes.sales_queue", lambda: found)
    return found


class _Connected:
    """Модуль продаж в том, о чём спрашивает пачка: продажи подключены."""

    async def connected(self, _session: AsyncSession) -> bool:
        return True


@pytest.fixture
async def seller(
    session: AsyncSession,
    make_user: MakeUser,
    sign_in: SignIn,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, str]:
    """Подключённые продажи, письмо продаж в очереди и оператор с отправкой и правом «Продажи»."""
    monkeypatch.setattr(stages._SALES, "load", _Connected)
    campaign = CampaignModel(stage=Stage.SALES, name="Пачка продаж", status="running")
    domain = DomainModel(host="lead-co.example.test")
    session.add_all([campaign, domain])
    await session.flush()
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            domain_id=domain.id,
            step=0,
            status=MessageStatus.QUEUED,
            subject="A question",
            body="Good afternoon!",
            idempotency_key="batch-queue:sales:lead-co:0",
        )
    )
    await session.commit()
    await make_user("seller@team.example.test", role=UserRole.OPERATOR, permissions={"send": True})
    return bearer(await sign_in("seller@team.example.test"))


async def _send(client: AsyncClient, headers: dict[str, str]) -> Response:
    return await client.post("/api/letters/send-queue", json={"stage": "sales"}, headers=headers)


async def test_the_sales_batch_goes_to_the_sales_queue(
    client: AsyncClient, seller: dict[str, str], runs: _Queue, sales: _Queue
) -> None:
    response = await _send(client, seller)

    assert response.status_code == 200, response.text
    assert runs.enqueued == []
    [(args, kwargs)] = sales.enqueued
    assert (args[0], kwargs["job_id"]) == (SEND_QUEUE_JOB, SALES_BATCH)
    # Ту же задачу выполняет воркер продаж: доводы — те, что она принимает.
    bound = inspect.signature(send_jobs.send_letter_queue).bind(*args[1:])
    assert bound.arguments["stage"] == "sales"


async def test_a_repeated_sales_batch_is_refused_in_words(
    client: AsyncClient, seller: dict[str, str], runs: _Queue, sales: _Queue
) -> None:
    first = await _send(client, seller)
    second = await _send(client, seller)

    assert first.status_code == 200, first.text
    assert (second.status_code, second.json()["detail"]) == (409, once.SEND_RUNNING)
    assert len(sales.enqueued) == 1


async def test_a_finished_batch_from_before_the_rollout_holds_nothing(
    client: AsyncClient, seller: dict[str, str], runs: _Queue, sales: _Queue, redis: _Redis
) -> None:
    """Пачка продаж прежней версии стояла в общей очереди и кончилась: её след держит номер, но
    новую пачку в очереди продаж не держит — убирается вместе с итогом, как своя."""
    runs.enqueue(SEND_QUEUE_JOB, "sales", job_id=SALES_BATCH, unique=True)
    redis.known[SALES_BATCH].status = JobStatus.FINISHED

    response = await _send(client, seller)

    assert response.status_code == 200, response.text
    assert redis.known[SALES_BATCH].origin == SALES_QUEUE_NAME
    assert redis.deleted == [Result.get_key(SALES_BATCH)]


async def test_a_batch_from_before_the_rollout_still_sending_holds(
    client: AsyncClient, seller: dict[str, str], runs: _Queue, sales: _Queue, redis: _Redis
) -> None:
    runs.enqueue(SEND_QUEUE_JOB, "sales", job_id=SALES_BATCH, unique=True)
    redis.known[SALES_BATCH].status = JobStatus.STARTED

    response = await _send(client, seller)

    assert (response.status_code, response.json()["detail"]) == (409, once.SEND_RUNNING)
    assert sales.enqueued == []


async def test_donor_letters_stay_in_the_shared_queue(
    client: AsyncClient,
    runs: _Queue,
    sales: _Queue,
    make_user: MakeUser,
    sign_in: SignIn,
) -> None:
    await make_user("admin@team.example.test", role=UserRole.ADMIN)
    headers = bearer(await sign_in("admin@team.example.test"))

    response = await client.post("/api/letters/build", json={"campaign": "Май"}, headers=headers)

    assert response.status_code == 200, response.text
    assert [kwargs["job_id"] for _, kwargs in runs.enqueued] == ["letters-build-donors-links"]
    assert sales.enqueued == []


def test_the_sales_worker_listens_to_the_sales_queue() -> None:
    """Задача, поставленная в очередь продаж, — тому, кто её слушает: `worker-sales`."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    command = compose["services"]["worker-sales"]["command"].split()

    assert command[:3] == ["python", "-m", "backend.workers.main"]
    assert worker_main.queue_of(command[3:]) == SALES_QUEUE_NAME
