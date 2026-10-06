"""Черновик агента: кому положен, что видит этап до письма, где лежит.

Проверяется на настоящей базе то, чего не видно по зелёному прогону: агенту
без настроек и выключенному платить не за что; отписке, автоответчику и
отвеченному ответу черновик не пишется; повтор не платит модели второй раз,
а «написать заново» переписывает черновик; расход ложится в журнал операцией
этапа; пропуск брифа двух видов модель не зовёт, а `human` не пишет текста;
факты брифа доходят до писателя, а `meta` — до черновика; сбой крючка
уведомления черновик не теряет.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from decimal import Decimal
from types import MappingProxyType
from typing import Any

import pytest
from backend.config import llm as llm_cfg
from backend.config import outreach as outreach_cfg
from backend.features.agent import drafting
from backend.features.agent import writer as agent_writer
from backend.features.agent.settings import AgentSettingsRepository, defaults
from backend.features.agent.stages import (
    AGENT_STAGES,
    AgentStage,
    Brief,
    BriefHook,
    Conversation,
    DraftNotice,
    Skip,
    SkipKind,
)
from backend.features.agent.writer import Request, Turn, Written
from backend.features.core.domain import DraftStatus, ReplyKind, Stage, ThreadStatus
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel, ReplyModel, ThreadModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.replies.pipeline import Inbox
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_replies_inbox import NOW, SECRET, reply_from, sent

__all__ = ["sent"]  # фикстура приёма — отсюда её видит pytest


class FakeWriter:
    """Агент на месте модели: запоминает просьбу, отвечает заданным."""

    def __init__(self, written: Written | None = None) -> None:
        self.written = written or Written(
            body="Thanks! A guide on home repair works.", needs_human=False, reason=None, tokens=40
        )
        self.seen: list[Request] = []

    async def write(self, request: Request) -> Written:
        self.seen.append(request)
        return self.written


@pytest.fixture
async def reply(
    session: AsyncSession, sent: MessageModel, monkeypatch: pytest.MonkeyPatch
) -> ReplyModel:
    """Донор ответил человеком, с ценой и цитатой нашего письма."""
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    monkeypatch.setattr(outreach_cfg, "SENDER_NAME", "Anna")
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)
    monkeypatch.setattr(llm_cfg, "RUN_TOKEN_CAP", 0)
    got = await Inbox(session, now=NOW).accept(
        reply_from(sent, "Our price is $90. Which topic?\n\n> Good afternoon,")
    )
    await session.commit()
    assert got.reply_id is not None
    found = await session.get(ReplyModel, got.reply_id)
    assert found is not None
    found.price_white = Decimal("90.00")
    found.currency = "USD"
    await session.commit()
    return found


async def agent_on(session: AsyncSession, *, enabled: bool = True) -> None:
    await AgentSettingsRepository(session).save(
        Stage.DONORS, replace(defaults(Stage.DONORS), enabled=enabled), author="a@x"
    )
    await session.commit()


def donors_with(monkeypatch: pytest.MonkeyPatch, **parts: Any) -> AgentStage:
    """Этап доноров с подменёнными частями — так встанет строка нового этапа."""
    stage = replace(AGENT_STAGES[Stage.DONORS], **parts)
    registry = MappingProxyType({**AGENT_STAGES, Stage.DONORS: stage})
    monkeypatch.setattr(drafting, "AGENT_STAGES", registry)
    return stage


def briefed(brief: Brief, seen: list[Conversation] | None = None) -> BriefHook:
    async def hook(_session: AsyncSession, conversation: Conversation) -> Brief:
        if seen is not None:
            seen.append(conversation)
        return brief

    return hook


async def stored(session: AsyncSession, reply_id: int) -> AgentDraftModel:
    draft = await session.scalar(
        select(AgentDraftModel).where(AgentDraftModel.reply_id == reply_id)
    )
    assert draft is not None
    await session.refresh(draft)
    return draft


class TestWho:
    async def test_writes_a_draft_from_the_whole_conversation(
        self, session: AsyncSession, reply: ReplyModel
    ) -> None:
        await agent_on(session)
        agent = FakeWriter()

        outcome = await drafting.draft_answer(session, agent, reply.id)

        assert outcome.skipped is None
        [request] = agent.seen
        assert [turn.ours for turn in request.turns] == [True, False]
        assert request.turns[0].text == "Good afternoon,"
        assert request.turns[1].text == "Our price is $90. Which topic?"  # без цитаты
        assert request.parsed == {"price_white": "90.00", "currency": "USD"}
        assert request.sign_as == "Anna"
        assert (request.prompt, request.model) == (agent_writer.PROMPT_PATH, llm_cfg.AGENT_MODEL)
        draft = await stored(session, reply.id)
        assert (draft.status, draft.body) == (
            DraftStatus.DRAFTED,
            "Thanks! A guide on home repair works.",
        )
        assert (draft.prompt_version, draft.meta) == (agent_writer.PROMPT_VERSION, {})

    @pytest.mark.parametrize(
        ("setup", "why"),
        [
            ("not_configured", "не настроен"),
            ("disabled", "выключен"),
            ("robot", "не письмо человека"),
            ("unsubscribed", "unsubscribed"),
        ],
    )
    async def test_no_draft_where_nobody_will_answer(
        self, session: AsyncSession, reply: ReplyModel, setup: str, why: str
    ) -> None:
        if setup != "not_configured":
            await agent_on(session, enabled=setup != "disabled")
        if setup == "robot":
            reply.kind = ReplyKind.AUTO_REPLY
        if setup == "unsubscribed":
            thread = await session.get(ThreadModel, reply.thread_id)
            assert thread is not None
            thread.status = ThreadStatus.UNSUBSCRIBED
        await session.commit()
        agent = FakeWriter()

        outcome = await drafting.draft_answer(session, agent, reply.id)

        assert outcome.skipped is not None
        assert why in outcome.skipped
        assert agent.seen == []

    async def test_answered_reply_gets_no_draft(
        self, session: AsyncSession, reply: ReplyModel, sent: MessageModel
    ) -> None:
        await agent_on(session)
        sent.answers_reply_id = reply.id  # будто наш ответ уже ушёл
        await session.commit()

        outcome = await drafting.draft_answer(session, FakeWriter(), reply.id)

        assert outcome.skipped == "на этот ответ уже ответили"


class TestAgain:
    async def test_retry_does_not_pay_twice_but_again_rewrites_until_decided(
        self, session: AsyncSession, reply: ReplyModel
    ) -> None:
        await agent_on(session)
        first = await drafting.draft_answer(session, FakeWriter(), reply.id)
        retry_agent = FakeWriter()

        retried = await drafting.draft_answer(session, retry_agent, reply.id)
        doubt = Written(body="Second take", needs_human=True, reason="не уверен", tokens=7)
        again = await drafting.draft_answer(session, FakeWriter(doubt), reply.id, again=True)
        (await stored(session, reply.id)).status = DraftStatus.REJECTED
        decided = await drafting.draft_answer(session, FakeWriter(), reply.id, again=True)

        assert retried.skipped == "черновик уже написан"
        assert retry_agent.seen == []
        assert again.draft_id == first.draft_id  # тот же черновик, переписан
        assert again.status is DraftStatus.ESCALATED  # агент сомневается — человеку
        assert decided.skipped is not None
        assert "уже решили" in decided.skipped

    async def test_spend_is_journaled_as_the_stage_operation(
        self, session: AsyncSession, reply: ReplyModel
    ) -> None:
        await agent_on(session)

        await drafting.draft_answer(session, FakeWriter(), reply.id)

        units = await session.scalar(
            select(func.sum(UsageRecordModel.units)).where(
                UsageRecordModel.operation == AGENT_STAGES[Stage.DONORS].usage_operation
            )
        )
        assert units == 40

    async def test_cap_stops_before_the_model(
        self, session: AsyncSession, reply: ReplyModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await agent_on(session)
        monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 1)
        await drafting.draft_answer(session, FakeWriter(), reply.id)  # 40 токенов — за потолком
        agent = FakeWriter()

        with pytest.raises(LlmCapExceededError):
            await drafting.draft_answer(session, agent, reply.id, again=True)
        assert agent.seen == []


class TestBrief:
    @pytest.mark.parametrize(
        ("kind", "status"),
        [(SkipKind.NO_REPLY, DraftStatus.SKIPPED), (SkipKind.HUMAN, DraftStatus.ESCALATED)],
    )
    async def test_skip_does_not_call_the_model_and_leaves_no_text(
        self,
        session: AsyncSession,
        reply: ReplyModel,
        monkeypatch: pytest.MonkeyPatch,
        kind: SkipKind,
        status: DraftStatus,
    ) -> None:
        skip = Skip(kind, "собеседник благодарит")
        donors_with(monkeypatch, brief=briefed(Brief(skip=skip, meta={"situation": "ack"})))
        await agent_on(session)
        agent = FakeWriter()

        outcome = await drafting.draft_answer(session, agent, reply.id)

        assert agent.seen == []  # модель не звали
        assert outcome.status is status
        draft = await stored(session, reply.id)
        assert (draft.status, draft.body, draft.reason) == (status, "", "собеседник благодарит")
        assert draft.meta == {"situation": "ack", "skip": kind.value}
        assert await session.scalar(select(func.count()).select_from(UsageRecordModel)) == 0

    async def test_facts_reach_the_writer_and_meta_lands_in_the_draft(
        self, session: AsyncSession, reply: ReplyModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[Conversation] = []
        brief = Brief(
            facts=("Аудит — цена на созвоне",),
            meta={"kb_version": "k7", "confidence": Decimal("0.87")},
        )
        donors_with(monkeypatch, brief=briefed(brief, seen))
        await agent_on(session)
        agent = FakeWriter()

        await drafting.draft_answer(session, agent, reply.id)

        [conversation] = seen
        assert conversation.turns[-1] == Turn(ours=False, text="Our price is $90. Which topic?")
        assert conversation.settings.goal == defaults(Stage.DONORS).goal
        assert agent.seen[0].facts == ("Аудит — цена на созвоне",)
        draft = await stored(session, reply.id)
        assert draft.status is DraftStatus.DRAFTED
        assert draft.meta == {"kb_version": "k7", "confidence": "0.87"}  # сумма — строкой

    async def test_forced_draft_writes_over_the_skip_and_remembers_it(
        self, session: AsyncSession, reply: ReplyModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        donors_with(monkeypatch, brief=briefed(Brief(skip=Skip(SkipKind.NO_REPLY, "спасибо"))))
        await agent_on(session)
        agent = FakeWriter()

        await drafting.draft_answer(session, agent, reply.id, force=True)

        assert len(agent.seen) == 1
        draft = await stored(session, reply.id)
        assert draft.status is DraftStatus.DRAFTED
        assert draft.meta == {"forced_over_skip": {"kind": "no_reply", "reason": "спасибо"}}


class TestAnnounce:
    async def test_hook_hears_ready_drafts_and_its_failure_keeps_the_draft(
        self,
        session: AsyncSession,
        reply: ReplyModel,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        heard: list[DraftNotice] = []

        async def hook(notice: DraftNotice) -> None:
            heard.append(notice)
            raise RuntimeError("канал уведомлений недоступен")

        donors_with(monkeypatch, on_draft=hook)
        await agent_on(session)
        outcome = await drafting.draft_answer(session, FakeWriter(), reply.id)
        await session.commit()

        with caplog.at_level(logging.ERROR):
            await drafting.announce(outcome)  # не бросает

        [notice] = heard
        assert (notice.draft_id, notice.status) == (outcome.draft_id, DraftStatus.DRAFTED)
        assert notice.thread_id == reply.thread_id
        assert "черновик цел" in caplog.text
        assert (await stored(session, reply.id)).status is DraftStatus.DRAFTED

    async def test_skipped_draft_is_not_announced(
        self, session: AsyncSession, reply: ReplyModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        heard: list[DraftNotice] = []

        async def hook(notice: DraftNotice) -> None:
            heard.append(notice)

        skip = Brief(skip=Skip(SkipKind.NO_REPLY, "спасибо"))
        donors_with(monkeypatch, on_draft=hook, brief=briefed(skip))
        await agent_on(session)

        await drafting.announce(await drafting.draft_answer(session, FakeWriter(), reply.id))

        assert heard == []
