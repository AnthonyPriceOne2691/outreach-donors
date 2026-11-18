"""Кнопки плана работ — маршруты `/api/sales/plans/{N}/…` и `/api/sales/plan-hypotheses/{N}/…`.

Остановить и продолжить план и гипотезу плана, изменить качество сайтов, «Собрать ещё N», «Добавить
ключи» текстом и книгой. Ответ — план или гипотеза плана, как в списке планов (с этапом, волной и
проблемами словами); отказ по состоянию — 409 словами, негодная просьба — 400 словами, нет строки —
404; право — `sales` и `run`, без них — 403 словами, и журнал пуст. Действие — в журнале с автором.
Поведение кнопок — `test_sales_plan_actions.py`; здесь — стык маршрута.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.sales.plans import ONE_SOURCE
from backend.features.access.permissions import no_right
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales.plan_actions import NOTHING_TO_EDIT, PLAN_STOPPED, STOPPED_BY_HAND
from backend.features.sales.plan_models import PlanStage, SalesPlanHypothesisModel
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_plan_actions import book_of
from tests.test_sales_plan_waves import sheet

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
SELLER = "seller@ours.example.test"
PLANS = "/api/sales/plans"
ROWS = "/api/sales/plan-hypotheses"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
WORDS = ("ремонт кофемашин", "dental implants price", "家具 批发")

#: Кнопки: метод, путь с `{plan}` или `{row}`, тело JSON и форма.
BUTTONS: list[tuple[str, str, dict[str, Any] | None, dict[str, str] | None]] = [
    ("POST", PLANS + "/{plan}/stop", None, None),
    ("POST", PLANS + "/{plan}/resume", None, None),
    ("POST", ROWS + "/{row}/stop", None, None),
    ("POST", ROWS + "/{row}/resume", None, None),
    ("PATCH", ROWS + "/{row}/quality", {"target": 200}, None),
    ("POST", ROWS + "/{row}/more", {"n": 10}, None),
    ("POST", ROWS + "/{row}/keywords", None, {"text": "Fußpflege Praxis"}),
]


@pytest.fixture
async def seller(make_user: MakeUser) -> UserModel:
    return await make_user(SELLER)


@pytest.fixture
async def headers(seller: UserModel, sign_in: SignIn) -> dict[str, str]:
    return bearer(await sign_in(SELLER))


@pytest.fixture
async def row(session: AsyncSession) -> SalesPlanHypothesisModel:
    found = await sheet(session, keys=WORDS, target=100)
    found.stage = PlanStage.COLLECTING.value
    await session.commit()
    return found


async def press(
    client: AsyncClient,
    headers: dict[str, str],
    button: tuple[str, str, dict[str, Any] | None, dict[str, str] | None],
    row: SalesPlanHypothesisModel,
) -> Response:
    method, path, body, form = button
    url = path.format(plan=row.plan_id, row=row.id)
    return await client.request(method, url, json=body, data=form, headers=headers)


async def journal(session: AsyncSession) -> list[tuple[int | None, str]]:
    found = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.USER_UPDATED)
        .order_by(AuditLogModel.id)
    )
    return [(log.user_id, str((log.details or {}).get("действие"))) for log in found.all()]


@pytest.mark.parametrize("button", BUTTONS, ids=[path for _, path, _, _ in BUTTONS])
@pytest.mark.parametrize(
    ("taken", "right"), [({"sales": False}, Permission.SALES), ({"run": False}, Permission.RUN)]
)
async def test_every_button_needs_sales_and_run_and_writes_nothing_without(
    session: AsyncSession,
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    row: SalesPlanHypothesisModel,
    button: tuple[str, str, dict[str, Any] | None, dict[str, str] | None],
    taken: dict[str, bool],
    right: Permission,
) -> None:
    await make_user(SELLER, permissions=taken)
    headers = bearer(await sign_in(SELLER))

    response = await press(client, headers, button, row)

    assert (response.status_code, response.json()["detail"]) == (403, no_right(right))
    assert await journal(session) == []


async def test_stop_answers_the_sheet_as_listed_and_a_second_stop_is_refused_in_words(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    seller: UserModel,
    row: SalesPlanHypothesisModel,
) -> None:
    stopped = await client.post(f"{ROWS}/{row.id}/stop", headers=headers)
    again = await client.post(f"{ROWS}/{row.id}/stop", headers=headers)
    listed = (await client.get(PLANS, headers=headers)).json()

    assert stopped.status_code == 200, stopped.text
    found = stopped.json()
    assert (found["id"], found["stage"], found["wave_page"], found["keywords"]) == (
        row.id,
        "stopped",
        1,
        3,
    )
    assert [(item["code"], item["words"]) for item in found["problems"]] == [
        ("stopped", STOPPED_BY_HAND)
    ]
    assert listed["plans"][0]["hypotheses"] == [found]
    assert again.status_code == 409
    assert again.json()["detail"].endswith("уже остановлена")
    assert await journal(session) == [(seller.id, "гипотеза плана остановлена")]


async def test_the_plan_stops_and_resumes_and_answers_the_plan(
    client: AsyncClient, headers: dict[str, str], row: SalesPlanHypothesisModel
) -> None:
    stopped = await client.post(f"{PLANS}/{row.plan_id}/stop", headers=headers)
    resumed = await client.post(f"{PLANS}/{row.plan_id}/resume", headers=headers)
    again = await client.post(f"{PLANS}/{row.plan_id}/resume", headers=headers)

    assert (stopped.json()["status"], stopped.json()["problem"]) == ("paused", PLAN_STOPPED)
    assert (resumed.json()["status"], resumed.json()["problem"]) == ("running", None)
    assert (again.status_code, again.json()["detail"]) == (409, "план «План недели» уже идёт")


async def test_quality_more_and_resume_answer_the_sheet(
    client: AsyncClient, headers: dict[str, str], row: SalesPlanHypothesisModel
) -> None:
    quality = await client.patch(
        f"{ROWS}/{row.id}/quality",
        json={"max_pages": 2, "target": 300, "excluded_countries": ["ru"]},
        headers=headers,
    )
    more = await client.post(f"{ROWS}/{row.id}/more", json={"n": 50}, headers=headers)
    await client.post(f"{ROWS}/{row.id}/stop", headers=headers)
    resumed = await client.post(f"{ROWS}/{row.id}/resume", headers=headers)

    assert quality.status_code == 200, quality.text
    assert (quality.json()["max_pages"], quality.json()["target"]) == (2, 300)
    assert more.json()["target"] == 350
    assert (resumed.json()["stage"], resumed.json()["problems"]) == ("collecting", [])


@pytest.mark.parametrize(
    ("body", "status", "words"),
    [
        ({}, 400, NOTHING_TO_EDIT),
        ({"other_countries": "somewhere"}, 400, "«компании из других стран»"),
    ],
)
async def test_quality_refusals_are_in_words(
    client: AsyncClient,
    headers: dict[str, str],
    row: SalesPlanHypothesisModel,
    body: dict[str, Any],
    status: int,
    words: str,
) -> None:
    response = await client.patch(f"{ROWS}/{row.id}/quality", json=body, headers=headers)

    assert response.status_code == status
    assert words in response.json()["detail"]


async def test_a_depth_off_the_book_steps_is_refused_before_the_button(
    client: AsyncClient, headers: dict[str, str], row: SalesPlanHypothesisModel
) -> None:
    response = await client.patch(
        f"{ROWS}/{row.id}/quality", json={"max_pages": 4}, headers=headers
    )

    assert response.status_code == 422


async def test_keys_go_in_as_text_or_as_a_book_of_one_sheet(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    seller: UserModel,
    row: SalesPlanHypothesisModel,
) -> None:
    as_text = await client.post(
        f"{ROWS}/{row.id}/keywords",
        data={"text": "Fußpflege Praxis\nремонт кофемашин"},
        headers=headers,
    )
    data = book_of([["Ключи"], ["طباعة ثلاثية الأبعاد"]])
    as_book = await client.post(
        f"{ROWS}/{row.id}/keywords", files={"file": ("ключи.xlsx", data, XLSX)}, headers=headers
    )
    repeated = await client.post(
        f"{ROWS}/{row.id}/keywords", data={"text": "ремонт кофемашин"}, headers=headers
    )

    assert as_text.status_code == 200, as_text.text
    assert {key: as_text.json()[key] for key in ("added", "known", "rejected")} == {
        "added": 1,
        "known": 1,
        "rejected": 0,
    }
    assert (as_book.json()["added"], as_book.json()["hypothesis"]["keywords"]) == (1, 5)
    assert (repeated.status_code, repeated.json()["detail"]) == (
        400,
        f"новых ключей нет: все 1 уже есть у гипотезы «{as_text.json()['hypothesis']['name']}»",
    )
    assert await journal(session) == [
        (seller.id, "ключи гипотезы плана добавлены"),
        (seller.id, "ключи гипотезы плана добавлены"),
    ]


@pytest.mark.parametrize("form", [{}, {"text": "  "}])
async def test_keys_need_text_or_a_book(
    client: AsyncClient,
    headers: dict[str, str],
    row: SalesPlanHypothesisModel,
    form: dict[str, str],
) -> None:
    response = await client.post(f"{ROWS}/{row.id}/keywords", data=form, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (400, ONE_SOURCE)


async def test_a_file_that_is_not_a_book_is_refused_in_words(
    client: AsyncClient, headers: dict[str, str], row: SalesPlanHypothesisModel
) -> None:
    response = await client.post(
        f"{ROWS}/{row.id}/keywords", files={"file": ("k.pdf", b"%PDF-1.4", XLSX)}, headers=headers
    )

    assert response.status_code == 400
    assert "не книга Excel (.xlsx)" in response.json()["detail"]


@pytest.mark.parametrize(
    ("path", "words"),
    [
        (f"{ROWS}/999999/stop", "гипотезы плана №999999 нет — обновите список планов"),
        (f"{PLANS}/999999/stop", "плана №999999 нет — обновите список планов"),
    ],
)
async def test_an_unknown_row_or_plan_is_named(
    client: AsyncClient, headers: dict[str, str], path: str, words: str
) -> None:
    response = await client.post(path, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (404, words)
