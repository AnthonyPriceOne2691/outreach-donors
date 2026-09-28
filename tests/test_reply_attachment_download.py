"""Отдача вложения донора: кто получает файл и в каком виде.

Файл пришёл снаружи. Открытый с нашего адреса HTML или SVG из вложения —
чужой скрипт на странице, где лежит пропуск сотрудника; поэтому здесь
проверяется не только «файл отдаётся», но и то, что отдаётся он **только
на скачивание**, каким бы типом его ни назвал отправитель.

Отдельно — перенос старого списка вложений в таблицу: SQL миграции
гоняется на настоящей базе, а не читается глазами.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest
from backend.features.core.domain import ReplyKind, UserRole
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.inbound_forms import make_sent

PDF = b"%PDF-1.4\r\n\x00\xff binary \r\n%%EOF"
PAGE = b"<html><script>fetch('/api/users')</script></html>"


@pytest.fixture
async def files(session: AsyncSession) -> dict[str, int]:
    """Два ответа: у первого прайс, страница и отказанный файл, у второго — свой файл."""
    sent: MessageModel = await make_sent(session)
    first = ReplyModel(thread_id=sent.thread_id, kind=ReplyKind.HUMAN, raw_body="See attached.")
    second = ReplyModel(thread_id=sent.thread_id, kind=ReplyKind.HUMAN, raw_body="Another one.")
    session.add_all([first, second])
    await session.flush()
    rows = {
        "pdf": ReplyAttachmentModel(
            reply_id=first.id,
            name="Прайс 2026.pdf",
            content_type="application/pdf",
            size=len(PDF),
            data=PDF,
            accepted=True,
        ),
        "page": ReplyAttachmentModel(
            reply_id=first.id,
            name="rates.html",
            content_type="text/html",
            size=len(PAGE),
            data=PAGE,
            accepted=True,
        ),
        "refused": ReplyAttachmentModel(
            reply_id=first.id,
            name="prices.exe",
            content_type="application/x-msdownload",
            size=3,
            data=None,
            accepted=False,
            reason="«.exe» — исполняемый файл или скрипт, такие не принимаются",
        ),
        "other": ReplyAttachmentModel(
            reply_id=second.id, name="b.pdf", size=1, data=b"x", accepted=True
        ),
    }
    session.add_all(rows.values())
    await session.commit()
    return {"first": first.id, "second": second.id, **{k: row.id for k, row in rows.items()}}


@pytest.fixture
async def viewer(make_user: Any, sign_in: Any) -> dict[str, str]:
    await make_user("зритель@site.com", role=UserRole.OPERATOR)
    return bearer(await sign_in("зритель@site.com"))


def _path(reply_id: int, attachment_id: int) -> str:
    return f"/api/replies/{reply_id}/attachments/{attachment_id}"


class TestWhoGetsTheFile:
    async def test_without_pass_nobody(self, client: AsyncClient, files: dict[str, int]) -> None:
        response = await client.get(_path(files["first"], files["pdf"]))

        assert response.status_code == 401

    async def test_pointed_refusal_of_view_closes_it(
        self, client: AsyncClient, files: dict[str, int], make_user: Any, sign_in: Any
    ) -> None:
        await make_user("слепой@site.com", role=UserRole.OPERATOR, permissions={"view": False})
        token = await sign_in("слепой@site.com")

        response = await client.get(_path(files["first"], files["pdf"]), headers=bearer(token))

        assert response.status_code == 403
        assert "«view»" in response.json()["detail"]


class TestHowItIsGiven:
    async def test_file_comes_whole_and_only_as_a_download(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> None:
        response = await client.get(_path(files["first"], files["pdf"]), headers=viewer)

        assert response.status_code == 200
        assert response.content == PDF
        assert response.headers["content-type"] == "application/octet-stream"
        # Латиницей «Прайс» не пишется: запасное имя — то, что от него
        # осталось, а настоящее едет в `filename*`.
        assert response.headers["content-disposition"] == (
            'attachment; filename="2026.pdf"; '
            "filename*=UTF-8''%D0%9F%D1%80%D0%B0%D0%B9%D1%81%202026.pdf"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"

    async def test_html_from_a_donor_is_never_served_as_a_page(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> None:
        response = await client.get(_path(files["first"], files["page"]), headers=viewer)

        assert response.content == PAGE
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"].startswith("attachment;")


class TestWhatIsNotGiven:
    async def test_file_of_another_reply_is_not_found(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> None:
        """Номер вложения чужого ответа — тоже «нет такого»."""
        response = await client.get(_path(files["first"], files["other"]), headers=viewer)

        assert response.status_code == 404
        assert f"№{files['other']}" in response.json()["detail"]

    async def test_refused_file_says_why_it_is_missing(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> None:
        response = await client.get(_path(files["first"], files["refused"]), headers=viewer)

        assert response.status_code == 404
        assert "prices.exe" in response.json()["detail"]
        assert "исполняемый файл" in response.json()["detail"]

    async def test_unknown_numbers_are_not_found(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> None:
        response = await client.get(_path(999_999, files["pdf"]), headers=viewer)

        assert response.status_code == 404


def _migration() -> Any:
    versions = Path(__file__).resolve().parent.parent / "backend" / "migrations" / "versions"
    path = next(versions.glob("*_reply_attachments_keep_files.py"))
    spec = importlib.util.spec_from_file_location("reply_attachments_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_old_attachment_list_moves_into_the_table_and_back(session: AsyncSession) -> None:
    """Старый список вложений (JSON у ответа) переезжает строками, без файлов:
    их не сохраняли. Колонку возвращаем внутри транзакции теста — она
    откатится вместе с ней."""
    migration = _migration()
    sent = await make_sent(session)
    await session.execute(text("ALTER TABLE replies ADD COLUMN attachments JSONB"))
    reply = ReplyModel(thread_id=sent.thread_id, kind=ReplyKind.HUMAN, raw_body="old")
    session.add(reply)
    await session.flush()
    await session.execute(
        text("UPDATE replies SET attachments = CAST(:listed AS JSONB) WHERE id = :id").bindparams(
            listed=(
                '[{"имя": "price.pdf", "байт": 0, "тип": "application/pdf", "принято": true},'
                ' {"имя": "run.exe", "байт": 12, "тип": null, "принято": false}, "мусор"]'
            ),
            id=reply.id,
        )
    )

    await session.execute(text(migration._MOVE_OLD_LIST))

    moved = (
        await session.execute(
            select(
                ReplyAttachmentModel.name,
                ReplyAttachmentModel.size,
                ReplyAttachmentModel.accepted,
                ReplyAttachmentModel.reason,
            )
            .where(ReplyAttachmentModel.reply_id == reply.id)
            .order_by(ReplyAttachmentModel.id)
        )
    ).all()
    assert [tuple(row) for row in moved] == [
        ("price.pdf", None, False, "ответ принят до того, как сервис начал хранить вложения"),
        ("run.exe", 12, False, "исполняемый файл или скрипт, такие не принимаются"),
    ]

    await session.execute(text("UPDATE replies SET attachments = NULL"))
    await session.execute(text(migration._RESTORE_OLD_LIST))
    restored = await session.scalar(
        text("SELECT attachments FROM replies WHERE id = :id").bindparams(id=reply.id)
    )
    assert restored == [
        {"имя": "price.pdf", "байт": 0, "тип": "application/pdf", "принято": False},
        {"имя": "run.exe", "байт": 12, "тип": None, "принято": False},
    ]
