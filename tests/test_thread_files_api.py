"""Файлы к ответу по HTTP: приложить, убрать, отправить с ответом, увидеть и скачать.

Права — как у ответа: приложить и убрать файл может тот, кто отвечает (`send`), скачать
ушедший — тот, кто смотрит переписку (`view`). Отказ — словами, с кодом по смыслу:
файл не годится — 422, его нет — 404, он уже ушёл с письмом — 409.

Отдельно — миграция на настоящей базе и потолки nginx: файл в предел сервера, отбитый
прокси страницей, человек увидел бы как «сервер ответил 413» без единого слова.
"""

from __future__ import annotations

import importlib.util
import re
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.api.threads import routes as thread_routes
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outgoing_attachment import OutgoingAttachmentModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.letters.outgoing_files import MAX_FILE_BYTES
from fastapi import FastAPI
from httpx import AsyncClient, Response
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_outgoing_files import PDF
from tests.test_replies_inbox import sent
from tests.test_thread_answer import Recording, conversation

__all__ = ["conversation", "sent"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ROOT = Path(__file__).resolve().parents[1]

MESSAGE_ROUTES: list[tuple[str, str, str]] = [
    # Вложение нашего письма — то же содержимое переписки, что и текст письма.
    ("GET", "/api/messages/{message}/attachments/{file}", "view"),
]


@pytest.fixture
async def admin(make_user: MakeUser, sign_in: SignIn) -> tuple[UserModel, dict[str, str]]:
    user = await make_user("админ@site.com", role=UserRole.ADMIN)
    return user, bearer(await sign_in("админ@site.com"))


@pytest.fixture
async def viewer(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор: смотрит переписку, а отвечать права нет."""
    await make_user("зритель@site.com", role=UserRole.OPERATOR)
    return bearer(await sign_in("зритель@site.com"))


async def _upload(
    client: AsyncClient,
    thread_id: int,
    headers: dict[str, str],
    name: str = "Прайс 2026.pdf",
    data: bytes = PDF,
    kind: str = "application/pdf",
) -> Response:
    return await client.post(
        f"/api/threads/{thread_id}/files", files={"file": (name, data, kind)}, headers=headers
    )


async def _answer(
    client: AsyncClient,
    conversation: tuple[MessageModel, ReplyModel],
    headers: dict[str, str],
    file_ids: list[int],
) -> Response:
    first, reply = conversation
    return await client.post(
        f"/api/threads/{first.thread_id}/answer",
        json={
            "reply_id": reply.id,
            "body": "Thanks! The rates are attached.",
            "file_ids": file_ids,
        },
        headers=headers,
    )


@pytest.fixture
def recording(monkeypatch: pytest.MonkeyPatch) -> Recording:
    """Почта ответа — нулевая и запоминает, что ей отдали."""
    transport = Recording()
    monkeypatch.setattr(thread_routes, "Transports", lambda: transport)
    return transport


class TestUpload:
    async def test_file_is_kept_without_a_letter_under_our_type_and_clean_name(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin: tuple[UserModel, dict[str, str]],
        conversation: tuple[MessageModel, ReplyModel],
    ) -> None:
        """Браузер назвал файл страницей и прислал путь — тип и имя назначает сервер."""
        user, headers = admin
        first, _ = conversation

        response = await _upload(
            client,
            first.thread_id or 0,
            headers,
            name="C:\\fakepath\\Прайс 2026.pdf",
            kind="text/html",
        )

        assert response.status_code == 200, response.text
        card = response.json()
        assert card == {
            "id": card["id"],
            "name": "Прайс 2026.pdf",
            "size": len(PDF),
            "content_type": "application/pdf",
        }
        row = await session.get(OutgoingAttachmentModel, card["id"])
        assert row is not None
        assert (row.thread_id, row.message_id, row.uploaded_by) == (first.thread_id, None, user.id)

    async def test_operator_with_the_send_right_may_attach(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        conversation: tuple[MessageModel, ReplyModel],
    ) -> None:
        """Право — то же, что у ответа, а не роль: оператору с `send` можно."""
        await make_user("пишет@site.com", role=UserRole.OPERATOR, permissions={"send": True})
        first, _ = conversation

        response = await _upload(
            client, first.thread_id or 0, bearer(await sign_in("пишет@site.com"))
        )

        assert response.status_code == 200, response.text

    @pytest.mark.parametrize(
        ("name", "data", "words"),
        [
            (
                "setup.exe",
                b"MZ\x90\x00",
                "«setup.exe»: такие файлы с письмом не уходят — можно PDF",
            ),
            ("price.pdf", b"MZ\x90\x00", "«price.pdf»: по содержимому это не PDF"),
            ("big.pdf", PDF + b"0" * MAX_FILE_BYTES, "«big.pdf» больше предела 10 МБ на файл"),
        ],
    )
    async def test_refusal_is_422_in_words_and_nothing_is_kept(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin: tuple[UserModel, dict[str, str]],
        conversation: tuple[MessageModel, ReplyModel],
        name: str,
        data: bytes,
        words: str,
    ) -> None:
        first, _ = conversation

        response = await _upload(client, first.thread_id or 0, admin[1], name=name, data=data)

        assert response.status_code == 422
        assert response.json()["detail"].startswith(words)
        assert await session.scalar(select(OutgoingAttachmentModel.id)) is None

    async def test_unknown_thread_is_404(
        self, client: AsyncClient, admin: tuple[UserModel, dict[str, str]]
    ) -> None:
        response = await _upload(client, 999_999, admin[1])

        assert response.status_code == 404
        assert response.json()["detail"] == "Диалога №999999 нет — прикладывать файл не к чему"


class TestRemove:
    async def test_unsent_file_is_removed_and_then_unknown(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin: tuple[UserModel, dict[str, str]],
        conversation: tuple[MessageModel, ReplyModel],
    ) -> None:
        first, _ = conversation
        file_id = (await _upload(client, first.thread_id or 0, admin[1])).json()["id"]
        path = f"/api/threads/{first.thread_id}/files/{file_id}"

        removed = await client.delete(path, headers=admin[1])
        again = await client.delete(path, headers=admin[1])

        assert removed.status_code == 204
        assert await session.get(OutgoingAttachmentModel, file_id) is None
        assert again.status_code == 404
        assert again.json()["detail"] == f"В переписке №{first.thread_id} нет файла №{file_id}"

    async def test_file_sent_with_the_answer_is_not_removed(
        self,
        client: AsyncClient,
        admin: tuple[UserModel, dict[str, str]],
        conversation: tuple[MessageModel, ReplyModel],
        recording: Recording,
    ) -> None:
        first, _ = conversation
        file_id = (await _upload(client, first.thread_id or 0, admin[1])).json()["id"]
        answered = await _answer(client, conversation, admin[1], [file_id])
        letter_id = answered.json()["id"]

        response = await client.delete(
            f"/api/threads/{first.thread_id}/files/{file_id}", headers=admin[1]
        )

        assert response.status_code == 409
        assert response.json()["detail"] == (
            f"Файл «Прайс 2026.pdf» уже приложен к письму №{letter_id} — убрать его нельзя: "
            "письмо ушло или уйдёт вместе с ним"
        )

    async def test_file_of_another_thread_is_not_found_by_this_one(
        self,
        client: AsyncClient,
        admin: tuple[UserModel, dict[str, str]],
        conversation: tuple[MessageModel, ReplyModel],
    ) -> None:
        first, _ = conversation
        file_id = (await _upload(client, first.thread_id or 0, admin[1])).json()["id"]

        response = await client.delete(f"/api/threads/999999/files/{file_id}", headers=admin[1])

        assert response.status_code == 404


class TestAnswerWithFiles:
    async def test_answer_takes_the_files_and_the_thread_shows_them_without_bodies(
        self,
        client: AsyncClient,
        admin: tuple[UserModel, dict[str, str]],
        conversation: tuple[MessageModel, ReplyModel],
        recording: Recording,
    ) -> None:
        first, _ = conversation
        file_id = (await _upload(client, first.thread_id or 0, admin[1])).json()["id"]

        answered = await _answer(client, conversation, admin[1], [file_id])
        shown = await client.get(f"/api/threads/{first.thread_id}", headers=admin[1])

        assert answered.status_code == 200, answered.text
        [out] = recording.seen
        assert [(f.name, f.data) for f in out.attachments] == [("Прайс 2026.pdf", PDF)]
        letters = shown.json()["letters"]
        assert [letter["attachments"] for letter in letters] == [
            [],
            [{"id": file_id, "name": "Прайс 2026.pdf", "size": len(PDF)}],
        ]

    async def test_answer_without_file_ids_is_the_old_request(
        self,
        client: AsyncClient,
        admin: tuple[UserModel, dict[str, str]],
        conversation: tuple[MessageModel, ReplyModel],
        recording: Recording,
    ) -> None:
        first, reply = conversation

        response = await client.post(
            f"/api/threads/{first.thread_id}/answer",
            json={"reply_id": reply.id, "body": "Thanks!"},
            headers=admin[1],
        )

        assert response.status_code == 200, response.text
        assert recording.seen[0].attachments == ()

    @pytest.mark.parametrize(
        ("file_ids", "code", "words"),
        [
            ([999_999], 404, "нет файла №999999 — приложить к ответу нечего"),
            ([1, 2, 3, 4, 5, 6], 422, "К письму — не больше 5 файлов, а приложено 6"),
        ],
    )
    async def test_bad_files_refuse_the_answer_in_words_and_no_letter_is_stored(
        self,
        client: AsyncClient,
        admin: tuple[UserModel, dict[str, str]],
        conversation: tuple[MessageModel, ReplyModel],
        recording: Recording,
        file_ids: list[int],
        code: int,
        words: str,
    ) -> None:
        first, _ = conversation

        response = await _answer(client, conversation, admin[1], file_ids)
        shown = await client.get(f"/api/threads/{first.thread_id}", headers=admin[1])

        assert response.status_code == code
        assert words in response.json()["detail"]
        assert recording.seen == []
        assert len(shown.json()["letters"]) == 1  # только первое письмо


class TestDownload:
    async def test_file_of_our_letter_is_given_whole_and_only_as_a_download(
        self,
        client: AsyncClient,
        admin: tuple[UserModel, dict[str, str]],
        viewer: dict[str, str],
        conversation: tuple[MessageModel, ReplyModel],
        recording: Recording,
    ) -> None:
        first, _ = conversation
        file_id = (await _upload(client, first.thread_id or 0, admin[1])).json()["id"]
        letter_id = (await _answer(client, conversation, admin[1], [file_id])).json()["id"]

        response = await client.get(
            f"/api/messages/{letter_id}/attachments/{file_id}", headers=viewer
        )

        assert response.status_code == 200, response.text
        assert response.content == PDF
        assert response.headers["content-type"] == "application/octet-stream"
        assert response.headers["content-disposition"] == (
            'attachment; filename="2026.pdf"; '
            "filename*=UTF-8''%D0%9F%D1%80%D0%B0%D0%B9%D1%81%202026.pdf"
        )
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "no-store"

    async def test_unsent_file_and_file_of_another_letter_are_not_found(
        self,
        client: AsyncClient,
        admin: tuple[UserModel, dict[str, str]],
        viewer: dict[str, str],
        conversation: tuple[MessageModel, ReplyModel],
    ) -> None:
        """Номер письма сверяется: файл, не ушедший ни с каким письмом, и файл чужого
        письма по подобранному номеру — «нет такого»."""
        first, _ = conversation
        file_id = (await _upload(client, first.thread_id or 0, admin[1])).json()["id"]

        response = await client.get(
            f"/api/messages/{first.id}/attachments/{file_id}", headers=viewer
        )

        assert response.status_code == 404
        assert response.json()["detail"] == f"У письма №{first.id} нет вложения №{file_id}"


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "permission"), MESSAGE_ROUTES)
    async def test_without_pass_nobody(
        self, client: AsyncClient, method: str, path: str, permission: str
    ) -> None:
        response = await client.request(method, path.format(message=1, file=1))

        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "permission"), MESSAGE_ROUTES)
    async def test_pointed_refusal_closes_it(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        method: str,
        path: str,
        permission: str,
    ) -> None:
        await make_user("слепой@site.com", role=UserRole.OPERATOR, permissions={permission: False})
        token = await sign_in("слепой@site.com")

        response = await client.request(
            method, path.format(message=1, file=1), headers=bearer(token)
        )

        assert response.status_code == 403
        assert f"«{permission}»" in response.json()["detail"]

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/messages")
            for method in methods
        }
        in_table = {
            (method, path.replace("{message}", "{message_id}").replace("{file}", "{file_id}"))
            for method, path, _ in MESSAGE_ROUTES
        }
        assert in_app == in_table


# --- миграция и прокси --------------------------------------------------------


def _migration() -> ModuleType:
    path = ROOT / "backend/migrations/versions/8f3a6b2d0c95_outgoing_attachments.py"
    spec = importlib.util.spec_from_file_location("outgoing_attachments_migration", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _tables(connection: Connection) -> tuple[list[Any], list[Any]]:
    migration = _migration()
    query = text("SELECT tablename FROM pg_tables WHERE tablename = 'outgoing_attachments'")
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = list(connection.execute(query).scalars())
        migration.upgrade()
    return down, list(connection.execute(query).scalars())


async def test_migration_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    assert await connection.run_sync(_tables) == ([], ["outgoing_attachments"])


#: Место загрузки файла в обоих nginx: внутри (образ web) и прокси хоста.
_FILES_LOCATION = re.compile(r"location\s+~\s+(\^/api/threads/\[0-9\]\+/files\$)\s*\{([^}]*)\}")


@pytest.mark.parametrize("conf", ["deploy/nginx.conf", "deploy/proxy/outreach.conf"])
def test_nginx_lets_a_file_at_our_limit_through_to_the_server(conf: str) -> None:
    """Потолок тела у места загрузки — не ниже нашего предела файла с запасом на разметку
    формы, а само место ловит загрузку и не ловит соседние адреса переписки."""
    found = _FILES_LOCATION.search((ROOT / conf).read_text(encoding="utf-8"))
    assert found is not None, f"в {conf} нет места загрузки файла к ответу"
    pattern, body = found.groups()
    size = re.search(r"client_max_body_size\s+(\d+)m;", body)
    assert size is not None
    assert int(size.group(1)) * 1024 * 1024 >= MAX_FILE_BYTES + 64 * 1024
    assert re.match(pattern, "/api/threads/17/files")
    assert not re.match(pattern, "/api/threads/17/files/3")
    assert not re.match(pattern, "/api/threads/17/answer")
