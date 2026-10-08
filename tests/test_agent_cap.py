"""Свой дневной потолок черновиков агента: черновики не выбирают день у разбора ответов.

Общий потолок (`LLM_DAILY_TOKEN_CAP`) считается по всем операциям модели, и
черновики на сотни лидов выбрали бы его целиком — разбор ответов доноров встал
бы до завтра. У черновиков свой потолок (`AGENT_DAILY_TOKEN_CAP`, не задан — доля
общего) тем же журналом расхода, по операциям агента из реестра этапов. На
настоящей базе: разбор ответа донора проходит, а черновик получает отказ
словами, и модель не зовётся; общий «потолка нет» без своего — нет его и у
черновиков; свой задан при общем 0 — действует свой.
"""

from __future__ import annotations

import pytest
from backend.config import llm as llm_cfg
from backend.features.agent import drafting, guarding
from backend.features.core import usage
from backend.features.core.models.outreach import ReplyModel
from backend.features.core.usage import LlmCapExceededError
from backend.features.replies.pipeline import Parser
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_agent_drafting import FakeWriter, agent_on, reply
from tests.test_parse_requeue import CountingExtractor
from tests.test_replies_inbox import NOW, sent

__all__ = ["reply", "sent"]  # фикстуры черновика — отсюда их видит pytest


async def test_spent_drafts_cap_lets_the_parse_go_and_refuses_the_draft_in_words(
    session: AsyncSession, reply: ReplyModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 5_437)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", 83)
    await agent_on(session)
    usage.record(session, operation="agent_draft", units=61)  # почти выбран
    await drafting.draft_answer(session, FakeWriter(), reply.id)  # +40 — последний, что влез
    model = CountingExtractor()
    agent = FakeWriter()

    await Parser(session, model, now=NOW).parse(reply.id)  # type: ignore[arg-type]
    with pytest.raises(LlmCapExceededError) as refused:
        await drafting.draft_answer(session, agent, reply.id, again=True)

    assert model.calls == 1  # разбор ответа донора прошёл: общий потолок не выбран
    assert agent.seen == []  # модель черновика не звали
    # 321 токен разбора — не в счёте черновиков: только 61 + 40.
    assert str(refused.value) == (
        "дневной потолок черновиков агента выбран: 101 из 83 токенов (AGENT_DAILY_TOKEN_CAP); "
        "завтра или поднимите AGENT_DAILY_TOKEN_CAP"
    )


@pytest.mark.parametrize(
    ("general", "own", "spent", "refused"),
    [
        (5_437, None, 1_631, True),  # не задан — 30 % общего: 1631 из 1631
        (5_437, None, 1_630, False),  # и разбор (97) в счёт черновиков не идёт
        (0, None, 98_765, False),  # общий «потолка нет», свой не задан — нет и у черновиков
        (0, 83, 83, True),  # свой задан при общем 0 — действует свой
    ],
)
async def test_drafts_cap_is_the_setting_or_a_share_of_the_general_one(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    general: int,
    own: int | None,
    spent: int,
    refused: bool,
) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", general)
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", own)
    usage.record(session, operation="agent_draft", units=spent)
    usage.record(session, operation="reply_parse", units=97)

    if refused:
        with pytest.raises(LlmCapExceededError, match="потолок черновиков агента"):
            await usage.ensure_llm_within_cap(session, own=guarding.drafts_cap())
    else:
        await usage.ensure_llm_within_cap(session, own=guarding.drafts_cap())


@pytest.mark.parametrize(
    ("own", "general", "expected"),
    [
        (None, 10_000, 2_000),  # не задан — доля общего
        (None, 3, 1),  # доля меньше токена — хотя бы один, а не «потолка нет»
        (None, 0, 0),  # общий «потолка нет» — нет и у части
        (500, 10_000, 500),  # задан — как есть
        (0, 10_000, 0),  # задан 0 — своего потолка нет, общий проверяется всё равно
    ],
)
def test_share_cap_is_the_setting_or_a_share_of_the_general_one(
    monkeypatch: pytest.MonkeyPatch, own: int | None, general: int, expected: int
) -> None:
    """Одно правило своих потолков — у черновиков агента и у вызовов модели продаж."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", general)

    assert usage.share_cap(own, llm_cfg.SALES_CAP_SHARE) == expected


@pytest.mark.parametrize(("raw", "parsed"), [("", None), ("1200", 1200), ("0", 0)])
def test_empty_sales_cap_in_env_is_unset_not_a_parse_error(
    monkeypatch: pytest.MonkeyPatch, raw: str, parsed: int | None
) -> None:
    """Пустое `SALES_DAILY_TOKEN_CAP=` в `.env` — «не задан» (доля общего), как у агента."""
    monkeypatch.setenv("SALES_DAILY_TOKEN_CAP", raw)

    assert llm_cfg._Llm().sales_daily_token_cap == parsed
