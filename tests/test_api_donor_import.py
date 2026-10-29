"""Доноры списком по HTTP (`api/donors/bulk.py`): шаблон, предпросмотр, загрузка, очередь
загрузки обхода.

Правила строк — у ядра (`test_bulk_entry.py`), чтение файла — `test_bulk_file.py`. Здесь — то,
что доходит до экрана: предпросмотр ничего не пишет, загрузка пишет и с галочкой ставит годных
в очередь загрузки, отказ файла — словами и кодом запроса, право — `run`, как у формы; очередь
загрузки видна смотрящему и снимается тем, у кого есть право запуска.
"""

from __future__ import annotations

import io
from collections.abc import Awaitable, Callable

import openpyxl
import pytest
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.crawl import CrawlBacklogModel
from backend.features.core.models.domain import DomainModel
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

WHO = "anna@ours.example.test"
CSV = (
    "домен,цена,валюта,откуда цена\nexample.com,150,USD,прайс\nexample.org,90,EUR,\nне-адрес,1,,\n"
)


@pytest.fixture
async def anna(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user(WHO, role=UserRole.OPERATOR)
    return await sign_in(WHO)


@pytest.fixture
async def viewer(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("watcher@ours.example.test", permissions={"run": False})
    return await sign_in("watcher@ours.example.test")


def _file(text: str = CSV, name: str = "доноры.csv") -> dict[str, tuple[str, bytes, str]]:
    return {"file": (name, text.encode("utf-8"), "text/csv")}


async def _count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_the_template_is_a_utf8_csv_with_a_bom(client: AsyncClient, anna: str) -> None:
    answer = await client.get("/api/donors/import/template", headers=bearer(anna))

    assert answer.status_code == 200
    assert answer.headers["content-type"].startswith("text/csv")
    assert "filename*=UTF-8''" in answer.headers["content-disposition"]
    assert answer.content.startswith("﻿".encode())
    assert "домен,цена,валюта,откуда цена" in answer.text


async def test_preview_reports_every_line_and_writes_nothing(
    client: AsyncClient, anna: str, session: AsyncSession
) -> None:
    answer = await client.post("/api/donors/import/preview", files=_file(), headers=bearer(anna))

    assert answer.status_code == 200
    body = answer.json()
    assert (body["created"], body["priced"], body["skipped"], body["crawlable"]) == (2, 0, 1, 2)
    assert [(row["line"], row["outcome"]) for row in body["rows"]] == [
        (2, "created"),
        (3, "created"),
        (4, "skipped"),
    ]
    assert body["rows"][2]["reason"].startswith("«не-адрес» — не домен")
    assert (body["queued"], body["backlog"]["waiting"]) == (0, 2)
    assert await _count(session, DomainModel) == 0


async def test_entering_with_the_crawl_box_queues_the_suitable(
    client: AsyncClient, anna: str, session: AsyncSession
) -> None:
    await make_donor(session, "example.org")
    await session.commit()

    answer = await client.post(
        "/api/donors/import", files=_file(), data={"crawl": "true"}, headers=bearer(anna)
    )

    assert answer.status_code == 200
    body = answer.json()
    assert (body["created"], body["priced"], body["queued"]) == (1, 1, 2)
    assert body["backlog"]["waiting"] == 2
    waiting = (await session.scalars(select(CrawlBacklogModel.host))).all()
    assert sorted(waiting) == ["example.com", "example.org"]
    vias = (
        await session.scalars(
            select(AuditLogModel.details).where(AuditLogModel.action == AuditAction.PRICE_REVIEWED)
        )
    ).all()
    assert [(entry or {}).get("как") for entry in vias] == ["загрузка списком"] * 2


async def test_entering_without_the_box_crawls_nobody(
    client: AsyncClient, anna: str, session: AsyncSession
) -> None:
    answer = await client.post("/api/donors/import", files=_file(), headers=bearer(anna))

    assert answer.status_code == 200
    assert answer.json()["queued"] == 0
    assert await _count(session, CrawlBacklogModel) == 0
    assert await _count(session, DomainModel) == 2


async def test_an_xlsx_is_read_the_same(client: AsyncClient, anna: str) -> None:
    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    for row in (["домен", "цена"], ["example.com", 150]):
        sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    files = {"file": ("доноры.xlsx", out.getvalue(), "application/octet-stream")}

    answer = await client.post("/api/donors/import/preview", files=files, headers=bearer(anna))

    assert answer.status_code == 200
    assert answer.json()["created"] == 1


async def test_a_file_without_a_price_column_is_refused_in_words(
    client: AsyncClient, anna: str
) -> None:
    answer = await client.post(
        "/api/donors/import/preview",
        files=_file("домен,сумма\nexample.com,1\n"),
        headers=bearer(anna),
    )

    assert answer.status_code == 400
    assert "не нашлось колонки с ценой" in answer.json()["detail"]


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("post", "/api/donors/import/preview"),
        ("post", "/api/donors/import"),
        ("delete", "/api/donors/import/backlog"),
    ],
)
async def test_without_the_run_right_nothing_is_entered(
    client: AsyncClient, viewer: str, session: AsyncSession, method: str, path: str
) -> None:
    files = _file() if method == "post" else None
    answer = await client.request(method.upper(), path, files=files, headers=bearer(viewer))

    assert answer.status_code == 403
    assert await _count(session, DomainModel) == 0


async def test_the_queue_is_seen_by_a_viewer_and_dropped_by_a_runner(
    client: AsyncClient, viewer: str, anna: str, session: AsyncSession
) -> None:
    session.add_all([CrawlBacklogModel(host="one.example"), CrawlBacklogModel(host="two.example")])
    await session.commit()

    seen = await client.get("/api/donors/import/backlog", headers=bearer(viewer))
    dropped = await client.delete("/api/donors/import/backlog", headers=bearer(anna))

    assert (seen.status_code, seen.json()["waiting"]) == (200, 2)
    assert (dropped.status_code, dropped.json()) == (200, {"dropped": 2})
    assert await _count(session, CrawlBacklogModel) == 0
    entry = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.CRAWL_BACKLOG_DROPPED)
    )
    assert entry is not None


async def test_the_donor_routes_still_take_a_number_after_import(
    client: AsyncClient, anna: str
) -> None:
    """Маршруты загрузки стоят до `/{donor_id}`: «import» — не номер донора, а число — номер."""
    answer = await client.get("/api/donors/999999", headers=bearer(anna))

    assert answer.status_code == 404
