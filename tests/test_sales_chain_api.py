"""Цепочка писем продаж через API — срез 4.6, часть 1: набор, запись шага, предпросмотр, 403.

Маршруты читают и пишут базу дерева; тексты шаблонов и подписи выдуманы, адреса —
`*.example.test`. Отказы — словами сервера: экран показывает `detail` целиком. Журнал
проверяется по базе: у правки с экрана есть автор.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import chain, hypotheses, sender
from backend.features.sales.chain_text import step_template
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
CHAIN = "/api/sales/chain"
PREVIEW = "/api/sales/chain/preview"
NO_RIGHT = "Действие «sales» недоступно этой учётке"

FIRST_BODY = (
    "[greeting] rewrite\nHello {{name}},\n\n"
    "[opening] rewrite\nThis is a made-up test opening about {{site}} for {{company}} only.\n\n"
    "[offer] fixed\nTest offer: nothing real is sold here."
)
FIRST = {"step": 1, "language": "en", "subject": "Test for {{company}}", "body": FIRST_BODY}
FOLLOW = {"step": 2, "language": "en", "body": "[reminder] fixed\nJust a made-up test reminder."}
EVERY_STEP_MISSING = ["первого письма", "первой добивки", "второй добивки"]

#: Каждый маршрут цепочки под правом `sales`: метод, путь, тело.
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", CHAIN, None),
    ("POST", CHAIN, FIRST),
    ("POST", PREVIEW, FIRST),
]


@pytest.fixture
async def seller(make_user: MakeUser, sign_in: SignIn) -> tuple[UserModel, dict[str, str]]:
    user = await make_user(SELLER)
    return user, bearer(await sign_in(SELLER))


async def _post(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post(CHAIN, json=body, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def _chain(
    client: AsyncClient, headers: dict[str, str], hypothesis: int | None = None
) -> dict[str, Any]:
    params = {} if hypothesis is None else {"hypothesis": hypothesis}
    response = await client.get(CHAIN, params=params, headers=headers)
    assert response.status_code == 200, response.text
    return dict(response.json())


def _states(view: dict[str, Any]) -> dict[str, tuple[str, list[str]]]:
    return {state["language"]: (state["source"], state["missing"]) for state in view["chains"]}


# --- набор ---------------------------------------------------------------------------------


async def test_empty_set_says_the_chain_is_not_set_in_each_language(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller

    view = await _chain(client, headers)

    assert (view["hypothesis_id"], view["rows"]) == (None, [])
    assert _states(view) == {
        "ru": ("common", EVERY_STEP_MISSING),
        "en": ("common", EVERY_STEP_MISSING),
    }
    assert (view["steps"], view["languages"]) == ([1, 2, 3], ["ru", "en"])
    assert view["placeholders"] == ["name", "company", "site"]
    assert view["limits"] == {"subject": 255, "body": 10_000}


async def test_saved_step_is_written_with_the_author_and_listed_with_its_zones(
    client: AsyncClient, session: AsyncSession, seller: tuple[UserModel, dict[str, str]]
) -> None:
    user, headers = seller

    card = await _post(client, headers, FIRST | {"subject": "  Test   for {{company}} "})
    view = await _chain(client, headers)

    assert {k: card[k] for k in ("hypothesis_id", "step", "language", "subject", "updated_by")} == {
        "hypothesis_id": None,
        "step": 1,
        "language": "en",
        "subject": "Test for {{company}}",
        "updated_by": SELLER,
    }
    assert [(zone["name"], zone["kind"]) for zone in card["zones"]] == [
        ("greeting", "rewrite"),
        ("opening", "rewrite"),
        ("offer", "fixed"),
    ]
    assert [row["id"] for row in view["rows"]] == [card["id"]]
    assert _states(view)["en"] == ("common", ["первой добивки", "второй добивки"])
    authors = await session.scalars(
        select(AuditLogModel.user_id).where(AuditLogModel.action == AuditAction.SALES_CHAIN_CHANGED)
    )
    assert list(authors) == [user.id]


async def test_editing_the_text_on_the_screen_changes_the_chain_version(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller
    await _post(client, headers, FIRST)
    before = {s["language"]: s["version"] for s in (await _chain(client, headers))["chains"]}

    await _post(client, headers, FIRST | {"body": FIRST_BODY.replace("only.", "and more.")})
    after = {s["language"]: s["version"] for s in (await _chain(client, headers))["chains"]}

    assert after["en"] != before["en"]
    assert after["ru"] == before["ru"]
    assert after["en"] == chain.version_of(
        [step_template(**(FIRST | {"body": FIRST_BODY.replace("only.", "and more.")}))]
    )


async def test_hypothesis_uses_the_common_chain_until_it_has_its_own_steps(
    client: AsyncClient, session: AsyncSession, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller
    hypothesis = await hypotheses.add(session, "Тестовая гипотеза", None)
    await session.commit()
    await _post(client, headers, FIRST)

    before = await _chain(client, headers, hypothesis.id)
    await _post(client, headers, FIRST | {"hypothesis_id": hypothesis.id, "subject": "Own test"})
    after = await _chain(client, headers, hypothesis.id)

    assert (before["rows"], _states(before)["en"][0]) == ([], "common")
    assert [row["subject"] for row in after["rows"]] == ["Own test"]
    assert {lang: source for lang, (source, _) in _states(after).items()} == {
        "en": "own",
        "ru": "common",
    }
    assert [row["hypothesis_id"] for row in (await _chain(client, headers))["rows"]] == [None]


# --- отказы словами --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "code", "words"),
    [
        (FIRST | {"subject": "Re: test"}, 400, "начинается с «Re:»"),
        (FIRST | {"body": FIRST_BODY + " Our DR 45 site"}, 400, "метрики Ahrefs"),
        (
            FIRST | {"body": FIRST_BODY.replace("{{name}}", "{{nam}}")},
            400,
            "подстановки {{nam}} нет",
        ),
        (FOLLOW | {"subject": "Test"}, 400, "у добивки темы нет"),
        (FIRST | {"step": 5}, 400, "шага 5 нет"),
        (FIRST | {"hypothesis_id": 987_654}, 404, "гипотезы №987654 нет"),
    ],
)
async def test_refusals_come_in_words_and_nothing_is_written(
    client: AsyncClient,
    session: AsyncSession,
    seller: tuple[UserModel, dict[str, str]],
    body: dict[str, Any],
    code: int,
    words: str,
) -> None:
    _, headers = seller

    response = await client.post(CHAIN, json=body, headers=headers)

    assert response.status_code == code, response.text
    assert words in response.json()["detail"]
    assert await chain.rows(session, None) == []


async def test_a_typo_in_a_field_name_is_refused_not_ignored(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller

    response = await client.post(CHAIN, json=FIRST | {"subjekt": "x"}, headers=headers)

    assert response.status_code == 422


async def test_unknown_hypothesis_set_is_refused_in_words(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller

    response = await client.get(CHAIN, params={"hypothesis": 987_654}, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (
        404,
        "гипотезы №987654 нет — обновите список гипотез",
    )


# --- предпросмотр ----------------------------------------------------------------------------


async def test_preview_shows_made_up_values_and_the_sender_signature_and_writes_nothing(
    client: AsyncClient, session: AsyncSession, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller
    settings = {
        "sender_name": "Iva Testova",
        "signature": "Iva Testova\nTest lead",
        "physical_address": "1 Test Street, Testville",
    }
    await sender.save(session, settings, author="тест", author_id=None)
    await session.commit()

    response = await client.post(PREVIEW, json=FIRST, headers=headers)

    assert response.status_code == 200, response.text
    shown = response.json()
    assert shown["subject"] == "Test for Example Company"
    assert shown["zones"][0]["text"] == "Hello Alex Example,"
    assert (shown["sender_name"], shown["signature"], shown["address"], shown["missing"]) == (
        "Iva Testova",
        "Iva Testova\nTest lead",
        "1 Test Street, Testville",
        [],
    )
    assert shown["values"] == {
        "name": "Alex Example",
        "company": "Example Company",
        "site": "example.com",
    }
    assert await chain.rows(session, None) == []


async def test_preview_of_a_followup_has_no_subject_and_names_what_sending_lacks(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller

    response = await client.post(PREVIEW, json=FOLLOW | {"language": "ru"}, headers=headers)

    assert response.status_code == 200, response.text
    assert (response.json()["subject"], response.json()["missing"]) == (
        None,
        ["не задан физический адрес", "не задана подпись", "не задано имя отправителя"],
    )


async def test_preview_refuses_a_body_that_repeats_the_settings_signature(
    client: AsyncClient, session: AsyncSession, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller
    await sender.save(session, {"signature": "Iva Testova"}, author="тест", author_id=None)
    await session.commit()

    body = FIRST | {"body": FIRST_BODY + "\nIva Testova"}
    response = await client.post(PREVIEW, json=body, headers=headers)

    assert response.status_code == 400
    assert "подпись из настроек отправителя" in response.json()["detail"]


# --- право ---------------------------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_without_the_sales_right_every_route_refuses_in_words(
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    method: str,
    path: str,
    body: dict[str, Any] | None,
) -> None:
    await make_user(SELLER, permissions={"sales": False})
    headers = bearer(await sign_in(SELLER))

    response = await client.request(method, path, json=body, headers=headers)

    assert (response.status_code, response.json()["detail"]) == (403, NO_RIGHT)


async def test_the_table_covers_every_route_of_the_chain(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith(CHAIN)
        for method in methods
    }

    assert in_app == {(method, path) for method, path, _ in ROUTES}
