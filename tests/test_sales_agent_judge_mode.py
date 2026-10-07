"""Режим судьи продаж `SALES_JUDGE_MODE` — срез 3.4 (Spec 3.3, решение координатора 07.10).

`enforce` (по умолчанию) отдаёт шву вердикт как есть: нарушение возвращает черновик
писателю. `shadow` — для замера: `block` записан словами в попытки черновика и в
журнал, а черновик не задерживается. Проверяется то, чего не видно по зелёному
прогону: умолчание — именно `enforce`; в наблюдении судья, который не смог
проверить, всё равно отдаёт черновик человеку (отказ закрыт в обоих режимах);
черновик путём шва в наблюдении не уходит на правку, но вердикт в нём виден.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import pytest
from backend.config import sales as sales_cfg
from backend.config.sales import SalesJudgeMode
from backend.features.agent import drafting
from backend.features.agent.stages import Verdict, VerdictKind
from backend.features.core.domain import DraftStatus
from backend.features.sales.agent import judge
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_agent_judge import FINE, check
from tests.test_sales_agent_judge_rules import CLEAN
from tests.test_sales_agent_situation import Plug, llm
from tests.test_sales_agent_stage import (
    ALLOW,
    BAD,
    GOOD,
    INFORM,
    Writer,
    lead_replied,
    sales_on,
    stored,
)

__all__ = ["llm", "sales_on"]  # фикстуры — отсюда их видит pytest

#: Строка продаж в реестре этапов — как тумблером `SALES_AGENT_ENABLED`.
pytestmark = pytest.mark.usefixtures("sales_on")

SetMode = Callable[[SalesJudgeMode], None]
INVENTED = CLEAN.replace("в 3,7 раза", "в 5 раз")
WHY = "число 5 не из базы и не из письма собеседника — уберите его или возьмите из фактов"


@pytest.fixture
def mode(monkeypatch: pytest.MonkeyPatch) -> SetMode:
    def set_mode(value: SalesJudgeMode) -> None:
        monkeypatch.setattr(sales_cfg, "JUDGE_MODE", value)

    return set_mode


def test_enforce_is_the_default() -> None:
    field = sales_cfg._Sales.model_fields["judge_mode"]

    assert field.default is SalesJudgeMode.ENFORCE
    assert field.validation_alias == "SALES_JUDGE_MODE"


def test_unknown_mode_is_refused_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SALES_JUDGE_MODE", "off")

    refused = r"SALES_JUDGE_MODE\n  Input should be 'shadow' or 'enforce'"

    with pytest.raises(ValueError, match=refused):
        sales_cfg._Sales()


@pytest.mark.parametrize(
    ("found", "expected"),
    [
        (Verdict(VerdictKind.ALLOW, tokens=5), Verdict(VerdictKind.ALLOW, tokens=5)),
        (
            Verdict(VerdictKind.ESCALATE, ("судья-модель не проверила черновик",)),
            Verdict(VerdictKind.ESCALATE, ("судья-модель не проверила черновик",)),
        ),
        (
            Verdict(VerdictKind.BLOCK, ("тон: давит", WHY), tokens=9),
            Verdict(VerdictKind.ALLOW, ("shadow block: тон: давит", f"shadow block: {WHY}"), 9),
        ),
    ],
    ids=["allow", "escalate", "block"],
)
def test_shadow_lets_only_a_block_through(found: Verdict, expected: Verdict) -> None:
    assert judge.shadowed(found) == expected


async def test_enforce_returns_the_block_as_is(mode: SetMode, llm: Plug) -> None:
    mode(SalesJudgeMode.ENFORCE)
    model = llm(judge=[FINE])

    found = await judge.guard(check(INVENTED))

    assert found == Verdict(VerdictKind.BLOCK, (WHY,))
    assert model.sent["judge"] == []


async def test_shadow_writes_the_block_down_and_does_not_hold_the_draft(
    mode: SetMode, llm: Plug, caplog: pytest.LogCaptureFixture
) -> None:
    mode(SalesJudgeMode.SHADOW)
    llm(judge=[FINE])

    with caplog.at_level(logging.INFO, logger=judge.__name__):
        found = await judge.guard(check(INVENTED))

    assert found == Verdict(VerdictKind.ALLOW, (f"shadow block: {WHY}",))
    assert f"режим shadow — вернул бы черновик на правку: {WHY}" in caplog.text


async def test_shadow_still_hands_an_unchecked_draft_to_a_human(mode: SetMode, llm: Plug) -> None:
    """Отказ закрыт и в наблюдении: модель не ответила — человеку, а не `allow`."""
    mode(SalesJudgeMode.SHADOW)
    llm(503, judge=[FINE])

    found = await judge.guard(check())

    assert found.kind is VerdictKind.ESCALATE
    assert found.reasons[0].startswith("судья-модель не проверила черновик")


async def test_verdict_is_the_same_in_both_modes(mode: SetMode, llm: Plug) -> None:
    """eval меряет вердикт без режима: наблюдение не прячет от него `block`."""
    llm(judge=[FINE])
    mode(SalesJudgeMode.SHADOW)
    shadow = await judge.verdict(check(INVENTED))
    mode(SalesJudgeMode.ENFORCE)
    enforce = await judge.verdict(check(INVENTED))

    assert shadow == enforce == Verdict(VerdictKind.BLOCK, (WHY,))


# --- путём шва: черновик продаж на настоящей базе ----------------------------------------


async def test_shadow_draft_is_not_sent_back_but_carries_the_verdict(
    session: AsyncSession, llm: Plug, mode: SetMode
) -> None:
    mode(SalesJudgeMode.SHADOW)
    reply_id = await lead_replied(session)
    llm(situation=[INFORM], judge=[ALLOW])
    writer = Writer(BAD, GOOD)

    outcome = await drafting.draft_answer(session, writer, reply_id)

    assert outcome.status is DraftStatus.DRAFTED
    assert len(writer.seen) == 1  # на правку не ушёл
    draft = await stored(session, reply_id)
    assert draft.body == BAD
    [attempt] = draft.meta["attempts"]
    assert attempt["verdict"] == "allow"
    assert attempt["reasons"] == [
        "shadow block: сумма 500 не из базы и не из письма собеседника — уберите её или "
        "возьмите из фактов"
    ]
    assert draft.meta["judge_mode"] == "shadow"


async def test_enforce_draft_records_its_mode(
    session: AsyncSession, llm: Plug, mode: SetMode
) -> None:
    mode(SalesJudgeMode.ENFORCE)
    reply_id = await lead_replied(session)
    llm(situation=[INFORM], judge=[ALLOW])

    outcome = await drafting.draft_answer(session, Writer(BAD, GOOD), reply_id)

    assert outcome.status is DraftStatus.DRAFTED
    draft = await stored(session, reply_id)
    assert draft.body == GOOD
    assert [attempt["verdict"] for attempt in draft.meta["attempts"]] == ["block", "allow"]
    assert draft.meta["judge_mode"] == "enforce"
