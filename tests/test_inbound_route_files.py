"""Вложения ответа на настоящей базе: файл сохраняется, отказ объясняется.

Платформа приёма — единственный получатель письма, другой копии нет.
До этого среза маршрут выбрасывал сами файлы и записывал только имена
с размером ноль: прайс, присланный донором файлом, терялся навсегда.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.core.domain import UserRole
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.inbound_forms import (
    AUTH,
    URL,
    NoQueue,
    attachment_info,
    letter,
    make_sent,
    multipart,
    setup_inbound,
)

PDF = b"%PDF-1.4\r\n" + bytes(range(256)) * 40 + b"\r\n%%EOF\r\n"
MB = 1024 * 1024


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch) -> NoQueue:
    return setup_inbound(monkeypatch)


@pytest.fixture
async def sent(session: AsyncSession) -> MessageModel:
    return await make_sent(session)


async def _post(
    client: AsyncClient,
    message: MessageModel,
    files: list[tuple[str, str, str, bytes]],
    *,
    info: tuple[str, bytes] | None = None,
    message_id: str = "in-1",
) -> dict[str, Any]:
    fields = letter(message, message_id=message_id, extra=[info] if info else [])
    body, headers = multipart(fields, files)
    response = await client.post(URL, content=body, headers={**headers, **AUTH})
    assert response.status_code == 200, response.text
    answer: dict[str, Any] = response.json()
    return answer


async def _rows(session: AsyncSession) -> list[tuple[Any, ...]]:
    found = await session.execute(
        select(
            ReplyAttachmentModel.name,
            ReplyAttachmentModel.content_type,
            ReplyAttachmentModel.size,
            ReplyAttachmentModel.data,
            ReplyAttachmentModel.accepted,
            ReplyAttachmentModel.reason,
        ).order_by(ReplyAttachmentModel.id)
    )
    return [tuple(row) for row in found.all()]


class TestKept:
    async def test_pdf_is_stored_byte_for_byte_with_its_real_size(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        await _post(
            client,
            sent,
            [("attachment1", 'filename="rate-card.pdf"', "application/pdf", PDF)],
            info=attachment_info(("rate-card.pdf", "application/pdf")),
        )

        assert await _rows(session) == [
            ("rate-card.pdf", "application/pdf", len(PDF), PDF, True, None)
        ]

    async def test_cyrillic_name_in_rfc2231(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        await _post(
            client,
            sent,
            [
                (
                    "attachment1",
                    "filename*=UTF-8''%D0%9F%D1%80%D0%B0%D0%B9%D1%81%202026.pdf",
                    "application/pdf",
                    PDF,
                )
            ],
        )

        assert (await _rows(session))[0][0] == "Прайс 2026.pdf"

    async def test_cyrillic_name_from_attachment_info(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        """Имя из списка вложений сильнее имени части: там оно целиком в UTF-8."""
        await _post(
            client,
            sent,
            [
                ("attachment1", 'filename="price.pdf"', "application/pdf", PDF),
                ("attachment2", 'filename="x.pdf"', "application/pdf", PDF),
            ],
            info=attachment_info(
                ("Прайс-лист.pdf", "application/pdf"),
                ("=?UTF-8?B?0JzQtdC00LjQsNC60LjRgi5wZGY=?=", "application/pdf"),
            ),
        )

        assert [row[0] for row in await _rows(session)] == ["Прайс-лист.pdf", "Медиакит.pdf"]

    async def test_repeat_of_the_webhook_does_not_double_the_files(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        files = [("attachment1", 'filename="rates.pdf"', "application/pdf", PDF)]

        await _post(client, sent, files)
        again = await _post(client, sent, files)

        assert again["duplicate"]
        assert len(await _rows(session)) == 1


class TestRefused:
    async def test_executable_is_not_stored_and_says_why(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        answer = await _post(
            client,
            sent,
            [("attachment1", 'filename="prices.exe"', "application/x-msdownload", b"MZ\x90")],
        )

        name, _, size, data, accepted, reason = (await _rows(session))[0]
        assert answer["accepted"]
        assert (name, size, data, accepted) == ("prices.exe", 3, None, False)
        assert "«.exe» — исполняемый файл" in reason

    async def test_twenty_first_file_is_listed_but_not_stored(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        files = [
            (f"attachment{n}", f'filename="page-{n}.pdf"', "application/pdf", PDF)
            for n in range(1, 22)
        ]

        answer = await _post(client, sent, files)

        rows = await _rows(session)
        assert answer["accepted"]
        assert len(rows) == 21
        assert [row[4] for row in rows] == [True] * 20 + [False]
        assert rows[-1][3] is None
        assert "больше 20 файлов" in rows[-1][5]

    async def test_file_over_ten_megabytes_is_listed_with_its_size(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        big = b"0" * (11 * MB)

        await _post(
            client,
            sent,
            [
                ("attachment1", 'filename="video.mp4"', "video/mp4", big),
                ("attachment2", 'filename="rates.pdf"', "application/pdf", PDF),
            ],
        )

        rows = await _rows(session)
        assert rows[0][2:5] == (11 * MB, None, False)
        assert "11 МБ — больше предела 10 МБ на файл" in rows[0][5]
        assert rows[1][4]

    async def test_files_over_the_reply_ceiling_are_listed(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        part = b"1" * (9 * MB)

        await _post(
            client,
            sent,
            [(f"attachment{n}", f'filename="{n}.pdf"', "application/pdf", part) for n in (1, 2, 3)],
        )

        rows = await _rows(session)
        assert [row[4] for row in rows] == [True, True, False]
        assert "25 МБ на ответ" in rows[2][5]

    async def test_named_file_that_did_not_come_is_listed(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        """Прайс был и потерялся — это видно, а не «прайса не присылали»."""
        await _post(
            client,
            sent,
            [("attachment1", 'filename="a.pdf"', "application/pdf", PDF)],
            info=attachment_info(("a.pdf", "application/pdf"), ("rates.xlsx", "application/xlsx")),
        )

        rows = await _rows(session)
        assert rows[1][:5] == ("rates.xlsx", "application/xlsx", None, None, False)
        assert "самого файла в письме не было" in rows[1][5]


async def test_thread_card_lists_files_without_their_bytes(
    client: AsyncClient,
    sent: MessageModel,
    session: AsyncSession,
    make_user: Any,
    sign_in: Any,
) -> None:
    await _post(
        client,
        sent,
        [
            ("attachment1", 'filename="rates.pdf"', "application/pdf", PDF),
            ("attachment2", 'filename="macro.js"', "text/javascript", b"alert(1)"),
        ],
    )
    await make_user("зритель@site.com", role=UserRole.OPERATOR)
    token = await sign_in("зритель@site.com")

    response = await client.get(f"/api/threads/{sent.thread_id}", headers=bearer(token))

    assert response.status_code == 200, response.text
    reply = (await session.execute(select(ReplyModel))).scalars().one()
    files = response.json()["incoming"][0]["attachments"]
    ids = [row.id for row in (await session.execute(select(ReplyAttachmentModel))).scalars()]
    assert files == [
        {
            "id": ids[0],
            "name": "rates.pdf",
            "size": len(PDF),
            "content_type": "application/pdf",
            "accepted": True,
            "reason": None,
        },
        {
            "id": ids[1],
            "name": "macro.js",
            "size": 8,
            "content_type": "text/javascript",
            "accepted": False,
            "reason": "«.js» — исполняемый файл или скрипт, такие не принимаются",
        },
    ]
    assert response.json()["incoming"][0]["id"] == reply.id
