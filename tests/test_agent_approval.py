"""Решение по черновику из переписки: устаревший черновик не уходит.

Черновик написан на одно письмо собеседника (`based_on` — его ответ). Пока он
ждёт человека, переписка может уйти дальше: собеседник напишет ещё, или
кто-то ответит ему руками. Тогда «Подходит · отправить» и «Отправить правку»
получают 409 «черновик устарел» — на сервере, а не только на экране: иначе
собеседник получит второй ответ или ответ мимо своего нового письма.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.features.core.domain import DraftStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.replies.pipeline import Inbox
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_agent_decisions import _drafted, no_caps
from tests.test_agent_drafting import stored
from tests.test_replies_inbox import NOW, reply_from, sent
from tests.test_thread_answer import conversation

__all__ = ["conversation", "no_caps", "sent"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
Conversation = tuple[MessageModel, ReplyModel]


async def _admin(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.example", role=UserRole.ADMIN)
    return await sign_in("админ@site.example")


async def _incoming(
    session: AsyncSession,
    first: MessageModel,
    text: str,
    number: int,
    headers: dict[str, str] | None = None,
) -> ReplyModel:
    """Ещё одно письмо собеседника в ту же переписку — после прежних."""
    got = await Inbox(session, now=NOW).accept(
        reply_from(
            first,
            text,
            sender="boss@donor.example.test",
            message_id=f"<in-{number}@site.test>",
            headers=headers or {},
        )
    )
    await session.commit()
    assert got.reply_id is not None
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    assert reply.thread_id == first.thread_id
    return reply


async def _answers_to(session: AsyncSession, reply_id: int) -> int:
    counted = await session.scalar(
        select(func.count())
        .select_from(MessageModel)
        .where(MessageModel.answers_reply_id == reply_id)
    )
    return int(counted or 0)


@pytest.mark.usefixtures("no_caps")
class TestStaleDraft:
    async def test_their_new_letter_makes_the_draft_stale_as_is_and_edited(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        conversation: Conversation,
    ) -> None:
        first, reply = conversation
        draft = await _drafted(session, reply.id)
        await _incoming(session, first, "One more thing: do you take crypto?", 2)
        token = await _admin(make_user, sign_in)
        path = f"/api/agent/drafts/{draft.id}/send"

        as_is = await client.post(path, json={}, headers=bearer(token))
        edited = await client.post(path, json={"body": "Crypto works too."}, headers=bearer(token))

        assert (as_is.status_code, edited.status_code) == (409, 409)
        assert "устарел" in as_is.json()["detail"]
        assert "собеседник написал ещё" in edited.json()["detail"]
        assert (await stored(session, reply.id)).status is DraftStatus.DRAFTED
        assert await _answers_to(session, reply.id) == 0  # письма не собралось

    async def test_our_later_letter_makes_the_draft_stale(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        conversation: Conversation,
    ) -> None:
        first, older = conversation
        newer = await _incoming(session, first, "And what about a second article?", 2)
        # Время ответа задано: оно же у первого письма, и «позже» значит позже его.
        newer.created_at = NOW
        draft = await _drafted(session, newer.id)
        token = await _admin(make_user, sign_in)
        manual = await client.post(
            f"/api/threads/{first.thread_id}/answer",
            json={"reply_id": older.id, "body": "Thanks, the first one is fine."},
            headers=bearer(token),
        )
        assert manual.status_code == 200, manual.text

        refused = await client.post(
            f"/api/agent/drafts/{draft.id}/send", json={}, headers=bearer(token)
        )

        assert refused.status_code == 409
        assert f"наше письмо №{manual.json()['id']}" in refused.json()["detail"]
        assert (await stored(session, newer.id)).status is DraftStatus.DRAFTED
        assert await _answers_to(session, newer.id) == 0

    async def test_autoreply_after_it_does_not_make_the_draft_stale(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        conversation: Conversation,
    ) -> None:
        first, reply = conversation
        draft = await _drafted(session, reply.id)
        robot = await _incoming(
            session,
            first,
            "I am out of the office until Monday.",
            2,
            headers={"Auto-Submitted": "auto-replied"},
        )
        assert robot.kind.value == "auto_reply"
        token = await _admin(make_user, sign_in)

        sent_now = await client.post(
            f"/api/agent/drafts/{draft.id}/send", json={}, headers=bearer(token)
        )

        assert sent_now.status_code == 200, sent_now.text
        assert (await stored(session, reply.id)).status is DraftStatus.SENT
