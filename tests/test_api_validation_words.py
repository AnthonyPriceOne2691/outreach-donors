"""Отказ разбора запроса (422) — по-русски и с именем поля (аудит 10.10.2026, QA №5).

Экран показывает первую строку списка `detail` как есть (`frontend/src/api/client.ts`),
и до этой правки каждый 422 на любом экране шёл по-английски: цена «сто евро» —
«Input should be a valid decimal», а свой текст проверки схемы — с приставкой
«Value error, ». Форма ответа прежняя: `detail` — список с `msg`, `loc` и `type`.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.errors import said_in_russian
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from httpx import AsyncClient
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
async def admin(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


def _first(response: Any) -> dict[str, Any]:
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert isinstance(detail, list), detail
    first: dict[str, Any] = detail[0]
    assert {"msg", "loc", "type"} <= set(first), "форма ответа — та, что ждёт экран"
    return first


async def test_a_price_in_words_is_refused_in_russian(client: AsyncClient, admin: str) -> None:
    """Ровно случай QA: цена в ответе донора словами — «нужно число», с именем поля."""
    response = await client.patch(
        "/api/replies/1", json={"price_white": "сто евро"}, headers=bearer(admin)
    )

    first = _first(response)
    assert first["msg"] == "Поле «price_white»: нужно число"
    assert (first["type"], first["loc"]) == ("decimal_parsing", ["body", "price_white"])


@pytest.mark.parametrize(
    ("method", "path", "body", "said"),
    [
        (
            "POST",
            "/api/runs/estimate",
            {"country": "us"},
            "Поле «keywords»: обязательно, а в запросе его нет",
        ),
        (
            "POST",
            "/api/runs/estimate",
            {"keywords": ["ключ"], "country": "us", "cap": "сто"},
            "Поле «cap»: нужно целое число",
        ),
        (
            "POST",
            "/api/runs/estimate",
            {"keywords": ["ключ"], "country": "us", "cap": 0},
            "Поле «cap»: нужно не меньше 1",
        ),
        (
            "POST",
            "/api/runs/estimate",
            {"keywords": [], "country": "us"},
            "Поле «keywords»: элементов — не меньше 1",
        ),
        ("GET", "/api/runs?page=0", None, "Поле «page»: нужно не меньше 1"),
        ("GET", "/api/runs?page=первая", None, "Поле «page»: нужно целое число"),
    ],
    ids=["нет поля", "не число", "меньше предела", "пустой список", "адрес", "адрес словами"],
)
async def test_common_refusals_name_the_field_in_russian(
    client: AsyncClient,
    admin: str,
    method: str,
    path: str,
    body: dict[str, Any] | None,
    said: str,
) -> None:
    response = await client.request(method, path, json=body, headers=bearer(admin))

    assert _first(response)["msg"] == said


async def test_a_choice_names_what_is_allowed(client: AsyncClient, admin: str) -> None:
    """Значение не из списка — список словами, «или» вместо «or»."""
    response = await client.get("/api/selection", params={"judge": "robot"}, headers=bearer(admin))

    said = _first(response)["msg"]
    assert said.startswith("Поле «judge»: нужно одно из: "), said
    assert " or " not in said
    assert " или " in said


async def test_not_json_says_so(client: AsyncClient, admin: str) -> None:
    response = await client.post(
        "/api/runs/estimate",
        content="{keywords: ",
        headers={**bearer(admin), "content-type": "application/json"},
    )

    assert _first(response)["msg"] == "Тело запроса — не JSON"


async def test_own_text_comes_without_the_english_prefix(
    client: AsyncClient, admin: str, make_user: MakeUser
) -> None:
    """Свой текст проверки схемы (`ValueError`) — как написан: без «Value error, »."""
    target = await make_user("кто-то@site.com")

    response = await client.patch(f"/api/users/{target.id}", json={}, headers=bearer(admin))

    assert _first(response)["msg"] == "Нечего менять: укажите роль, права или активность"


@pytest.mark.parametrize(
    ("error", "said"),
    [
        (
            {"type": "decimal_parsing", "loc": ("body", "price"), "msg": "Input should be ..."},
            "Поле «price»: нужно число",
        ),
        (
            {"type": "string_too_long", "loc": ("body", "subject"), "msg": "String should ...",
             "ctx": {"max_length": 512}},
            "Поле «subject»: знаков — не больше 512",
        ),
        (
            {"type": "too_long", "loc": ("body", "keywords"), "msg": "List should ...",
             "ctx": {"field_type": "List", "max_length": 500, "actual_length": 501}},
            "Поле «keywords»: элементов — не больше 500, пришло 501",
        ),
        (
            {"type": "string_too_short", "loc": ("body", "items", 2, "name"), "msg": "...",
             "ctx": {"min_length": 3}},
            "Поле «items[2].name»: знаков — не меньше 3",
        ),
        (
            {"type": "greater_than", "loc": ("query", "limit"), "msg": "...", "ctx": {"gt": 0}},
            "Поле «limit»: нужно больше 0",
        ),
        (
            {"type": "model_attributes_type", "loc": ("body",), "msg": "Input should be ..."},
            "Тело запроса: нужен объект",
        ),
        (
            {"type": "value_error", "loc": ("body", "email"),
             "msg": "Value error, The email address is not valid."},
            "Поле «email»: значение не подходит",
        ),
        (
            {"type": "brand_new_check", "loc": ("body", "x"), "msg": "Something odd happened"},
            "Поле «x»: значение не подходит",
        ),
        (
            {"type": "greater_than", "loc": ("body", "x"), "msg": "...", "ctx": {}},
            "Поле «x»: значение не подходит",
        ),
        (
            {"type": "depth_out_of_range", "loc": ("body", "depth_pages"),
             "msg": "Глубина выдачи — от 10 до 100 результатов на ключ"},
            "Глубина выдачи — от 10 до 100 результатов на ключ",
        ),
    ],
    ids=["число", "длинная строка", "длинный список", "вложенное поле", "больше нуля",
         "тело целиком", "чужой ValueError", "незнакомый тип", "нет подстановки", "свой текст"],
)  # fmt: skip
def test_every_refusal_reads_in_russian(error: dict[str, Any], said: str) -> None:
    """Знакомый тип — фразой, незнакомый и чужой английский текст — общими словами
    с именем поля, свой русский текст — как есть."""
    assert said_in_russian(error) == said
