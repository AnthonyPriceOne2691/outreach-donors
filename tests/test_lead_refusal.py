"""«В отказ» у лида рекламодателя (слово владельца 25.11.2026).

Отказ рекламодателя («thanks, not interested») висел в «Диалогах с рекламодателями» как
«лид — ждёт человека», а снять его можно было только «Взять в работу» — с передачей в CRM.
Проверяется: отказ снимает ответ с ожидания и в CRM не уходит; переписка — «лид в отказе», карточка
ответа называет решение; взять или передать лид в отказе нельзя, и в выгрузку он не попадает;
повтор, взятый лид и ответ донора — отказ словами; кто и когда — в журнале.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import AuditAction, ReplyDecision, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.replies import lead_handoff
from backend.features.replies.pipeline import Inbox
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_lead_handoff import hook, queue
from tests.test_letter_draft import FakeQueue
from tests.test_letters_advertisers import NOW, SECRET, answer_to, sent_offer
from tests.test_replies_inbox import inbound_secret, reply_from, sent

__all__ = ["hook", "inbound_secret", "queue", "sent"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
REFUSAL = "Thanks for the offer, but we are not interested right now."


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("анна@site.com", role=UserRole.ADMIN)
    return await sign_in("анна@site.com")


async def _refusal(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> ReplyModel:
    """Ответ рекламодателя на оффер — лид, который ждёт человека."""
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    got = await Inbox(session, now=NOW).accept(answer_to(await sent_offer(session), REFUSAL))
    await session.commit()
    assert got.reply_id is not None
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    return reply


@pytest.mark.usefixtures("filled_legal", "hook")
class TestRefuse:
    async def test_refused_lead_waits_for_nobody_and_goes_nowhere(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        queue: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reply = await _refusal(session, monkeypatch)
        before = await client.get(f"/api/threads/{reply.thread_id}", headers=bearer(token))
        assert before.json()["card"]["state"] == "lead"

        refused = await client.post(f"/api/replies/{reply.id}/lead/refuse", headers=bearer(token))

        assert refused.status_code == 200, refused.text
        assert refused.json()["reviewed_by"] == "анна@site.com"
        assert queue.kwargs == []  # адрес CRM задан, а передачи нет
        after = (await client.get(f"/api/threads/{reply.thread_id}", headers=bearer(token))).json()
        assert after["card"]["state"] == "lead_refused"
        card = next(c for c in after["incoming"] if c["id"] == reply.id)
        assert (card["decision"], card["reviewed_by"]) == (ReplyDecision.REFUSED, "анна@site.com")
        assert card["reviewed_at"] is not None

    async def test_refused_lead_is_neither_taken_nor_sent_nor_exported(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        queue: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reply = await _refusal(session, monkeypatch)
        await client.post(f"/api/replies/{reply.id}/lead/refuse", headers=bearer(token))

        again = await client.post(f"/api/replies/{reply.id}/lead/refuse", headers=bearer(token))
        taken = await client.post(f"/api/replies/{reply.id}/lead", headers=bearer(token))
        sent_again = await client.post(f"/api/replies/{reply.id}/lead/send", headers=bearer(token))

        assert again.status_code == taken.status_code == sent_again.status_code == 409
        assert "уже в отказе: отказал анна@site.com" in again.json()["detail"]
        assert "уже в отказе" in taken.json()["detail"]
        assert queue.kwargs == []
        assert await lead_handoff.lead_card(session, reply.id) is None
        assert await lead_handoff.leads(session) == []

    async def test_taken_lead_is_not_refused(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        queue: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reply = await _refusal(session, monkeypatch)
        await client.post(f"/api/replies/{reply.id}/lead", headers=bearer(token))

        refused = await client.post(f"/api/replies/{reply.id}/lead/refuse", headers=bearer(token))

        assert refused.status_code == 409
        assert "уже в работе: взял анна@site.com" in refused.json()["detail"]
        await session.refresh(reply)
        assert reply.decision is None
        assert len(queue.kwargs) == 1  # передача — только от «взять»

    async def test_the_journal_says_who_refused(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        queue: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reply = await _refusal(session, monkeypatch)

        await client.post(f"/api/replies/{reply.id}/lead/refuse", headers=bearer(token))

        rows = (
            await session.scalars(
                select(AuditLogModel).where(AuditLogModel.action == AuditAction.LEAD_REFUSED)
            )
        ).all()
        assert [(row.target, (row.details or {}).get("действие")) for row in rows] == [
            (f"reply:{reply.id}", "лид — в отказ")
        ]


async def test_donor_answer_is_not_refused(
    client: AsyncClient,
    token: str,
    session: AsyncSession,
    sent: Any,
    inbound_secret: None,
) -> None:
    got = await Inbox(session, now=NOW).accept(reply_from(sent, "Our price is $90."))
    await session.commit()
    assert got.reply_id is not None

    refused = await client.post(f"/api/replies/{got.reply_id}/lead/refuse", headers=bearer(token))

    assert refused.status_code == 409
    assert "не лид" in refused.json()["detail"]
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    assert (reply.decision, reply.reviewed_at) == (None, None)
