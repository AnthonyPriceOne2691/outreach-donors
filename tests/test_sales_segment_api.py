"""Экран «Прогон гипотезы» — маршруты `/api/sales/segment…` (срез 6.1).

Смета без записи, запуск задачей очереди продаж «один проход прогона за раз», продолжение,
отчёт и решение человека по «на проверку»; права — `sales`, на запуск и продолжение ещё и
`run`. Очередь задач — подделка с правилами rq (`test_sales_clean_api._Jobs`), Redis не
трогается; настройки сметы — подменой атрибутов конфига.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.sales import segment as segment_api
from backend.config.judge import JudgeMode
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.ops import job_outcome
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
)
from backend.shared.queue import SALES_BULK_QUEUE_NAME
from fastapi import FastAPI
from httpx import AsyncClient, Response
from redis.exceptions import RedisError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_segment_estimate import KEYWORDS, _settings
from tests.test_sales_segment_run import SEGMENT, hypothesis, make_run

__all__ = ["_settings"]  # настройки сметы — те же, что у её тестов

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
SEGMENT_URL = "/api/sales/segment"
RUNS = "/api/sales/segment/runs"


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    found = _Jobs()
    monkeypatch.setattr("backend.api.sales.segment.sales_bulk_queue", lambda: found)
    return found


def body(hypothesis_id: int, **changes: Any) -> dict[str, Any]:
    return {"hypothesis_id": hypothesis_id, "keywords": KEYWORDS, "country": "us", **changes}


async def seen(
    client: AsyncClient, headers: dict[str, str], asked: dict[str, Any]
) -> dict[str, Any]:
    """Тело запуска — как у сметы, которую «видел человек»: с её отпечатком. Смета не
    посчиталась (нет гипотезы, нет права) — без отпечатка: отказ скажет запуск."""
    counted = await client.post(f"{SEGMENT_URL}/estimate", json=asked, headers=headers)
    digest = counted.json().get("digest") if counted.status_code == 200 else None
    return {**asked, "digest": digest}


async def start(client: AsyncClient, headers: dict[str, str], hypothesis_id: int) -> Response:
    return await client.post(
        RUNS, json=await seen(client, headers, body(hypothesis_id)), headers=headers
    )


# --- вкладка и описание сегмента -----------------------------------------------------------------


async def test_the_tab_names_the_segment_the_markets_the_languages_and_the_runs(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)

    response = await client.get(f"{SEGMENT_URL}?hypothesis={owner.id}", headers=headers)

    assert response.status_code == 200, response.text
    found = response.json()
    assert (found["segment"], found["mode"], found["languages"]) == (
        SEGMENT,
        "shadow",
        ["ru", "en"],
    )
    assert {"us", "kz", "ua"} <= set(found["countries"])
    assert (found["max_keywords"], found["max_depth"]) == (100, 10)
    assert [row["id"] for row in found["runs"]] == [run.id]
    assert found["runs"][0]["keywords"] == 1


async def test_the_segment_description_is_written_to_the_hypothesis(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    owner = await hypothesis(session, description=None)

    written = await client.patch(
        f"/api/sales/hypotheses/{owner.id}",
        json={"description": "  Магазины самокатов.  "},
        headers=headers,
    )
    cleared = await client.patch(
        f"/api/sales/hypotheses/{owner.id}", json={"description": " "}, headers=headers
    )

    assert written.json() == {
        "hypothesis_id": owner.id,
        "name": owner.name,
        "description": "Магазины самокатов.",
    }
    assert cleared.json()["description"] is None


async def test_unknown_hypothesis_is_404_in_words(
    client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    read = await client.get(f"{SEGMENT_URL}?hypothesis=987654", headers=headers)
    started = await start(client, headers, 987654)

    words = "гипотезы №987654 нет — обновите список гипотез"
    assert (read.status_code, read.json()["detail"]) == (404, words)
    assert (started.status_code, started.json()["detail"]) == (404, words)
    assert jobs.enqueued == []


# --- смета и запуск ---------------------------------------------------------------------------------


async def test_estimate_writes_nothing_and_answers_in_words(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    owner = await hypothesis(session)

    response = await client.post(f"{SEGMENT_URL}/estimate", json=body(owner.id), headers=headers)

    assert response.status_code == 200, response.text
    found = response.json()
    assert (found["sites"], found["refusal"]) == (26, None)
    assert found["lines"][0] == (
        "Выдача — стандарт: ключей 3 × страниц 1 — результатов до 30, по $0,0007 за страницу "
        "≈ $0,0021; ждать ≈ 5 мин."
    )
    assert await session.scalar(select(func.count()).select_from(SalesSegmentRunModel)) == 0
    assert jobs.enqueued == []


async def test_a_run_starts_as_a_job_of_the_sales_queue_and_into_the_journal(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    owner = await hypothesis(session)

    response = await start(client, headers, owner.id)

    assert response.status_code == 202, response.text
    run = await session.scalar(select(SalesSegmentRunModel))
    assert run is not None
    assert response.json() == {"run_id": run.id, "job_id": segment_job_id(run.id)}
    [(args, kwargs)] = jobs.enqueued
    assert args == (SEGMENT_JOB, run.id)
    assert (kwargs["unique"], "retry" in kwargs, "result_ttl" in kwargs) == (True, True, True)
    assert (run.keywords, run.country, run.language, run.mode, run.stage) == (
        ["купить горный велосипед", "велосипед для города", "запчасти для велосипеда"],
        "us",
        "en",
        "shadow",
        "queued",
    )
    assert (run.job_id, run.created_by) == (segment_job_id(run.id), SELLER)
    assert run.estimate["sites"] == 26
    journal = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert journal is not None
    assert journal.details is not None
    assert (journal.details["действие"], journal.details["ключей"]) == ("сбор базы продаж", 3)
    # Этап — в записи в момент действия: журнал не узнаёт его потом по строке прогона.
    assert journal.details["этап"] == "sales"


async def test_a_run_above_the_model_cap_is_refused_before_the_queue(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # A4
    monkeypatch.setattr("backend.config.llm.SALES_DAILY_TOKEN_CAP", 3301)
    owner = await hypothesis(session)

    response = await start(client, headers, owner.id)

    assert response.status_code == 409
    assert response.json()["detail"].startswith(
        "Прогон не запущен: смета модели выше остатка дневного потолка: ≈ 46\u00a0800 токенов"
    )
    assert await session.scalar(select(func.count()).select_from(SalesSegmentRunModel)) == 0
    assert jobs.enqueued == []


async def test_a_queue_that_does_not_answer_leaves_no_run_behind(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Очередь задач не ответила — 503 словами, и прогона без задачи в списке нет: он «стоял бы
    в очереди», которой у него нет."""

    def down(*_args: object, **_kwargs: object) -> None:
        raise RedisError("очередь недоступна")

    monkeypatch.setattr(segment_api, "enqueue_once", down)
    owner = await hypothesis(session)

    response = await start(client, headers, owner.id)

    assert response.status_code == 503
    assert await session.scalar(select(func.count()).select_from(SalesSegmentRunModel)) == 0


@pytest.mark.parametrize(
    ("taken", "right"),
    [({"sales": False}, Permission.SALES), ({"run": False}, Permission.RUN)],
)
async def test_without_the_sales_or_the_run_right_the_run_is_refused_in_words(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    jobs: _Jobs,
    taken: dict[str, bool],
    right: Permission,
) -> None:
    owner = await hypothesis(session)
    await make_user(SELLER, permissions=taken)

    response = await start(client, bearer(await sign_in(SELLER)), owner.id)

    assert (response.status_code, response.json()["detail"]) == (403, no_right(right))
    assert jobs.enqueued == []


async def test_estimate_and_review_need_only_the_sales_right(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    owner = await hypothesis(session)
    await make_user(SELLER, permissions={"run": False})
    headers = bearer(await sign_in(SELLER))

    response = await client.post(f"{SEGMENT_URL}/estimate", json=body(owner.id), headers=headers)

    assert response.status_code == 200


def test_the_run_goes_to_the_sales_bulk_queue_and_the_screen_names_its_job() -> None:
    """Прогон — в очередь долгих задач продаж (`worker-sales-bulk`): сбор идёт до получаса и не
    держит разбор ответа лида; строка задачи называет его словами."""
    assert segment_api.sales_bulk_queue().name == SALES_BULK_QUEUE_NAME
    assert job_outcome.KINDS[SEGMENT_JOB] == "сбор базы продаж"
    assert job_outcome.sales_job("x", SEGMENT_JOB, None)


# --- продолжение ------------------------------------------------------------------------------------


async def test_resume_queues_the_same_run_and_a_second_press_is_refused(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    run = await make_run(session, await hypothesis(session))

    first = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)
    second = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert first.status_code == 202, first.text
    assert first.json() == {"run_id": run.id, "job_id": segment_job_id(run.id)}
    assert (second.status_code, second.json()["detail"]) == (409, segment_api.RUN_GOING)
    assert len(jobs.enqueued) == 1


async def test_resume_after_the_job_ended_queues_again(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    run = await make_run(session, await hypothesis(session))
    await client.post(f"{RUNS}/{run.id}/resume", headers=headers)
    jobs.known[segment_job_id(run.id)].finish({"serp": {}})

    again = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert again.status_code == 202
    assert len(jobs.enqueued) == 2


async def test_resume_of_an_unknown_run_is_404(
    client: AsyncClient, headers: dict[str, str], jobs: _Jobs
) -> None:
    response = await client.post(f"{RUNS}/987654/resume", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (
        404,
        "прогона сбора №987654 нет — обновите список прогонов",
    )


# --- отчёт и решение человека -------------------------------------------------------------------


async def _review_site(
    session: AsyncSession,
) -> tuple[SalesSegmentRunModel, SalesSegmentCandidateModel]:
    owner = await hypothesis(session)
    run = await make_run(session, owner, mode=JudgeMode.SHADOW)
    domain = DomainModel(host="velo-maybe-sample.com")
    session.add(domain)
    await session.flush()
    candidate = SalesSegmentCandidateModel(
        run_id=run.id,
        hypothesis_id=owner.id,
        domain_id=domain.id,
        page_url="https://velo-maybe-sample.com/catalog",
        page_title="Велосипеды",
        keyword="купить горный велосипед",
        lane=Lane.REVIEW,
        verdict=Lane.REJECT,
        # Причина старого вида — так её писал судья до 20.10: в базе она не переписывается, экран
        # показывает её коротко, как новую «Отказ: …» (`SegmentSitesWords.reasonWords`).
        reason="судья в наблюдении отрезал бы: мало текста",
        decided_by="model",
    )
    session.add(candidate)
    await session.commit()
    return run, candidate


async def test_the_report_counts_the_lanes_and_the_list_of_sites_names_those_waiting(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Отчёт опрашивается раз в три секунды, пока прогон идёт, — в нём только числа: сайтов «на
    проверку» по одному в нём нет, их отдаёт список сайтов прогона страницами."""
    run, candidate = await _review_site(session)

    response = await client.get(f"{RUNS}/{run.id}", headers=headers)
    listed = await client.get(f"{RUNS}/{run.id}/sites?lane=review", headers=headers)

    assert response.status_code == 200, response.text
    found = response.json()
    assert found["summary"] == {
        "hosts": 1, "accepted": 0, "review": 1, "would_reject": 0, "rejected": 0, "pending": 0,
        "no_address": 0, "leads": 0,
    }  # fmt: skip
    assert found["estimate_lines"] == ["смета"]
    assert "review" not in found
    assert listed.status_code == 200, listed.text
    [row] = listed.json()["rows"]
    assert (row["id"], row["host"], row["verdict"], row["reason"]) == (
        candidate.id,
        "velo-maybe-sample.com",
        "reject",
        "судья в наблюдении отрезал бы: мало текста",
    )


async def test_a_human_decides_a_review_site_once(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    _, candidate = await _review_site(session)
    url = f"/api/sales/segment/candidates/{candidate.id}/decision"

    accepted = await client.post(url, json={"accept": True}, headers=headers)
    again = await client.post(url, json={"accept": False}, headers=headers)
    unknown = await client.post(
        "/api/sales/segment/candidates/987654/decision", json={"accept": True}, headers=headers
    )

    assert accepted.status_code == 200, accepted.text
    assert (accepted.json()["lane"], accepted.json()["decided_by_user"]) == ("accept", SELLER)
    assert (again.status_code, again.json()["detail"]) == (
        409,
        "сайт не ждёт решения: он уже «принят» — обновите отчёт прогона",
    )
    assert unknown.status_code == 404
    await session.refresh(candidate)
    assert (candidate.lane, candidate.decided_by, candidate.verdict) == (
        "accept",
        "human",
        "reject",
    )


async def test_unknown_run_report_is_404(client: AsyncClient, headers: dict[str, str]) -> None:
    response = await client.get(f"{RUNS}/987654", headers=headers)

    assert response.status_code == 404


async def test_segment_routes_are_these(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        for method in methods
        # Правка гипотезы — здесь же (`segment.py`); удаление и «что держит» — у заведения.
        if "/segment" in path
        or (path == "/api/sales/hypotheses/{hypothesis_id}" and method == "patch")
    }

    assert in_app == {
        ("GET", "/api/sales/segment"),
        ("PATCH", "/api/sales/hypotheses/{hypothesis_id}"),
        ("POST", "/api/sales/segment/estimate"),
        ("POST", "/api/sales/segment/runs"),
        ("POST", "/api/sales/segment/runs/{run_id}/resume"),
        # «Повторить сейчас» из отчёта прогона: адреса ждущим повтора или очистка ждущих лидов.
        ("POST", "/api/sales/segment/runs/{run_id}/again"),
        ("GET", "/api/sales/segment/runs/{run_id}"),
        ("POST", "/api/sales/segment/candidates/{candidate_id}/decision"),
        # Отмена решения человека (`features/sales/segment_undo.py`) — тот же адрес.
        ("DELETE", "/api/sales/segment/candidates/{candidate_id}/decision"),
        # Все сайты прогона и пересуд — `api/sales/segment_sites.py`.
        ("GET", "/api/sales/segment/runs/{run_id}/sites"),
        ("GET", "/api/sales/segment/runs/{run_id}/rejudge"),
        ("POST", "/api/sales/segment/runs/{run_id}/rejudge"),
        # Помощник описания сегмента — `api/sales/segment_helper.py`.
        ("POST", "/api/sales/segment/helper"),
    }
