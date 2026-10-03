"""Загрузка базы продаж через API — срез 1.3: путь, по которому пойдёт мастер (1.5).

Предпросмотр ничего не пишет, загрузка пишет лидов и журнал с автором; всё — под
правом `sales`. Отказы — словами сервера: экран показывает `detail` целиком.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from backend.api.sales import routes
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.sales import intake, sheet
from backend.features.sales.models import SalesHypothesisModel, SalesLeadModel
from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_intake import A1, _people

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SELLER = "seller@ours.example.test"
LINK = "https://docs.google.com/spreadsheets/d/1" + "sheet" * 8 + "abc/edit"
PREVIEW = "/api/sales/import/preview"


async def _headers(make_user: MakeUser, sign_in: SignIn, *, sales: bool = True) -> dict[str, str]:
    await make_user(SELLER, permissions=None if sales else {"sales": False})
    return bearer(await sign_in(SELLER))


def _file(text: str | bytes) -> dict[str, Any]:
    data = text.encode("utf-8") if isinstance(text, str) else text
    return {"file": ("leads.csv", data, "text/csv")}


async def _written(session: AsyncSession) -> tuple[int, ...]:
    """Сколько лидов, доменов и записей журнала о загрузке сейчас в базе."""
    counts = [
        select(func.count()).select_from(SalesLeadModel),
        select(func.count()).select_from(DomainModel),
        select(func.count())
        .select_from(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.SALES_LEADS_IMPORTED),
    ]
    return tuple([int(await session.scalar(query) or 0) for query in counts])


async def test_a7_five_thousand_rows_preview_in_seconds_and_writes_nothing(
    client: httpx.AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:  # A7
    headers = await _headers(make_user, sign_in)
    before = await _written(session)

    started = time.monotonic()
    response = await client.post(PREVIEW, headers=headers, files=_file(_people(5000)))
    spent = time.monotonic() - started

    assert response.status_code == 200, response.text
    body = response.json()
    counts = [body[name] for name in ("source", "rows", "accepted", "rejected", "loaded")]
    assert counts == ["leads.csv", 5000, 5000, 0, None]
    assert (body["mapping"], body["needs_mapping"]) == (
        {"email": 0, "name": 1, "company": 2},
        False,
    )
    assert [lead["line"] for lead in body["leads"]] == list(range(2, 52))  # первые 50
    assert spent < 5, f"предпросмотр 5000 строк шёл {spent:.1f} с"
    assert await _written(session) == before


async def test_a8_load_writes_leads_and_its_author_into_the_journal(
    client: httpx.AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:  # A8
    headers = await _headers(make_user, sign_in)
    hypothesis = SalesHypothesisModel(name="сайты EN")
    session.add(hypothesis)
    await session.commit()

    response = await client.post(
        "/api/sales/import",
        headers=headers,
        files=_file(A1),
        data={"hypothesis_id": str(hypothesis.id)},
    )

    assert response.status_code == 200, response.text
    assert [response.json()[name] for name in ("loaded", "accepted", "rejected")] == [2, 2, 0]
    author = await session.scalar(
        select(AuditLogModel.user_id).where(
            AuditLogModel.action == AuditAction.SALES_LEADS_IMPORTED
        )
    )
    assert author == await session.scalar(select(UserModel.id).where(UserModel.email == SELLER))
    assert await _written(session) == (2, 2, 1)


async def test_without_the_sales_right_nothing_is_read(
    api_app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    """Право проверяется до чтения: без него сервер не ходит даже в Google."""
    asked: list[httpx.Request] = []
    google = httpx.MockTransport(lambda request: asked.append(request) or httpx.Response(200))
    api_app.dependency_overrides[routes.sheet_http] = lambda: httpx.AsyncClient(transport=google)
    headers = await _headers(make_user, sign_in, sales=False)

    response = await client.post(PREVIEW, headers=headers, data={"link": LINK})

    assert (response.status_code, asked) == (403, [])


async def test_a5_closed_sheet_link_is_refused_in_words(
    api_app: FastAPI, client: httpx.AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:  # A5
    closed = httpx.MockTransport(lambda _: httpx.Response(401, content=b"<!DOCTYPE html>"))
    api_app.dependency_overrides[routes.sheet_http] = lambda: httpx.AsyncClient(transport=closed)
    headers = await _headers(make_user, sign_in)

    response = await client.post(PREVIEW, headers=headers, data={"link": LINK})

    assert (response.status_code, response.json()["detail"]) == (400, sheet.CLOSED)


EITHER = "дайте файл или ссылку на Google-таблицу — одно из двух"


@pytest.mark.parametrize(
    ("path", "files", "data", "status", "words"),
    [
        (PREVIEW, None, {}, 400, EITHER),
        (PREVIEW, _file(A1), {"link": LINK}, 400, EITHER),
        (
            PREVIEW,
            _file(A1),
            {"mapping": "{email}"},
            400,
            'сопоставление не разобрано (1 ош.): ждём JSON {"email": 0, "name": 1} — поле и номер колонки с нуля',
        ),
        (
            PREVIEW,
            _file(A1),
            {"mapping": '{"email": 9}'},
            400,
            "колонки 10 нет, их в файле 4: email не сопоставить",
        ),
        (
            "/api/sales/import",
            _file("ivan@acme.example.test;Иван\n"),
            {"hypothesis_id": "1"},
            400,
            "сопоставьте колонки: не найдена колонка почты — без неё лидов нет",
        ),
        (
            "/api/sales/import",
            _file(A1),
            {"hypothesis_id": "999999"},
            404,
            "гипотезы №999999 нет — заведите её: outreach sales-hypothesis-add",
        ),
    ],
)
async def test_refusals_say_what_to_do(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    path: str,
    files: dict[str, Any] | None,
    data: dict[str, str],
    status: int,
    words: str,
) -> None:
    headers = await _headers(make_user, sign_in)
    response = await client.post(path, headers=headers, files=files, data=data)
    assert (response.status_code, response.json()["detail"]) == (status, words)


async def test_a6_mapping_by_hand_reads_a_file_without_a_header(
    client: httpx.AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:  # A6
    headers = await _headers(make_user, sign_in)
    response = await client.post(
        PREVIEW,
        headers=headers,
        files=_file("ivan@acme.example.test;Иван\n"),
        data={"mapping": '{"email": 0, "name": 1}'},
    )
    body = response.json()
    assert (body["header"], body["needs_mapping"], body["accepted"]) == (False, False, 1)
    assert body["leads"][0]["name"] == "Иван"


@pytest.mark.parametrize("extra", [0, 1])
async def test_file_bigger_than_the_ceiling_is_refused(
    client: httpx.AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    monkeypatch: pytest.MonkeyPatch,
    extra: int,
) -> None:
    monkeypatch.setattr(intake, "MAX_BYTES", 2**20)
    body = (b"x" * 1023 + b"\n") * 1024 + b"x" * extra  # ровно 1 МБ и на байт больше
    response = await client.post(
        PREVIEW, headers=await _headers(make_user, sign_in), files=_file(body)
    )
    if extra:
        refused = "файл leads.csv больше 1 МБ — разделите базу"
        assert (response.status_code, response.json()["detail"]) == (400, refused)
    else:
        assert (response.status_code, response.json()["rows"]) == (200, 1024)
