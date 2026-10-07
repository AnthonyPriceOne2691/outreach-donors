"""Решения по черновику агента продаж — срез 3.5, A3 и A4: проверки на сервере.

API решений один на все этапы (`/api/agent/drafts/{id}/…`, шов; экран — плашка 5.1).
У продаж строгий список причин отклонения (Spec 5.1, `strict_reasons`): причина —
пункт списка или «другое: …» словами; своими словами без «другое» и «другое» без
слов — 422 словами со списком, черновик не тронут. Вид причины — пункт списка или
«другое» — ложится в черновик и в журнал. «Как есть» у отданного человеку — 409
словами. Ответ продаж почта ещё не ведёт: правка упирается в 409 «продажи к почте
ещё не подключены», и черновик остаётся ждать.

Черновик пишется настоящим путём шва по переписке продаж (строка продаж в реестре —
фикстурой `sales_on`, как тумблером); модель — подставной HTTP.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.agent import drafting
from backend.features.agent.stages import OTHER_REASON
from backend.features.core.domain import AuditAction, DraftStatus, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.sales.agent import parts
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.migration_helpers import columns_down_and_up
from tests.test_sales_agent_situation import Plug, llm
from tests.test_sales_agent_stage import GOOD, INFORM, Writer, lead_replied, sales_on

__all__ = ["llm", "sales_on"]  # фикстуры — отсюда их видит pytest

pytestmark = pytest.mark.usefixtures("sales_on")

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

ALLOW = {"claims": [], "promises": [], "tone": {"ok": True, "problem": ""}}
#: Судья-модель ответила не по форме — черновик отдан человеку с текстом.
UNCHECKED = "Всё хорошо."
LISTED = "Отклонить черновик этого этапа можно только с причиной из списка: "
OTHER_WITHOUT_WORDS = "Причина «другое» — только со словами"
REJECT_KIND_REVISION = "1cbf4c6b63f0_agent_drafts_reject_kind.py"


async def _draft(session: AsyncSession, llm: Plug, judge: Any = ALLOW) -> AgentDraftModel:
    reply_id = await lead_replied(session)
    llm(situation=[INFORM], judge=[judge])
    outcome = await drafting.draft_answer(session, Writer(GOOD), reply_id)
    await session.commit()
    assert outcome.draft_id is not None
    draft = await session.get(AgentDraftModel, outcome.draft_id)
    assert draft is not None
    return draft


async def _admin(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("продажи@site.com", role=UserRole.ADMIN)
    return bearer(await sign_in("продажи@site.com"))


async def _decisions(session: AsyncSession) -> list[dict[str, Any]]:
    rows = await session.scalars(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.AGENT_DRAFT_DECIDED)
    )
    return [row.details or {} for row in rows]


async def _undecided(session: AsyncSession, draft: AgentDraftModel, status: DraftStatus) -> None:
    await session.refresh(draft)
    assert (draft.status, draft.decided_at, draft.reject_kind) == (status, None, None)


# --- A3: отклонить без причины из списка → 422 словами -----------------------------------


@pytest.mark.parametrize(
    ("body", "words", "status"),
    [
        ({}, None, 422),
        ({"reason": "   "}, None, 422),
        ({"reason": "плохо"}, LISTED, 422),
        ({"reason": "другое"}, OTHER_WITHOUT_WORDS, 422),
        ({"reason": "другое:   "}, OTHER_WITHOUT_WORDS, 422),
    ],
    ids=["empty", "blank", "own-words", "other-without-words", "other-blank"],
)
async def test_a3_discard_without_a_listed_reason_is_422_in_words(  # A3
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    session: AsyncSession,
    llm: Plug,
    body: dict[str, str],
    words: str | None,
    status: int,
) -> None:
    draft = await _draft(session, llm)
    headers = await _admin(make_user, sign_in)

    refused = await client.post(f"/api/agent/drafts/{draft.id}/reject", json=body, headers=headers)

    assert refused.status_code == status, refused.text
    if words is not None:
        detail = refused.json()["detail"]
        assert detail.startswith(words)
        if words == LISTED:
            assert all(reason in detail for reason in parts.REJECT_REASONS)
            assert "«другое: …» своими словами" in detail
    await _undecided(session, draft, DraftStatus.DRAFTED)
    assert await _decisions(session) == []


@pytest.mark.parametrize(
    ("reason", "kind"),
    [
        ("не тот язык", "не тот язык"),
        ("надо было промолчать", "надо было промолчать"),
        ("другое: лид просил прайс файлом", "другое"),
    ],
    ids=["listed", "listed-last", "other-with-words"],
)
async def test_a3_discard_with_a_listed_reason_keeps_its_kind_in_the_draft_and_journal(  # A3
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    session: AsyncSession,
    llm: Plug,
    reason: str,
    kind: str,
) -> None:
    draft = await _draft(session, llm)
    headers = await _admin(make_user, sign_in)

    rejected = await client.post(
        f"/api/agent/drafts/{draft.id}/reject", json={"reason": reason}, headers=headers
    )

    assert rejected.status_code == 200, rejected.text
    shown = rejected.json()
    assert (shown["status"], shown["reject_kind"], shown["reject_reason"]) == (
        "rejected",
        kind,
        reason,
    )
    [decided] = await _decisions(session)
    assert (decided["решение"], decided["вид причины"], decided["причина"]) == (
        "отклонён",
        kind,
        reason,
    )


async def test_thread_offers_the_sales_reasons_to_the_banner(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn, session: AsyncSession, llm: Plug
) -> None:
    """Плашка 5.1 берёт причины этапа из переписки (`agent_reasons`) — у продаж свои."""
    draft = await _draft(session, llm)
    headers = await _admin(make_user, sign_in)
    reply = await session.get(ReplyModel, draft.reply_id)
    assert reply is not None
    assert reply.thread_id is not None

    shown = await client.get(f"/api/threads/{reply.thread_id}", headers=headers)

    assert shown.status_code == 200, shown.text
    assert shown.json()["agent_reasons"] == list(parts.REJECT_REASONS)


def test_sales_reasons_are_the_spec_list_and_other_is_added_by_the_screen() -> None:
    assert parts.REJECT_REASONS == (
        "неверная ситуация",
        "факт не из базы",
        "не тот язык",
        "длинно",
        "нет призыва",
        "не тот тон",
        "надо было промолчать",
    )
    assert OTHER_REASON not in parts.REJECT_REASONS  # «другое» экран добавляет сам


# --- A4: «как есть» у отданного человеку → 409 словами ------------------------------------


async def test_a4_send_as_is_of_an_escalated_sales_draft_is_409_in_words(  # A4
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    session: AsyncSession,
    llm: Plug,
) -> None:
    draft = await _draft(session, llm, judge=UNCHECKED)
    assert draft.status is DraftStatus.ESCALATED
    headers = await _admin(make_user, sign_in)

    as_is = await client.post(f"/api/agent/drafts/{draft.id}/send", json={}, headers=headers)

    assert as_is.status_code == 409, as_is.text
    assert as_is.json()["detail"] == (
        f"Черновик №{draft.id} отдан человеку ({draft.reason}) — как есть он не уходит, "
        "и прежним текстом через правку тоже: поправьте текст или ответьте сами"
    )
    await _undecided(session, draft, DraftStatus.ESCALATED)
    assert await _decisions(session) == []


async def test_edited_sales_answer_waits_for_the_mail_of_sales_and_the_draft_stays(
    client: AsyncClient,
    make_user: MakeUser,
    sign_in: SignIn,
    session: AsyncSession,
    llm: Plug,
) -> None:
    """Точка 5.1: исполнитель отправки шва — ответ в переписке, а его почта продаж
    ещё не ведёт. Отказ словами, письма нет, черновик ждёт."""
    draft = await _draft(session, llm, judge=UNCHECKED)
    headers = await _admin(make_user, sign_in)

    body = GOOD.replace("Pick any time", "Choose any time")  # правка, а не прежний текст
    edited = await client.post(
        f"/api/agent/drafts/{draft.id}/send", json={"body": body}, headers=headers
    )

    assert edited.status_code == 409, edited.text
    assert edited.json()["detail"].endswith("продажи к почте ещё не подключены")
    await _undecided(session, draft, DraftStatus.ESCALATED)
    answers = await session.scalar(
        select(func.count(MessageModel.id)).where(MessageModel.answers_reply_id == draft.reply_id)
    )
    assert answers == 0


async def test_reject_kind_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up = await connection.run_sync(
        columns_down_and_up, REJECT_KIND_REVISION, "agent_drafts", {"reject_kind"}
    )

    assert (down, up) == (set(), {"reject_kind"})
