"""План работ — маршруты `/api/sales/plans…`: превью книги, сохранение по отпечатку, список.

Превью ничего не пишет и называет листы, ключи счётом и примерами, смету диапазоном и почему план не
сохранить. Сохранение разбирает книгу заново и идёт только по отпечатку превью: изменили выбор окна —
409; правила сохранения — 400 словами; права — `sales`, на сохранение ещё и `run`. Книги собираются в
памяти тестами разбора; настройки сметы — те же, что у её тестов.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.sales.plans import STALE
from backend.features.access.permissions import no_right
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import hypotheses
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.plan_models import SalesPlanKeywordModel, SalesPlanModel
from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_plan_file import KEYS, book, changed, rows
from tests.test_sales_segment_estimate import _settings

__all__ = ["_settings"]  # настройки сметы — те же, что у её тестов

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
SELLER = "seller@ours.example.test"
URL = "/api/sales/plans"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FORM = {"mode": "economy", "cap_usd": "50", "hypothesis_cap_usd": "10", "goal": "300"}
SECOND = changed(Название="Курьеры — пример", Где_ищем="Узбекистан", Найти_сайтов="")


def workbook() -> bytes:
    return book(("Пилот 1", rows(changed(), KEYS)), ("Пилот 2", rows(SECOND, KEYS[:2])))


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def post(
    client: AsyncClient,
    url: str,
    headers: dict[str, str],
    form: dict[str, str],
    data: bytes | None = None,
    name: str = "План недели.xlsx",
) -> Response:
    files = {"file": (name, workbook() if data is None else data, XLSX)}
    return await client.post(url, files=files, data=form, headers=headers)


async def count(session: AsyncSession, model: type[Any]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_the_preview_names_sheets_keys_and_the_estimate_and_writes_nothing(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await post(client, f"{URL}/preview", headers, FORM)

    assert response.status_code == 200, response.text
    found = response.json()
    first, second = found["sheets"]
    assert (first["sheet"], first["name"], first["markets"], first["language"]) == (
        "Пилот 1",
        "Логистика — пример",
        ["kz"],
        "ru",
    )
    assert (first["target"], first["goal"], second["target"], second["goal"]) == (
        5000,
        5000,
        None,
        300,
    )
    assert first["keys"] == {
        "taken": 3,
        "blank": 0,
        "repeats": 0,
        "rejected": 0,
        "examples": list(KEYS),
        "rejected_examples": {},
    }
    assert first["high"]["results"] == 3 * 5 * 10
    assert first["low"]["results"] == 3 * 10
    assert found["high"]["queries"] == first["high"]["queries"] + second["high"]["queries"]
    assert found["refusals"] == []
    assert len(found["digest"]) == 16
    assert found["skipped"] == {"Как заполнять": "инструкция — не гипотеза"}
    assert any("письма не соберутся" in words for words in first["warnings"])
    assert (await count(session, SalesPlanModel), await count(session, SalesHypothesisModel)) == (
        0,
        0,
    )


async def test_the_preview_says_why_the_plan_cannot_be_saved(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await post(client, f"{URL}/preview", headers, {"mode": "fast"})

    assert response.status_code == 200, response.text
    assert response.json()["refusals"] == [
        "у листов «Пилот 2» нет цели «Найти сайтов», и общая цель не задана — впишите цель в "
        "лист или задайте общую",
        "потолок плана обязателен — задайте его в долларах, больше нуля",
        "потолок гипотезы обязателен — задайте его в долларах, больше нуля",
    ]
    assert response.json()["serp_mode"] == "live"


async def test_the_plan_is_saved_by_the_digest_of_the_preview_and_listed(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    seen = (await post(client, f"{URL}/preview", headers, FORM)).json()

    saved = await post(client, URL, headers, {**FORM, "digest": seen["digest"]})

    assert saved.status_code == 201, saved.text
    plan = saved.json()
    assert (plan["name"], plan["mode"], plan["cap_usd"], plan["status"]) == (
        "План недели",
        "economy",
        50.0,
        "running",
    )
    assert [
        (row["sheet"], row["name"], row["target"], row["keywords"], row["cap_usd"])
        for row in plan["hypotheses"]
    ] == [
        ("Пилот 1", "Логистика — пример", 5000, 3, 10.0),
        ("Пилот 2", "Курьеры — пример", 300, 2, 10.0),
    ]
    assert await count(session, SalesPlanKeywordModel) == 5
    listed = await client.get(URL, headers=headers)
    one = await client.get(f"{URL}/{plan['id']}", headers=headers)
    assert [found["id"] for found in listed.json()["plans"]] == [plan["id"]]
    assert one.json() == plan


async def test_a_change_after_the_preview_is_refused_as_stale(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    seen = (await post(client, f"{URL}/preview", headers, FORM)).json()

    response = await post(client, URL, headers, {**FORM, "cap_usd": "60", "digest": seen["digest"]})

    assert (response.status_code, response.json()["detail"]) == (409, STALE)
    assert await count(session, SalesPlanModel) == 0


async def test_a_sheet_named_as_an_existing_hypothesis_is_refused_in_words(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    await hypotheses.add(session, "Логистика — пример", None)
    await session.commit()
    seen = (await post(client, f"{URL}/preview", headers, FORM)).json()

    response = await post(client, URL, headers, {**FORM, "digest": seen["digest"]})

    assert any("измените поле «Название»" in words for words in seen["refusals"])
    assert response.status_code == 400
    assert "лист «Пилот 1»: имя «Логистика — пример» уже у гипотезы" in response.json()["detail"]
    assert await count(session, SalesPlanModel) == 0


async def test_a_file_that_is_not_a_book_is_refused_in_words(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    response = await post(client, f"{URL}/preview", headers, FORM, b"%PDF-1.4", "plan.pdf")

    assert response.status_code == 400
    assert "не книга Excel (.xlsx)" in response.json()["detail"]


@pytest.mark.parametrize(
    ("taken", "right", "save"),
    [
        ({"sales": False}, Permission.SALES, False),
        ({"run": False}, Permission.RUN, True),
    ],
)
async def test_the_preview_needs_sales_and_saving_needs_run_too(
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    taken: dict[str, bool],
    right: Permission,
    save: bool,
) -> None:
    await make_user(SELLER, permissions=taken)
    headers = bearer(await sign_in(SELLER))

    response = await post(
        client, URL if save else f"{URL}/preview", headers, {**FORM, "digest": "x"}
    )

    assert (response.status_code, response.json()["detail"]) == (403, no_right(right))


async def test_an_unknown_plan_is_named(client: AsyncClient, headers: dict[str, str]) -> None:
    response = await client.get(f"{URL}/999999", headers=headers)

    assert (response.status_code, response.json()["detail"]) == (
        404,
        "плана №999999 нет — обновите список планов",
    )
