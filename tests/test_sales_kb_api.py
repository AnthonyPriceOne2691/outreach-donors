"""База знаний и отправитель продаж через API — срез 3.1: записи, предпросмотр агента, 403.

Маршруты читают и пишут базу дерева; тексты записей, подписи и адреса выдуманы,
ссылки — на `*.example.test`. Отказы — словами сервера: экран показывает `detail`
целиком. Журнал проверяется по базе: у правки с экрана есть автор.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.sales import kb
from backend.features.sales.models import KbKind
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
KB = "/api/sales/kb"
PREVIEW = "/api/sales/kb/preview"
SENDER = "/api/sales/sender"
NO_RIGHT = "Действие «sales» недоступно этой учётке"

PRICE = {
    "kind": "price_policy",
    "language": "ru",
    "title": "Цена аудита",
    "text": "Цену называем после короткого созвона.",
    "tags": ["Цена", "аудит"],
}
BRIEF = {
    "kind": "brief",
    "language": "ru",
    "title": "Кто мы",
    "text": "Студия примеров для тестов.",
}
CASE = {
    "kind": "case",
    "language": "en",
    "title": "Made-up shop",
    "text": "Doubled made-up traffic.",
}

#: Каждый маршрут раздела под правом `sales`: метод, путь, тело.
ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", KB, None),
    ("POST", KB, BRIEF),
    ("GET", PREVIEW, None),
    ("PATCH", KB + "/1", {"active": False}),
    ("GET", SENDER, None),
    ("POST", SENDER, {"signature": "Ива"}),
]


@pytest.fixture
async def seller(make_user: MakeUser, sign_in: SignIn) -> tuple[UserModel, dict[str, str]]:
    """Продавец с правом отправки: менять базу и отправителя — `sales` и `send`
    (`test_sales_write_rights`)."""
    user = await make_user(SELLER, permissions={"send": True})
    return user, bearer(await sign_in(SELLER))


@pytest.fixture
def connected(monkeypatch: pytest.MonkeyPatch) -> None:
    """Продажи подключены, кроме полей «Отправителя»: готовность — тем же правилом, что отказ
    отправки (`connection.reasons`), — и называет только их."""
    w.connect(monkeypatch)


async def _post(
    client: AsyncClient, headers: dict[str, str], body: dict[str, Any]
) -> dict[str, Any]:
    response = await client.post(KB, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return dict(response.json())


# --- записи --------------------------------------------------------------------------


async def test_new_entry_is_written_with_the_author_and_listed_with_the_version(
    client: AsyncClient, session: AsyncSession, seller: tuple[UserModel, dict[str, str]]
) -> None:
    user, headers = seller

    card = await _post(client, headers, PRICE)
    listed = (await client.get(KB, headers=headers)).json()

    assert {k: card[k] for k in ("kind", "language", "title", "tags", "active", "updated_by")} == {
        "kind": "price_policy",
        "language": "ru",
        "title": "Цена аудита",
        "tags": ["аудит", "цена"],
        "active": True,
        "updated_by": SELLER,
    }
    assert (listed["total"], listed["active"], [row["id"] for row in listed["rows"]]) == (
        1,
        1,
        [card["id"]],
    )
    assert listed["version"] == kb.version_of([kb.entry(**PRICE)])
    assert listed["kinds"] == [kind.value for kind in KbKind]
    assert listed["limits"] == {"title": 255, "text": 20_000, "tag": 64, "tags": 20}
    authors = await session.scalars(
        select(AuditLogModel.user_id).where(AuditLogModel.action == AuditAction.SALES_KB_CHANGED)
    )
    assert list(authors) == [user.id]


async def test_a1_price_edit_through_the_screen_changes_the_version(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    # A1 — пример спеки, сторона экрана
    _, headers = seller
    card = await _post(client, headers, PRICE)
    before = (await client.get(KB, headers=headers)).json()["version"]

    edited = await client.patch(
        f"{KB}/{card['id']}", json={"text": "Цену называем сразу."}, headers=headers
    )
    after = (await client.get(KB, headers=headers)).json()["version"]

    assert edited.status_code == 200, edited.text
    assert edited.json()["text"] == "Цену называем сразу."
    assert after != before


async def test_a2_switched_off_entry_stays_on_the_list_and_leaves_the_agent_preview(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    # A2 — пример спеки, сторона экрана
    _, headers = seller
    price = await _post(client, headers, PRICE)
    await _post(client, headers, CASE)
    await _post(client, headers, BRIEF)

    off = await client.patch(f"{KB}/{price['id']}", json={"active": False}, headers=headers)
    listed = (await client.get(KB, headers=headers)).json()
    preview = (await client.get(PREVIEW, headers=headers)).json()

    assert (off.status_code, off.json()["active"]) == (200, False)
    assert (listed["total"], listed["active"]) == (3, 2)
    assert [
        (g["kind"], g["language"], [f["title"] for f in g["facts"]]) for g in preview["groups"]
    ] == [
        ("brief", "ru", ["Кто мы"]),
        ("case", "en", ["Made-up shop"]),
    ]
    assert preview["total"] == 2
    # Предпросмотр и список называют одну версию — ту, что получит агент.
    assert (
        preview["version"]
        == listed["version"]
        == kb.version_of([kb.entry(**BRIEF), kb.entry(**CASE)])
    )


@pytest.mark.parametrize(
    ("body", "code", "words"),
    [
        (PRICE | {"language": "Russian"}, 400, "язык «Russian» — не код языка: ждём en, ru, pt-br"),
        (PRICE | {"text": "  "}, 400, "нет текста — пустая запись агенту ничего не скажет"),
        (
            PRICE | {"title": " Цена   аудита "},
            409,
            "запись «Цена аудита» (price_policy, ru) уже есть",
        ),
    ],
)
async def test_bad_or_taken_entry_is_refused_in_words(
    client: AsyncClient,
    seller: tuple[UserModel, dict[str, str]],
    body: dict[str, Any],
    code: int,
    words: str,
) -> None:
    _, headers = seller
    await _post(client, headers, PRICE)

    response = await client.post(KB, json=body, headers=headers)

    assert response.status_code == code, response.text
    assert response.json()["detail"].startswith(words)


@pytest.mark.parametrize(
    "body",
    [PRICE | {"titel": "опечатка"}, PRICE | {"kind": "pricing"}],
)
async def test_unknown_field_or_kind_is_refused_by_the_schema(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]], body: dict[str, Any]
) -> None:
    """Опечатка в имени поля не должна молча ничего не менять."""
    _, headers = seller

    response = await client.post(KB, json=body, headers=headers)

    assert response.status_code == 422


async def test_unknown_entry_is_404_in_words_and_null_fields_are_left_alone(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller
    card = await _post(client, headers, PRICE)

    missing = await client.patch(f"{KB}/999999", json={"active": False}, headers=headers)
    kept = await client.patch(
        f"{KB}/{card['id']}", json={"title": None, "text": None}, headers=headers
    )

    assert (missing.status_code, missing.json()["detail"]) == (
        404,
        "записи базы знаний №999999 нет — обновите список",
    )
    assert (kept.status_code, kept.json()["title"], kept.json()["text"]) == (
        200,
        "Цена аудита",
        PRICE["text"],
    )


# --- отправитель ----------------------------------------------------------------------


async def test_empty_sender_names_what_sending_lacks_and_the_field_limits(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]], connected: None
) -> None:
    _, headers = seller

    body = (await client.get(SENDER, headers=headers)).json()

    assert body["missing"] == [
        "не задан физический адрес",
        "не задана подпись",
        "не задано имя отправителя",
    ]
    assert (body["physical_address"], body["updated_at"]) == (None, None)
    assert body["limits"] == {
        "sender_name": 128,
        "sender_position": 128,
        "signature": 1000,
        "website": 255,
        "telegram": 255,
        "physical_address": 500,
        "call_link": 512,
    }


async def test_sender_is_saved_whole_and_read_back_ready(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]], connected: None
) -> None:
    _, headers = seller
    body = {
        "sender_name": " Ива  Тестова ",
        "signature": "Ива Тестова\nстудия примеров",
        "physical_address": "Выдуманная ул., 7",
        "telegram": "@studio_example",
    }

    saved = await client.post(SENDER, json=body, headers=headers)
    read = (await client.get(SENDER, headers=headers)).json()

    assert saved.status_code == 200, saved.text
    assert (read["sender_name"], read["missing"], read["updated_by"]) == ("Ива Тестова", [], SELLER)
    assert (read["website"], read["telegram"]) == (None, "@studio_example")


async def test_switched_off_module_is_the_first_thing_sending_lacks(
    client: AsyncClient,
    seller: tuple[UserModel, dict[str, str]],
    connected: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«Отправка продаж не готова» — тем же правилом, что отказ отправки: выключенный модуль —
    первым и словами (находка QA на проде: при выключенном модуле экран говорил «готова»)."""
    monkeypatch.setattr(sales_cfg, "ENABLED", False)
    _, headers = seller
    filled = {"sender_name": "Ива Тестова", "signature": "Ива", "physical_address": "Ул., 7"}

    await client.post(SENDER, json=filled, headers=headers)
    read = (await client.get(SENDER, headers=headers)).json()

    assert read["missing"] == ["модуль продаж выключен — включает администратор"]


async def test_sender_with_a_bad_link_is_refused_in_words_and_typos_by_the_schema(
    client: AsyncClient, seller: tuple[UserModel, dict[str, str]]
) -> None:
    _, headers = seller

    bad = await client.post(SENDER, json={"website": "studio.example.test"}, headers=headers)
    typo = await client.post(SENDER, json={"adress": "Выдуманная ул., 7"}, headers=headers)

    assert (bad.status_code, bad.json()["detail"]) == (
        400,
        "сайт: «studio.example.test» — не ссылка, ждём https://…",
    )
    assert typo.status_code == 422


# --- право ------------------------------------------------------------------------------


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


async def test_the_table_covers_every_route_of_the_knowledge_base(api_app: FastAPI) -> None:
    in_app = {
        (method.upper(), path)
        for path, methods in api_app.openapi()["paths"].items()
        if path.startswith((KB, SENDER))
        for method in methods
    }
    in_table = {(method, path.replace("/1", "/{entry_id}")) for method, path, _ in ROUTES}

    assert in_app == in_table
