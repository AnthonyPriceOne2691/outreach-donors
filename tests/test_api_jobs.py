"""Маршрут исхода задачи: права и форма ответа."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.ops.job_outcome import JobOutcome
from fastapi import FastAPI
from httpx import AsyncClient
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def outcomes(monkeypatch: pytest.MonkeyPatch) -> dict[str, JobOutcome]:
    known: dict[str, JobOutcome] = {}
    monkeypatch.setattr("backend.api.jobs.routes.job_outcome", known.get)
    return known


async def test_retry_wait_is_shown_with_reason_and_time(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, outcomes: dict[str, JobOutcome]
) -> None:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    token = await sign_in("оператор@site.com")
    outcomes["j1"] = JobOutcome(
        job_id="j1",
        kind="сборка писем",
        state="retry_wait",
        error="ConnectError: сеть",
        retries_left=2,
        next_try_at=AT,
    )

    response = await client.get("/api/jobs/j1", headers=bearer(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["title"] == "ждёт повтора"
    assert body["error"] == "ConnectError: сеть"
    assert body["retries_left"] == 2


async def test_unknown_job_is_404_with_a_hint(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, outcomes: dict[str, JobOutcome]
) -> None:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    token = await sign_in("оператор@site.com")

    response = await client.get("/api/jobs/нет-такой", headers=bearer(token))

    assert response.status_code == 404
    assert "неделю" in response.json()["detail"]


async def test_needs_a_pass(client: AsyncClient, outcomes: dict[str, JobOutcome]) -> None:
    response = await client.get("/api/jobs/j1")
    assert response.status_code == 401


def test_route_table_is_complete(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith("/api/jobs")
        for method in methods
    }
    assert in_app == {("GET", "/api/jobs/{job_id}")}
