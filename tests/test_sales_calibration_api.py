"""Качество агента продаж через API — `GET /sales/agent-quality` (срез 6.4).

Журнал — тот же подложенный, что у чисел (`test_sales_calibration.journal_world`): две
версии агента, все виды решений. Права — `sales`; отказы словами. Поля ответа сверяются с
типами экрана (`frontend/src/api/salesQuality.ts`): поле, переименованное на сервере, экран
показал бы пустым — без ошибки.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path

import pytest
from backend.api.sales.calibration import (
    AgentQualityView,
    ChangeRow,
    CohortRow,
    CohortView,
    DiffPart,
    GateView,
    QualityCounts,
    QualityLimits,
    ReasonRow,
    SituationRow,
)
from backend.config import sales as sales_cfg
from backend.features.access.permissions import no_right
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_calibration import OLD, current_cohort, journal_world

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
QUALITY = "/api/sales/agent-quality"
NO_RIGHT = no_right(Permission.SALES)
TYPES = (Path(__file__).resolve().parent.parent / "frontend/src/api/salesQuality.ts").read_text(
    encoding="utf-8"
)


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def test_the_screen_gets_the_current_version_and_every_version_apart(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    made = await journal_world(session)
    await session.commit()
    monkeypatch.setattr(sales_cfg, "AUTOREPLY_MIN_N", 3)
    now = await current_cohort(session)

    response = await client.get(QUALITY, headers=headers)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["limits"] == {"min_n": 3, "as_is": 80, "silence": 90, "settle_days": 3}
    assert body["shown"]["id"] == body["current"]["id"] == now.id
    assert body["shown"]["reply"] == now.reply
    assert body["counts"] == {
        "as_is": 2,
        "edited": 1,
        "rejected": 1,
        "true_silence": 1,
        "false_silence": 1,
        "held_sent": 1,
        "held_rejected": 0,
        "held_waiting": 1,
        "waiting": 2,
        "auto": 1,
    }
    versions = [(row["cohort"]["id"], row["current"]) for row in body["cohorts"]]
    assert versions == [(now.id, True), (OLD.id, False)]
    assert body["cohorts"][1]["counts"]["rejected"] == 2
    price = next(row for row in body["situations"] if row["situation"] == "asks_price")
    assert price["title"] == "спрашивает цену"
    assert price["gate"] == {"state": "failed", "said": ["без правки 66,7% (2 из 3) — ниже 80%"]}
    reasons = {row["reason"]: row["count"] for row in body["reasons"]}
    assert reasons["факт не из базы"] == 1
    assert reasons["длинно"] == 0  # прежняя версия в текущую не попала
    edited = body["changes"][0]
    assert (edited["draft_id"], edited["decision"], edited["held"]) == (
        made["edited"].id,
        "edited",
        False,
    )
    assert edited["sent"] == "Спасибо за вопрос. Цену назовём на созвоне."
    assert {part["kind"] for part in edited["parts"]} == {"same", "removed", "added"}
    held = body["changes"][2]
    assert (held["held"], held["held_reason"]) == (True, "судья: сумма не из базы")


async def test_an_earlier_version_is_shown_by_its_number(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    await journal_world(session)
    await session.commit()

    body = (await client.get(QUALITY, params={"cohort": OLD.id}, headers=headers)).json()

    assert body["shown"]["id"] == OLD.id
    assert body["shown"]["kb"] == OLD.kb
    assert (body["counts"]["as_is"], body["counts"]["rejected"]) == (1, 2)
    assert {row["reason"]: row["count"] for row in body["reasons"]}["длинно"] == 1


async def test_an_unknown_version_is_refused_in_words(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get(QUALITY, params={"cohort": "0123456789"}, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (
        404,
        "версии агента №0123456789 в журнале нет — обновите экран",
    )


async def test_a_version_number_of_another_form_is_refused(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await client.get(QUALITY, params={"cohort": "../etc"}, headers=headers)

    assert response.status_code == 422


async def test_an_empty_journal_answers_with_the_current_version_and_no_versions(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    body = (await client.get(QUALITY, headers=headers)).json()

    assert body["cohorts"] == []
    assert body["shown"]["id"] == body["current"]["id"] == (await current_cohort(session)).id
    assert body["changes"] == []
    assert all(row["gate"]["state"] == "few" for row in body["situations"])


async def test_without_the_sales_right_the_screen_refuses_in_words(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user(SELLER, permissions={"sales": False})

    response = await client.get(QUALITY, headers=bearer(await sign_in(SELLER)))

    assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)


def _screen_fields(name: str) -> set[str]:
    """Поля интерфейса экрана `export interface <name> { … }` — файл читается как текст."""
    found = re.search(rf"export interface {name} \{{\n(.*?)\n\}}", TYPES, re.DOTALL)
    assert found is not None, f"в salesQuality.ts нет интерфейса {name}"
    return set(re.findall(r"^  ([a-z_]+):", found.group(1), re.MULTILINE))


@pytest.mark.parametrize(
    "model",
    [
        AgentQualityView,
        QualityLimits,
        QualityCounts,
        GateView,
        CohortView,
        CohortRow,
        SituationRow,
        ReasonRow,
        DiffPart,
        ChangeRow,
    ],
)
def test_screen_reads_the_answer_by_the_server_names(model: type[BaseModel]) -> None:
    assert _screen_fields(model.__name__) == set(model.model_fields)


async def test_quality_route_is_this_one(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith(QUALITY)
        for method in methods
    }

    assert in_app == {("GET", QUALITY)}
