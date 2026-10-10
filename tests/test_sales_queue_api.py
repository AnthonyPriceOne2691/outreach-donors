"""Очередь писем продаж через API и задачу — срез 4.6b, T4; лид диалога по явной связи.

`GET /api/sales/queue` — подключены ли продажи и чего не хватает (словами отказа
отправки), цепочки по языкам, сколько лидов без письма и писем в очереди. `POST` — сборка
задачей; отказ подключения — 409 словами до очереди задач. Права: смотреть — `sales`, собрать —
`sales` и `send` (`test_sales_write_rights.py`). Тексты и адреса выдуманы (`*.example.test`).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.sales import queue as sales_queue_api
from backend.api.sales.queue import SalesQueueBody, SalesQueueView
from backend.cli.sales_queue import run_sales_queue
from backend.config import sales as sales_cfg
from backend.config import storage
from backend.features.core.domain import AuditAction, SenderStatus, Stage
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import MessageModel
from backend.features.core.stages import SALES_NOT_CONNECTED, SalesNotConnectedError
from backend.features.letters import followups
from backend.features.letters.batch import BATCH_MAX, send_queue
from backend.features.letters.rewrite import RewriteClient
from backend.features.letters.sending import Sending
from backend.features.ops import job_outcome
from backend.features.sales import chain, connection, queue, queue_jobs, sender
from backend.features.sales.handoff import lead_of
from backend.features.sales.models import SalesThreadModel
from backend.shared.queue import QUEUE_NAME, SALES_QUEUE_NAME
from fastapi import FastAPI
from httpx import AsyncClient, Response
from rq.job import JobStatus
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import TEST_DSN, bearer
from tests.test_sales_clean_api import _Jobs, _screen_fields, _screen_reads
from tests.test_sales_send import FIRST_DUE, _transports

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
QUEUE = "/api/sales/queue"
NO_RIGHT = "Действие «sales» недоступно этой учётке"


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Продавец с правом отправки: собрать очередь — `sales` и `send` (`test_sales_write_rights`)."""
    await make_user(SELLER, permissions={"send": True})
    return bearer(await sign_in(SELLER))


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    """Подставная очередь очистки (`test_sales_clean_api.py`): правила rq 2.12 — номер, `unique`,
    след задачи и итог отдельно от задачи; одна подделка на сборку и очистку."""
    found = _Jobs()
    monkeypatch.setattr("backend.api.sales.queue.sales_queue", lambda: found)
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
    # Потолок пачки — тот же, что у экрана писем: окно подтверждения называет его.
    assert body["batch_max"] == BATCH_MAX
    assert [item["language"] for item in body["chains"]] == ["ru", "en"]
    # Та же форма, что у экрана цепочки: экран продаж читает её одним типом.
    assert body["chains"][1] == {
        "language": "en",
        "source": "common",
        "missing": [],
        "version": common.version,
    }


async def test_followup_stuck_in_the_queue_is_not_in_the_number_of_the_batch(
    session: AsyncSession, world: w.World, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Пачка этапа берёт только первые письма (общая очередь почты): добивка, застрявшая
    в очереди без своего ящика, ждёт проход добивок — в числе на кнопке её нет, и пачка
    уходит ровно тем числом, что названо."""
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")
    await w.lead(session, world.hypothesis_id, "olga@acme.example.test")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=5)
    first = await session.scalar(select(MessageModel).order_by(MessageModel.id).limit(1))
    assert first is not None
    source = _transports()
    await Sending(session, source, now=w.NOW).send(first.id)
    world.sales_box.status = SenderStatus.PAUSED
    stuck = await followups.send_due(session, transport=source, limit=5, now=FIRST_DUE)
    world.sales_box.status = SenderStatus.FREE
    await session.commit()

    body = (await client.get(f"{QUEUE}?hypothesis={world.hypothesis_id}", headers=headers)).json()
    report = await send_queue(session, source, stage=Stage.SALES)

    assert stuck.postponed == 1
    assert (body["queued"], body["stage_queued"]) == (1, 1)
    assert (report.sent, report.left) == (1, 0)


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
    # Выключенный модуль — первым пунктом и словами человека: включает его администратор,
    # имя настройки — в журнале, а не на экране (находка QA на проде).
    assert body["missing"] == [
        "модуль продаж выключен — включает администратор",
        f"не задан физический адрес; не задана подпись; не задано имя отправителя — {sender.WHERE}",
    ]
    assert [item["missing"] for item in body["chains"]] == [
        ["первого письма", "первой добивки", "второй добивки"]
    ] * 2


async def test_switched_off_build_is_refused_in_words_without_the_setting_name(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Отказ сборки при выключенном модуле — словами человека; имя настройки — в журнале."""
    await session.commit()
    monkeypatch.setattr(sales_cfg, "ENABLED", False)

    with caplog.at_level(logging.WARNING, logger=connection.__name__):
        response = await _build(client, headers, world.hypothesis_id)

    detail = response.json()["detail"]
    assert (response.status_code, detail) == (
        409,
        f"{queue.WHAT}: {SALES_NOT_CONNECTED} — модуль продаж выключен — включает администратор",
    )
    assert "SALES_ENABLED" not in detail
    journal = [r.__dict__["settings"] for r in caplog.records if r.name == connection.__name__]
    assert journal == [["SALES_ENABLED"]]
    assert jobs.enqueued == []


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
    assert response.json() == {"job_id": sales_queue_api.build_job_id(world.hypothesis_id)}
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


def test_build_goes_to_the_sales_queue_not_the_common_one() -> None:
    """Сборка — в очередь продаж (`worker-sales`): модель на каждое письмо — минуты, и они не
    держат воркер доноров, а сборка не ждёт за прогоном. Очередь — настоящая `rq.Queue`,
    Redis не трогается: соединение открывается только командой."""
    assert sales_queue_api.sales_queue().name == SALES_QUEUE_NAME != QUEUE_NAME


async def _build(client: AsyncClient, headers: dict[str, str], hypothesis_id: int) -> Response:
    return await client.post(
        QUEUE, json={"hypothesis_id": hypothesis_id, "limit": 5}, headers=headers
    )


async def test_second_build_while_the_first_runs_is_refused_in_words(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
) -> None:
    """Двойное «Собрать» — одна сборка: вторая собрала бы те же письма и потратила модель дважды."""
    await session.commit()

    first = await _build(client, headers, world.hypothesis_id)
    second = await _build(client, headers, world.hypothesis_id)

    assert first.status_code == 200, first.text
    assert (second.status_code, second.json()["detail"]) == (409, sales_queue_api.BUILD_RUNNING)
    assert len(jobs.enqueued) == 1


@pytest.mark.parametrize("ended", [JobStatus.FINISHED, JobStatus.FAILED, JobStatus.CANCELED])
async def test_build_after_the_previous_one_ended_goes_again(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    ended: JobStatus,
) -> None:
    """След готовой задачи лежит неделю (`result_ttl`): по одному номеру с `unique=True` новая
    сборка гипотезы неделю получала бы 409 — закончившаяся убирается, новая ставится."""
    await session.commit()
    await _build(client, headers, world.hypothesis_id)
    previous = jobs.known[sales_queue_api.build_job_id(world.hypothesis_id)]
    previous.status = ended

    again = await _build(client, headers, world.hypothesis_id)

    assert again.status_code == 200, again.text
    assert previous.deleted
    assert len(jobs.enqueued) == 2


#: Отчёт прежней сборки — выдуманный, не круглый.
PREVIOUS = {"campaign_id": 3, "prepared": 7, "refreshed": 0, "waiting": {}, "stopped": None}


async def test_new_build_in_the_queue_does_not_show_the_report_of_the_previous_one(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Итог прежней сборки rq хранит отдельно от задачи, и `Job.delete()` его не трогает: новая
    сборка под тем же номером, пока стоит в очереди, показала бы отчёт прежней. Строка задачи —
    тем же путём, что у экрана: `GET /api/jobs/{номер}`."""
    _screen_reads(jobs, monkeypatch)
    await session.commit()
    job_id = sales_queue_api.build_job_id(world.hypothesis_id)
    await _build(client, headers, world.hypothesis_id)
    jobs.known[job_id].finish(PREVIOUS)
    before = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()

    again = await _build(client, headers, world.hypothesis_id)
    after = (await client.get(f"/api/jobs/{job_id}", headers=headers)).json()

    assert (before["kind"], before["state"], before["report"]) == (
        "сборка очереди продаж",
        "done",
        PREVIOUS,
    )
    assert again.status_code == 200, again.text
    assert (after["state"], after["report"]) == ("queued", None)


@pytest.mark.parametrize("waits", [JobStatus.STARTED, JobStatus.DEFERRED, JobStatus.SCHEDULED])
async def test_build_waiting_for_a_retry_still_counts_as_running(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    waits: JobStatus,
) -> None:
    """Идёт, ждёт зависимости или повтора после сбоя — сборка ещё не кончилась: вторую не ставим."""
    await session.commit()
    await _build(client, headers, world.hypothesis_id)
    jobs.known[sales_queue_api.build_job_id(world.hypothesis_id)].status = waits

    second = await _build(client, headers, world.hypothesis_id)

    assert second.status_code == 409
    assert len(jobs.enqueued) == 1


async def test_two_clicks_racing_past_the_check_still_build_once(
    session: AsyncSession,
    world: w.World,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Оба нажатия прошли проверку, а поставить успел один — второму `unique=True` отвечает 409."""
    await session.commit()
    monkeypatch.setattr(jobs, "fetch_job", lambda _job_id: None)

    first = await _build(client, headers, world.hypothesis_id)
    second = await _build(client, headers, world.hypothesis_id)

    assert (first.status_code, second.status_code) == (200, 409)
    assert len(jobs.enqueued) == 1


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
        raise SalesNotConnectedError(queue.WHAT, "модуль продаж выключен — включает администратор")

    monkeypatch.setattr(queue_jobs, "run_build", refused)
    monkeypatch.setattr(queue_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(queue_jobs, "check_storage", lambda: None)

    result = queue_jobs.build_sales_queue(5, 10)

    assert result == {
        "error": f"SalesNotConnectedError: {queue.WHAT}: {SALES_NOT_CONNECTED} — "
        "модуль продаж выключен — включает администратор",
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
