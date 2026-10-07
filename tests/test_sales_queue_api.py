"""Очередь писем продаж через API и задачу — срез 4.6b, T4; лид диалога по явной связи.

`GET /api/sales/queue` — подключены ли продажи и чего не хватает (словами отказа
отправки), цепочки по языкам, сколько лидов без письма и писем в очереди. `POST` — сборка
задачей; отказ подключения — 409 словами до очереди задач. Права — `sales`. Тексты и
адреса выдуманы (`*.example.test`).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from backend.api.sales.queue import SalesQueueBody, SalesQueueView
from backend.cli.sales_queue import run_sales_queue
from backend.config import sales as sales_cfg
from backend.config import storage
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import MessageModel
from backend.features.core.stages import SALES_NOT_CONNECTED, SalesNotConnectedError
from backend.features.letters.rewrite import RewriteClient
from backend.features.ops import job_outcome
from backend.features.sales import chain, queue, queue_jobs, sender
from backend.features.sales.handoff import lead_of
from backend.features.sales.models import SalesThreadModel
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import TEST_DSN, bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
QUEUE = "/api/sales/queue"
NO_RIGHT = "Действие «sales» недоступно этой учётке"
TYPES = (Path(__file__).resolve().parent.parent / "frontend/src/api/salesTypes.ts").read_text(
    encoding="utf-8"
)


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


class _Jobs:
    """Очередь задач: запоминает, что поставили."""

    def __init__(self) -> None:
        self.enqueued: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def enqueue(self, *args: object, **kwargs: object) -> object:
        self.enqueued.append((args, kwargs))
        return type("Job", (), {"id": "job-7"})()


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    found = _Jobs()
    monkeypatch.setattr("backend.api.sales.queue.runs_queue", lambda: found)
    return found


# --- что видно на экране -----------------------------------------------------------------------


async def test_queue_shows_connected_sales_chains_and_what_waits(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    await w.lead(session, world.hypothesis_id, "olga@acme.example.test")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=1)
    other = await _hypothesis(session)
    await w.lead(session, other, "ivan@beta.example.test")
    await w.lead(session, other, "petr@gamma.example.test")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=other, limit=5)
    await session.commit()
    common = await chain.resolve(session, hypothesis_id=world.hypothesis_id, language="en")

    response = await client.get(f"{QUEUE}?hypothesis={world.hypothesis_id}", headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["connected"], body["missing"], body["unwritten"], body["queued"]) == (
        True,
        [],
        1,
        1,
    )
    # Пачка берёт очередь этапа целиком: кнопка называет письма всех гипотез.
    assert (body["stage_queued"], body["limit_max"]) == (3, 200)
    assert [item["language"] for item in body["chains"]] == ["ru", "en"]
    # Та же форма, что у экрана цепочки: экран продаж читает её одним типом.
    assert body["chains"][1] == {
        "language": "en",
        "source": "common",
        "missing": [],
        "version": common.version,
    }


async def test_queue_says_in_words_what_sales_lack(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    w.connect(monkeypatch)
    monkeypatch.setattr(sales_cfg, "ENABLED", False)
    hypothesis = await _hypothesis(session)

    response = await client.get(f"{QUEUE}?hypothesis={hypothesis}", headers=headers)

    body = response.json()
    assert body["connected"] is False
    assert body["missing"] == [
        "продажи выключены: SALES_ENABLED не включён",
        f"не задан физический адрес; не задана подпись; не задано имя отправителя — {sender.WHERE}",
    ]
    assert [item["missing"] for item in body["chains"]] == [
        ["первого письма", "первой добивки", "второй добивки"]
    ] * 2


async def _hypothesis(session: AsyncSession) -> int:
    from backend.features.sales import hypotheses  # noqa: PLC0415

    made = await hypotheses.add(session, "Гипотеза экрана очереди", None)
    await session.commit()
    return made.id


async def test_unknown_hypothesis_is_404_in_words(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get(f"{QUEUE}?hypothesis=9876", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (
        404,
        "гипотезы №9876 нет — обновите список гипотез",
    )


# --- сборка задачей ----------------------------------------------------------------------------


async def test_build_goes_to_the_job_queue_and_into_the_journal(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
) -> None:
    await session.commit()

    response = await client.post(
        QUEUE, json={"hypothesis_id": world.hypothesis_id, "limit": 17}, headers=headers
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"job_id": "job-7"}
    [(args, _)] = jobs.enqueued
    assert args == (queue_jobs.QUEUE_JOB, world.hypothesis_id, 17)
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert journal is not None
    assert journal.details == {
        "действие": "сборка очереди продаж",
        "гипотеза": "Выдуманная гипотеза",
        "писем": 17,
    }


async def test_build_refuses_with_409_before_the_job_queue(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
) -> None:
    await w.settings(session, signature=None)
    await session.commit()

    response = await client.post(
        QUEUE, json={"hypothesis_id": world.hypothesis_id}, headers=headers
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"{queue.WHAT}: {SALES_NOT_CONNECTED} — не задана подпись — {sender.WHERE}"
    )
    assert jobs.enqueued == []


@pytest.mark.parametrize("limit", [0, 201])
async def test_build_limit_is_bounded(
    world: w.World, client: AsyncClient, headers: dict[str, str], jobs: _Jobs, limit: int
) -> None:
    response = await client.post(
        QUEUE, json={"hypothesis_id": world.hypothesis_id, "limit": limit}, headers=headers
    )

    assert response.status_code == 422
    assert jobs.enqueued == []


@pytest.mark.parametrize(("method", "body"), [("GET", None), ("POST", {"hypothesis_id": 1})])
async def test_without_the_sales_right_the_queue_refuses_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, method: str, body: Any
) -> None:
    await make_user(SELLER, permissions={"sales": False})
    path = f"{QUEUE}?hypothesis=1" if method == "GET" else QUEUE

    response = await client.request(method, path, json=body, headers=bearer(await sign_in(SELLER)))

    assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)


def _screen_fields(name: str) -> set[str]:
    """Поля интерфейса экрана `export interface <name> { … }` — файл читается как текст."""
    found = re.search(rf"export interface {name} \{{\n(.*?)\n\}}", TYPES, re.DOTALL)
    assert found is not None, f"в salesTypes.ts нет интерфейса {name}"
    return set(re.findall(r"^  ([a-z_]+):", found.group(1), re.MULTILINE))


def test_screen_reads_the_queue_by_the_server_names() -> None:
    """Поле, переименованное на сервере, экран показал бы пустым — без ошибки."""
    assert _screen_fields("SalesQueueView") == set(SalesQueueView.model_fields)
    assert _screen_fields("SalesQueueBody") == set(SalesQueueBody.model_fields)
    assert _screen_fields("SalesQueueReport") == set(queue.QueueReport(campaign_id=1).as_dict())


async def test_queue_routes_are_these_two(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith(QUEUE)
        for method in methods
    }

    assert in_app == {("GET", QUEUE), ("POST", QUEUE)}


# --- задача ------------------------------------------------------------------------------------


def test_job_is_named_in_words_on_the_screen() -> None:
    """Строка задачи на экране говорит, что это за задача, а не путь функции."""
    assert job_outcome.KINDS[queue_jobs.QUEUE_JOB] == "сборка очереди продаж"


def test_job_settles_a_refusal_of_connection_as_an_outcome(monkeypatch: pytest.MonkeyPatch) -> None:
    """Повтор задачи продажи не подключит: итог «не выполнена», а не три попытки."""

    async def refused(_hypothesis_id: int, _limit: int) -> dict[str, Any]:
        raise SalesNotConnectedError(queue.WHAT, "продажи выключены: SALES_ENABLED не включён")

    monkeypatch.setattr(queue_jobs, "run_build", refused)
    monkeypatch.setattr(queue_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(queue_jobs, "check_storage", lambda: None)

    result = queue_jobs.build_sales_queue(5, 10)

    assert result == {
        "error": f"SalesNotConnectedError: {queue.WHAT}: {SALES_NOT_CONNECTED} — "
        "продажи выключены: SALES_ENABLED не включён",
        "permanent": True,
    }


def test_job_lets_a_passing_failure_go_to_the_queue_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(_hypothesis_id: int, _limit: int) -> dict[str, Any]:
        raise ConnectionError("база не ответила")

    monkeypatch.setattr(queue_jobs, "run_build", broken)
    monkeypatch.setattr(queue_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(queue_jobs, "check_storage", lambda: None)

    with pytest.raises(ConnectionError, match="база не ответила"):
        queue_jobs.build_sales_queue(5, 10)


async def test_job_builds_with_its_own_session_and_model_client(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[int, int]] = []

    async def build(_session: AsyncSession, _rewriter: object, **kwargs: int) -> queue.QueueReport:
        seen.append((kwargs["hypothesis_id"], kwargs["limit"]))
        return queue.QueueReport(campaign_id=3, prepared=2)

    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(queue, "build", build)

    result = await queue_jobs.run_build(5, 10)

    assert seen == [(5, 10)]
    assert (result["campaign_id"], result["prepared"]) == (3, 2)


async def test_screen_and_console_build_with_the_same_inputs(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Урок L63: у одной сборки два пути — задача с кнопки экрана и консоль. Разойдись их
    входы, очередь с кнопки и из консоли собиралась бы по-разному под тем же именем."""
    hypothesis = await _hypothesis(session)
    seen: list[dict[str, Any]] = []

    async def build(_session: AsyncSession, rewriter: object, **kwargs: Any) -> queue.QueueReport:
        seen.append({"модель": type(rewriter), **kwargs})
        return queue.QueueReport(campaign_id=3)

    monkeypatch.setattr(queue, "build", build)
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    rewriter = RewriteClient()
    try:
        await queue_jobs.run_build(hypothesis, 17)
        await run_sales_queue(session, rewriter, "Гипотеза экрана очереди", 17)
    finally:
        await rewriter.aclose()

    assert len(seen) == 2
    assert seen[0] == seen[1] == {"модель": RewriteClient, "hypothesis_id": hypothesis, "limit": 17}


# --- лид диалога по явной связи ----------------------------------------------------------------


async def test_lead_of_a_sales_dialog_comes_by_the_explicit_link(
    session: AsyncSession, world: w.World
) -> None:
    """У диалога продаж нет адреса `contacts`: прежний поиск по домену и адресу отказал бы
    («нет адреса собеседника»), а у двух лидов одной компании — угадывал бы."""
    jane = await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    olga = await w.lead(session, world.hypothesis_id, "olga@acme.example.test")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5)
    links = list(await session.scalars(select(SalesThreadModel).order_by(SalesThreadModel.lead_id)))

    found = [await lead_of(session, link.thread_id) for link in links]

    assert [lead.id for lead in found] == [jane.id, olga.id]
    letters = await session.scalars(select(MessageModel.contact_id))
    assert set(letters) == {None}
