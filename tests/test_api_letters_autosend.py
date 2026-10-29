"""Маршруты очереди, которая уходит сама (`letters/autosend.py`): отметка «дальше — сама» в окне
пачки включает режим, «Остановить» — выключает; экран писем видит, идёт ли режим, кто его включил
и чем кончился проход. Без отметки и при выключенном рубильнике режима нет.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import QueueAutosendModel
from backend.features.letters import autosend
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_letters_send_queue import FakeQueue, _queue, queue  # noqa: F401 — фикстура

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


async def _signed(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("отправитель@site.com", role=UserRole.ADMIN)
    return await sign_in("отправитель@site.com")


async def _launches(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(QueueAutosendModel)) or 0)


class TestKeepSending:
    async def test_the_mark_starts_the_mode_and_the_screen_sees_it(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        queue: FakeQueue,  # noqa: F811
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        await _queue(session, 2)
        token = await _signed(make_user, sign_in)

        sent = await client.post(
            "/api/letters/send-queue",
            json={"stage": "donors", "keep_sending": True},
            headers=bearer(token),
        )
        view = await client.get("/api/letters", headers=bearer(token))

        assert sent.status_code == 200, sent.text
        assert sent.json()["autosend"] is True
        shown = view.json()["autosend"]
        assert (shown["available"], shown["running"], shown["started_by"]) == (
            True,
            True,
            "отправитель@site.com",
        )
        journal = await session.scalar(
            select(AuditLogModel.details).where(AuditLogModel.action == AuditAction.RUN_STARTED)
        )
        assert journal is not None
        assert journal["действие"] == "очередь уходит сама"

    async def test_a_second_press_keeps_the_same_launch(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        queue: FakeQueue,  # noqa: F811
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        await _queue(session, 2)
        token = await _signed(make_user, sign_in)
        body = {"stage": "donors", "keep_sending": True}

        await client.post("/api/letters/send-queue", json=body, headers=bearer(token))
        await client.post("/api/letters/send-queue", json=body, headers=bearer(token))

        assert await _launches(session) == 1

    async def test_without_the_mark_there_is_no_mode(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        queue: FakeQueue,  # noqa: F811
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        await _queue(session, 2)
        token = await _signed(make_user, sign_in)

        sent = await client.post(
            "/api/letters/send-queue", json={"stage": "donors"}, headers=bearer(token)
        )

        assert sent.json()["autosend"] is False
        assert await _launches(session) == 0

    async def test_the_switched_off_service_ignores_the_mark(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        queue: FakeQueue,  # noqa: F811
        make_user: MakeUser,
        sign_in: SignIn,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(outreach_cfg, "AUTOSEND", False)
        await _queue(session, 2)
        token = await _signed(make_user, sign_in)

        sent = await client.post(
            "/api/letters/send-queue",
            json={"stage": "donors", "keep_sending": True},
            headers=bearer(token),
        )
        view = await client.get("/api/letters", headers=bearer(token))

        assert sent.json()["autosend"] is False
        assert view.json()["autosend"]["available"] is False
        assert await _launches(session) == 0


class TestStop:
    async def test_stop_ends_the_launch_and_says_who(
        self,
        client: AsyncClient,
        session: AsyncSession,
        filled_legal: None,
        queue: FakeQueue,  # noqa: F811
        make_user: MakeUser,
        sign_in: SignIn,
    ) -> None:
        await _queue(session, 2)
        token = await _signed(make_user, sign_in)
        await client.post(
            "/api/letters/send-queue",
            json={"stage": "donors", "keep_sending": True},
            headers=bearer(token),
        )

        stopped = await client.post(
            "/api/letters/send-queue/stop", json={"stage": "donors"}, headers=bearer(token)
        )
        again = await client.post(
            "/api/letters/send-queue/stop", json={"stage": "donors"}, headers=bearer(token)
        )

        assert stopped.status_code == 200, stopped.text
        shown = stopped.json()
        assert (shown["running"], shown["stopped_by"], shown["stop_reason"]) == (
            False,
            "отправитель@site.com",
            autosend.BY_HUMAN,
        )
        assert again.status_code == 409
        assert "не уходит сама" in again.json()["detail"]
        changed = await session.scalar(
            select(AuditLogModel.details).where(AuditLogModel.action == AuditAction.USER_UPDATED)
        )
        assert changed is not None
        assert changed["действие"] == "очередь больше не уходит сама"
