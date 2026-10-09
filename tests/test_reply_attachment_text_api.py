"""Текст вложения на экран: кто его получает, что в нём — и что карточка знает без него.

Файл донора на нашей странице не показывается никогда (`download.py`); показывается
текст, прочитанный из него на сервере. Ответ, принятый до того, как файлы начали
читаться, читается по первому запросу, — и прочитанное остаётся: второй показ
файл не читает. Карточка диалога говорит «текст есть» и «почему нет», но сам
текст не везёт: она читает сведения о десятке файлов разом.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.core.domain import ReplyKind, UserRole
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies import attachments
from backend.features.replies.attachment_text import PICTURE
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.attachment_files import PNG, xlsx
from tests.conftest import bearer
from tests.inbound_forms import make_sent

BOOK = xlsx({"Прайс": [["Guest post", 150]]}, formats={"Прайс!B1": '"$"#,##0'})
BOOK_TEXT = "[лист «Прайс»]\nGuest post\t$ 150"
REFUSED = "«.exe» — исполняемый файл или скрипт, такие не принимаются"


@pytest.fixture
async def files(session: AsyncSession) -> dict[str, int]:
    """Ответ с прайсом, картинкой и отказанным файлом — не читанные: приняты
    до того, как файлы начали читаться. И чужой ответ со своим файлом."""
    sent = await make_sent(session)
    first = ReplyModel(thread_id=sent.thread_id, kind=ReplyKind.HUMAN, raw_body="See attached.")
    second = ReplyModel(thread_id=sent.thread_id, kind=ReplyKind.HUMAN, raw_body="Another one.")
    session.add_all([first, second])
    await session.flush()
    rows = {
        "book": ReplyAttachmentModel(
            reply_id=first.id, name="Прайс 2026.xlsx", size=len(BOOK), data=BOOK, accepted=True
        ),
        "logo": ReplyAttachmentModel(
            reply_id=first.id, name="logo.png", size=len(PNG), data=PNG, accepted=True
        ),
        "refused": ReplyAttachmentModel(
            reply_id=first.id, name="prices.exe", size=2, data=None, accepted=False, reason=REFUSED
        ),
        "other": ReplyAttachmentModel(
            reply_id=second.id, name="b.txt", size=1, data=b"x", accepted=True
        ),
    }
    session.add_all(rows.values())
    await session.commit()
    return {
        "thread": sent.thread_id,
        "first": first.id,
        "second": second.id,
        **{key: row.id for key, row in rows.items()},
    }


@pytest.fixture
async def viewer(make_user: Any, sign_in: Any) -> dict[str, str]:
    await make_user("зритель@site.com", role=UserRole.OPERATOR)
    return bearer(await sign_in("зритель@site.com"))


def _path(reply_id: int, attachment_id: int) -> str:
    return f"/api/replies/{reply_id}/attachments/{attachment_id}/text"


def _never(*_args: Any, **_kwargs: Any) -> Any:
    raise AssertionError("файл, который уже читали, читается второй раз")


class TestWhoGetsTheText:
    async def test_without_pass_nobody(self, client: AsyncClient, files: dict[str, int]) -> None:
        response = await client.get(_path(files["first"], files["book"]))

        assert response.status_code == 401

    async def test_pointed_refusal_of_view_closes_it(
        self, client: AsyncClient, files: dict[str, int], make_user: Any, sign_in: Any
    ) -> None:
        await make_user("слепой@site.com", role=UserRole.OPERATOR, permissions={"view": False})
        token = await sign_in("слепой@site.com")

        response = await client.get(_path(files["first"], files["book"]), headers=bearer(token))

        assert response.status_code == 403
        assert "«view»" in response.json()["detail"]


class TestWhatComesBack:
    async def test_file_is_read_on_the_first_request_and_kept(
        self,
        client: AsyncClient,
        files: dict[str, int],
        viewer: dict[str, str],
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        response = await client.get(_path(files["first"], files["book"]), headers=viewer)

        assert response.status_code == 200
        assert response.json() == {"name": "Прайс 2026.xlsx", "text": BOOK_TEXT, "note": None}
        read_at = await session.scalar(
            select(ReplyAttachmentModel.text_read_at).where(
                ReplyAttachmentModel.id == files["book"]
            )
        )
        assert read_at is not None

        monkeypatch.setattr(attachments, "read_text", _never)
        again = await client.get(_path(files["first"], files["book"]), headers=viewer)
        assert again.json() == response.json()

    async def test_file_without_text_answers_why(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> None:
        response = await client.get(_path(files["first"], files["logo"]), headers=viewer)

        assert response.status_code == 200
        assert response.json() == {"name": "logo.png", "text": None, "note": PICTURE}

    async def test_file_of_another_reply_is_not_found(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> None:
        """Номер вложения чужого ответа — тоже «нет такого», словами."""
        response = await client.get(_path(files["first"], files["other"]), headers=viewer)

        assert response.status_code == 404
        assert response.json()["detail"] == (
            f"У ответа №{files['first']} нет вложения №{files['other']}"
        )

    async def test_file_that_was_not_kept_says_why(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> None:
        response = await client.get(_path(files["first"], files["refused"]), headers=viewer)

        assert response.status_code == 404
        assert response.json()["detail"] == f"Файл «prices.exe» не сохранён: {REFUSED}"

    async def test_unknown_numbers_are_not_found(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> None:
        response = await client.get(_path(999_999, files["book"]), headers=viewer)

        assert response.status_code == 404


class TestCard:
    async def test_card_says_whether_there_is_text_and_carries_no_text(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> None:
        before = await self._cards(client, files, viewer)
        assert before == {
            ("Прайс 2026.xlsx", False, None),
            ("logo.png", False, None),
            ("prices.exe", False, None),
        }

        for name in ("book", "logo"):
            await client.get(_path(files["first"], files[name]), headers=viewer)

        assert await self._cards(client, files, viewer) == {
            ("Прайс 2026.xlsx", True, None),
            ("logo.png", False, PICTURE),
            ("prices.exe", False, None),
        }

    async def _cards(
        self, client: AsyncClient, files: dict[str, int], viewer: dict[str, str]
    ) -> set[tuple[str, bool, str | None]]:
        view = await client.get(f"/api/threads/{files['thread']}", headers=viewer)
        assert view.status_code == 200, view.text
        reply = next(card for card in view.json()["incoming"] if card["id"] == files["first"])
        assert all("text" not in card for card in reply["attachments"])
        return {
            (card["name"], card["has_text"], card["text_note"]) for card in reply["attachments"]
        }
