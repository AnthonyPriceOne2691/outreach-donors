"""Решения по черновику агента: отправить как есть, с правкой, отклонить.

Решения общие для всех этапов и проверяются сервером. На настоящей базе и
через маршруты: отклонить без причины нельзя (422), второе решение не ложится
(409), «как есть» у отданного человеку закрыто (409), а с правкой — уходит
путём ответа человека; ответ из переписки закрывает черновик; список «ждут
человека» и черновик целиком с `meta` видит смотрящий, а решает — право send.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.api.threads import routes as thread_routes
from backend.config import llm as llm_cfg
from backend.features.agent import drafting
from backend.features.agent.stages import Brief
from backend.features.agent.writer import Written
from backend.features.core.domain import AuditAction, DraftStatus, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import load_migration
from tests.test_agent_drafting import FakeWriter, agent_on, briefed, donors_with, stored
from tests.test_replies_inbox import sent
from tests.test_thread_answer import conversation

__all__ = ["conversation", "sent"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
Conversation = tuple[MessageModel, ReplyModel]

_DOUBT = Written(body="We could do $120.", needs_human=True, reason="цена за пределом", tokens=9)


@pytest.fixture
def no_caps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "RUN_TOKEN_CAP", 0)


async def _drafted(
    session: AsyncSession, reply_id: int, written: Written | None = None
) -> AgentDraftModel:
    await agent_on(session)
    await drafting.draft_answer(session, FakeWriter(written), reply_id)
    await session.commit()
    return await stored(session, reply_id)


async def _admin(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


@pytest.mark.usefixtures("no_caps")
class TestDecisions:
    async def test_reject_needs_a_reason_and_is_the_only_decision(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        conversation: Conversation,
    ) -> None:
        draft = await _drafted(session, conversation[1].id)
        token = await _admin(make_user, sign_in)
        path = f"/api/agent/drafts/{draft.id}/reject"

        missing = await client.post(path, json={}, headers=bearer(token))
        blank = await client.post(path, json={"reason": "   "}, headers=bearer(token))
        rejected = await client.post(path, json={"reason": "тон не тот"}, headers=bearer(token))
        again = await client.post(path, json={"reason": "ещё раз"}, headers=bearer(token))
        send = await client.post(
            f"/api/agent/drafts/{draft.id}/send", json={}, headers=bearer(token)
        )

        assert (missing.status_code, blank.status_code) == (422, 422)
        # Словами ядра, а не списком полей проверки: правило одно у экрана и API.
        no_reason = "Отклонить черновик можно только с причиной"
        assert (missing.json()["detail"], blank.json()["detail"]) == (no_reason, no_reason)
        assert rejected.status_code == 200, rejected.text
        shown = rejected.json()
        assert (shown["status"], shown["reject_reason"]) == ("rejected", "тон не тот")
        assert shown["decided_by"] == "админ@site.com"
        assert (again.status_code, send.status_code) == (409, 409)
        audit = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.AGENT_DRAFT_DECIDED)
        )
        assert audit is not None
        assert audit.details is not None
        assert (audit.details["решение"], audit.details["причина"]) == ("отклонён", "тон не тот")

    @pytest.mark.parametrize(
        ("reason", "kind"),
        [
            ("тон", None),
            ("не тот тон", "не тот тон"),
            ("другое: просил ответить завтра", "другое"),
        ],
        ids=["own-words", "listed", "other-with-words"],
    )
    async def test_reason_kind_is_the_listed_reason_and_donors_may_use_own_words(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        conversation: Conversation,
        reason: str,
        kind: str | None,
    ) -> None:
        """Вид причины — пункт списка этапа или «другое»: по нему считает калибровка.
        Список доноров не строгий: причина своими словами принимается, без вида."""
        draft = await _drafted(session, conversation[1].id)
        token = await _admin(make_user, sign_in)

        done = await client.post(
            f"/api/agent/drafts/{draft.id}/reject", json={"reason": reason}, headers=bearer(token)
        )

        assert done.status_code == 200, done.text
        assert (done.json()["reject_reason"], done.json()["reject_kind"]) == (reason, kind)

    async def test_escalated_draft_does_not_go_as_is_but_goes_edited(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        conversation: Conversation,
    ) -> None:
        reply = conversation[1]
        draft = await _drafted(session, reply.id, _DOUBT)
        assert draft.status is DraftStatus.ESCALATED
        token = await _admin(make_user, sign_in)
        path = f"/api/agent/drafts/{draft.id}/send"

        as_is = await client.post(path, json={}, headers=bearer(token))
        edited = await client.post(path, json={"body": "We could do $100."}, headers=bearer(token))

        assert as_is.status_code == 409
        assert "как есть он не уходит" in as_is.json()["detail"]
        assert edited.status_code == 200, edited.text
        draft = await stored(session, reply.id)
        assert (draft.status, draft.edited, draft.final_body) == (
            DraftStatus.SENT,
            True,
            "We could do $100.",
        )
        assert draft.sent_message_id == edited.json()["id"]

    async def test_ready_draft_goes_as_is_by_the_answer_path(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        conversation: Conversation,
    ) -> None:
        reply = conversation[1]
        draft = await _drafted(session, reply.id)
        token = await _admin(make_user, sign_in)

        sent_now = await client.post(
            f"/api/agent/drafts/{draft.id}/send", json={}, headers=bearer(token)
        )

        assert sent_now.status_code == 200, sent_now.text
        assert sent_now.json()["sender_email"] == "anna@mail.test"  # ящик переписки
        letter = await session.get(MessageModel, sent_now.json()["id"])
        assert letter is not None
        assert (letter.answers_reply_id, letter.body) == (reply.id, draft.body)
        draft = await stored(session, reply.id)
        assert (draft.status, draft.edited, draft.decided_by) == (
            DraftStatus.SENT,
            False,
            "админ@site.com",
        )

    async def test_answer_from_the_thread_closes_the_draft(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        conversation: Conversation,
    ) -> None:
        first, reply = conversation
        await _drafted(session, reply.id, _DOUBT)
        token = await _admin(make_user, sign_in)

        answered = await client.post(
            f"/api/threads/{first.thread_id}/answer",
            json={"reply_id": reply.id, "body": "Our limit is $100, sorry."},
            headers=bearer(token),
        )

        assert answered.status_code == 200, answered.text
        draft = await stored(session, reply.id)
        assert (draft.status, draft.edited) == (DraftStatus.SENT, True)
        assert draft.sent_message_id == answered.json()["id"]

    async def test_draft_close_failure_does_not_turn_a_sent_answer_into_a_refusal(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Письмо уже ушло и записано — сбой закрытия черновика не даёт 500:
        человек видит «отправлено», черновик ждёт человека, причина в журнале."""
        first, reply = conversation
        await _drafted(session, reply.id, _DOUBT)
        token = await _admin(make_user, sign_in)

        async def broken(*_: object, **__: object) -> None:
            await session.execute(text("SELECT * FROM no_such_table"))

        monkeypatch.setattr(thread_routes, "settle_sent", broken)
        with caplog.at_level(logging.WARNING, logger=thread_routes.__name__):
            answered = await client.post(
                f"/api/threads/{first.thread_id}/answer",
                json={"reply_id": reply.id, "body": "Our limit is $100, sorry."},
                headers=bearer(token),
            )

        assert answered.status_code == 200, answered.text
        assert answered.json()["sender_email"]
        assert await session.get(MessageModel, answered.json()["id"]) is not None
        draft = await stored(session, reply.id)
        assert (draft.status, draft.sent_message_id) == (DraftStatus.ESCALATED, None)
        assert f"письмо №{answered.json()['id']}), а черновик к нему не закрыт" in caplog.text

    async def test_waiting_list_and_detail_with_meta(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        conversation: Conversation,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        donors_with(monkeypatch, brief=briefed(Brief(meta={"situation": "asks_price"})))
        draft = await _drafted(session, conversation[1].id, _DOUBT)
        await make_user("смотрит@site.com", role=UserRole.OPERATOR)
        token = await sign_in("смотрит@site.com")

        waiting = await client.get("/api/agent/drafts", headers=bearer(token))
        ready = await client.get("/api/agent/drafts?status=drafted", headers=bearer(token))
        detail = await client.get(f"/api/agent/drafts/{draft.id}", headers=bearer(token))
        missing = await client.get("/api/agent/drafts/999999", headers=bearer(token))
        refused = await client.post(
            f"/api/agent/drafts/{draft.id}/reject", json={"reason": "нет"}, headers=bearer(token)
        )

        assert [card["id"] for card in waiting.json()] == [draft.id]
        assert ready.json() == []
        assert detail.json()["meta"] == {"situation": "asks_price"}
        assert detail.json()["reason"] == "цена за пределом"
        assert missing.status_code == 404
        assert refused.status_code == 403  # решать — право send


def _journal_values(connection: Connection) -> list[str]:
    """Ревизия журнала ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие
    его не видит. `ADD VALUE IF NOT EXISTS` делает повтор безвредным."""
    migration = load_migration("3924977db911_agent_draft_decided_audit_action.py")
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()
    values = connection.execute(text("SELECT unnest(enum_range(NULL::auditaction))::text"))
    return list(values.scalars())


async def test_journal_value_is_there_once_and_survives_a_rerun(session: AsyncSession) -> None:
    connection = await session.connection()
    values = await connection.run_sync(_journal_values)
    assert values.count("agent_draft_decided") == 1
