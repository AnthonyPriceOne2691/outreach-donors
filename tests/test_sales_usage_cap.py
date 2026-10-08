"""Свой дневной потолок вызовов модели продаж: продажи не выбирают день у разбора цен доноров.

Вид ответа лида (`sales_reply_kind`) и переписывание писем очереди (`sales_letter_rewrite`)
считает свой потолок (`SALES_DAILY_TOKEN_CAP`, не задан — доля общего `SALES_CAP_SHARE`) тем
же журналом расхода; общий проверяется всегда. На настоящей базе: свой потолок выбран — вид
ответа отказывает словами с именем настройки и модель не зовётся, а разбор цены донора
проходит; сборка очереди останавливается словами того же потолка, а переписывание письма
продаж пишется своей операцией, не операцией писем доноров.
"""

from __future__ import annotations

import pytest
from backend.config import llm as llm_cfg
from backend.features.agent.stages import agent_operations
from backend.features.core import usage
from backend.features.core.domain import UsageProvider
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.core.usage import OPERATION_PROVIDERS, LlmCapExceededError
from backend.features.replies.pipeline import Parser
from backend.features.sales import queue, usage_cap
from backend.features.sales.replies import SalesReplies
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_agent_drafting import reply
from tests.test_parse_requeue import CountingExtractor
from tests.test_replies_inbox import NOW, sent
from tests.test_sales_reply_kind import _sales_reply
from tests.test_sales_reply_routing import FakeClassifier, secret

__all__ = ["reply", "secret", "sent"]  # фикстуры ответа донора и подписи адреса — их видит pytest

#: Слова отказа своего потолка продаж — с именем настройки, которой его поднять.
REFUSED = (
    "дневной потолок вызовов модели продаж выбран: {spent} из {cap} токенов "
    "(SALES_DAILY_TOKEN_CAP); завтра или поднимите SALES_DAILY_TOKEN_CAP"
)


async def _sales_cap_spent(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    """Свой потолок продаж выбран, общий — нет: 61 + 40 из 83, общий 5437."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 5_437)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 83)
    usage.record(session, operation="sales_reply_kind", units=61)
    usage.record(session, operation="sales_letter_rewrite", units=40)  # вместе 101 — выбран
    usage.record(session, operation="sales_draft", units=500)  # агент продаж — не в этом счёте
    usage.record(session, operation="reply_parse", units=321)  # разбор доноров — тоже не в нём


async def test_spent_sales_cap_refuses_the_reply_kind_in_words_without_the_model(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _sales_cap_spent(monkeypatch, session)
    lead_reply = await _sales_reply(session, "Сколько стоит аудит?")
    kinds = FakeClassifier()

    with pytest.raises(LlmCapExceededError) as refused:
        await SalesReplies(session, kinds).handle(lead_reply.id)

    assert kinds.calls == 0  # модель вида ответа не звали
    # Разбор доноров и черновик агента — не в счёте продаж: только 61 + 40.
    assert str(refused.value) == REFUSED.format(spent=101, cap=83)


async def test_spent_sales_cap_lets_the_donor_price_parse_go(
    session: AsyncSession, reply: ReplyModel, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _sales_cap_spent(monkeypatch, session)
    donor_model = CountingExtractor()

    await Parser(session, donor_model, now=NOW).parse(reply.id)  # type: ignore[arg-type]

    assert donor_model.calls == 1  # разбор цены донора прошёл: общий потолок не выбран


@pytest.mark.parametrize(
    ("general", "own", "spent", "refused"),
    [
        (5_437, None, 1_087, True),  # не задан — 20 % общего: 1087 из 1087
        (5_437, None, 1_086, False),  # и разбор доноров (97) в счёт продаж не идёт
        (0, None, 98_765, False),  # общий «потолка нет», свой не задан — нет и у продаж
        (0, 83, 83, True),  # свой задан при общем 0 — действует свой
    ],
)
async def test_sales_cap_is_the_setting_or_a_share_of_the_general_one(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    general: int,
    own: int | None,
    spent: int,
    refused: bool,
) -> None:
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", general)
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", own)
    usage.record(session, operation="sales_letter_rewrite", units=spent)
    usage.record(session, operation="reply_parse", units=97)

    if refused:
        with pytest.raises(LlmCapExceededError, match="SALES_DAILY_TOKEN_CAP"):
            await usage.ensure_llm_within_cap(session, own=usage_cap.sales_cap())
    else:
        await usage.ensure_llm_within_cap(session, own=usage_cap.sales_cap())


async def test_the_queue_build_stops_in_words_of_the_sales_cap_and_spends_under_its_own_name(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сборка проверяет свой потолок продаж до каждого письма; расход переписывания —
    операцией `sales_letter_rewrite`, иначе он ушёл бы в письма доноров и мимо потолка."""
    world = await w.world(session, monkeypatch)
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 0)  # общего нет — держит свой
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 53)  # 37 токенов на письмо
    for name in ("one", "two", "three"):
        await w.lead(session, world.hypothesis_id, f"{name}@{name}.example.test")
    rewriter = w.CorridorRewriter()

    report = await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=10)

    assert (report.prepared, report.tokens_spent, len(rewriter.seen)) == (2, 74, 2)
    assert report.stopped == REFUSED.format(spent=74, cap=53)
    spent = await session.execute(
        select(UsageRecordModel.operation, func.sum(UsageRecordModel.units)).group_by(
            UsageRecordModel.operation
        )
    )
    assert dict(spent.tuples().all()) == {"sales_letter_rewrite": 74}


def test_the_sales_cap_counts_exactly_the_model_calls_of_sales_outside_the_agent() -> None:
    assert {"sales_reply_kind", "sales_letter_rewrite"} == usage_cap.OPERATIONS
    for operation in usage_cap.OPERATIONS:
        assert OPERATION_PROVIDERS[operation] is UsageProvider.LLM
    # Черновик, ситуацию и судью агента продаж держит потолок черновиков, а не этот.
    assert usage_cap.OPERATIONS.isdisjoint({"sales_draft", "sales_situation", "sales_judge"})
    assert usage_cap.OPERATIONS.isdisjoint(agent_operations())
