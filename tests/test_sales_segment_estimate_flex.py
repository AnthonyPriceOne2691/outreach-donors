"""Тариф судьи в смете прогона и смете плана (`segment_tier`): экономный — OpenAI Flex.

- Без тарифа смета прежняя до байта: числа и слова те же, в смете прогона (`as_dict`) ключа тарифа
  нет, отпечаток — тем же правилом, что до тарифа.
- Flex: сайты и токены те же, деньги токенов вдвое ниже, цены в смете — прайса (долю применяет тот,
  кто считает деньги: смета — деньгам токенов, отчёт прогона — факту, и вдвое скидка не ложится);
  строка судьи — «Судья сегмента — экономный тариф, медленнее»; смета прогона несёт тариф — задача
  прогона читает его оттуда; отпечаток другой.
- Незнакомый тариф — отказ словами до денег.
- Смета плана — с тарифом и без: тот же расчёт сметы прогона.

Настройки — как у тестов сметы (`test_sales_segment_estimate._settings`); числа выдуманы.
"""

from __future__ import annotations

import hashlib
import json

import pytest
from backend.features.sales import segment_tier
from backend.features.sales.plan_estimate import plan_estimate
from backend.features.sales.segment_estimate import Estimate, estimate
from backend.features.sales.segment_estimate_keys import DIGEST_LENGTH
from backend.features.sales.segment_judge import segment_digest
from backend.features.sales.segment_spend import money, spend_rows
from backend.features.sales.segment_tier import JudgeTier
from backend.features.serp.modes import SerpMode
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_plan_estimate import KEYS, draft
from tests.test_sales_plan_file import changed
from tests.test_sales_segment_estimate import _settings, ask
from tests.test_sales_segment_run import hypothesis
from tests.test_sales_segment_spend import REPORT, run_of

__all__ = ["_settings"]  # настройки сметы — те же, что у её тестов (фикстура видна отсюда)

JUDGE = "Судья сегмента: "
FLEX = "Судья сегмента — экономный тариф, медленнее: "


def judge_line(found: Estimate) -> str:
    [line] = [line for line in found.lines if line.startswith("Судья сегмента")]
    return line


async def test_without_a_tier_the_estimate_is_the_one_it_always_was(session: AsyncSession) -> None:
    """Тариф не назван или назван обычным — смета одна и та же; ключа тарифа в смете прогона нет, а
    отпечаток считается тем же правилом, что до тарифа: смета, посчитанная до выкатки, не устареет."""
    owner = await hypothesis(session)

    plain = await estimate(session, ask(owner.id))
    standard = await estimate(session, ask(owner.id, judge_tier="standard"))

    assert plain.refusal is None
    assert plain.as_dict() == standard.as_dict()
    assert "judge_tier" not in plain.as_dict()
    assert plain.judge_tier == "standard"
    seen = {
        "hypothesis": owner.id,
        "keywords": plain.keywords,
        "excluded": [],
        "segment": segment_digest(plain.segment),
        "mode": plain.mode,
        "serp_mode": plain.serp_mode,
        "country": plain.country,
        "language": plain.language,
        "depth_pages": plain.depth_pages,
    }
    text = json.dumps(seen, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert plain.digest == hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]
    assert judge_line(plain).startswith(JUDGE)


async def test_flex_halves_the_judge_money_and_keeps_the_list_prices(session: AsyncSession) -> None:
    """Те же сайты и токены, деньги токенов — половиной; цены в смете — прайса; строка судьи — та же,
    кроме тарифа после имени и денег; деньги вместе — с половиной модели."""
    owner = await hypothesis(session)

    standard = await estimate(session, ask(owner.id))
    flex = await estimate(session, ask(owner.id, judge_tier="flex"))

    assert flex.refusal is None
    assert (flex.sites, flex.tokens, flex.serp_usd) == (
        standard.sites,
        standard.tokens,
        standard.serp_usd,
    )
    half = standard.tokens * standard.usd_per_1m_tokens * segment_tier.FLEX_PRICE_SHARE / 1e6
    assert flex.tokens_usd == pytest.approx(round(half, 4))
    assert 0 < flex.tokens_usd < standard.tokens_usd
    prices = ("usd_per_1m_tokens", "usd_per_1m_input", "usd_per_1m_output")
    assert [getattr(flex, name) for name in prices] == [getattr(standard, name) for name in prices]
    saved = flex.as_dict()
    assert saved["judge_tier"] == "flex"
    assert segment_tier.tier_of(saved) is JudgeTier.FLEX  # задача прогона читает тариф отсюда
    assert judge_line(flex) == judge_line(standard).replace(JUDGE, FLEX, 1).replace(
        money(standard.tokens_usd), money(flex.tokens_usd)
    )
    assert flex.lines[-1] == standard.lines[-1].replace(
        money(standard.serp_usd + standard.tokens_usd), money(flex.serp_usd + flex.tokens_usd)
    )


async def test_the_digest_changes_with_the_tier(session: AsyncSession) -> None:
    """Тариф — то, что человек видел: смета обычным тарифом не запускает прогон экономным."""
    owner = await hypothesis(session)

    standard = await estimate(session, ask(owner.id))
    flex = await estimate(session, ask(owner.id, judge_tier="flex"))
    again = await estimate(session, ask(owner.id, judge_tier="flex"))

    assert standard.digest != flex.digest
    assert flex.digest == again.digest


async def test_an_unknown_tier_is_a_refusal_in_words_before_the_money(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)

    found = await estimate(session, ask(owner.id, judge_tier="turbo"))

    assert found.refusal == segment_tier.UNKNOWN.format(raw="turbo", known="standard, flex")
    assert (found.results, found.tokens_usd, found.lines) == (0, 0.0, [])


async def test_the_run_report_prices_a_flex_estimate_once_not_twice(session: AsyncSession) -> None:
    """Смета экономного прогона несёт цены прайса и тариф: факт судьи в отчёте — половиной прайса,
    а не четвертью (скидка сметы и скидка отчёта не складываются)."""
    owner = await hypothesis(session)
    flex = await estimate(session, ask(owner.id, judge_tier="flex"))
    judged = {"tokens": 13_000, "prompt_tokens": 10_000, "completion_tokens": 3_000}

    run = run_of(estimate=flex.as_dict(), report={**REPORT, "judge": judged})
    row = {row.item: row for row in spend_rows(run)}["judge"]

    assert (flex.usd_per_1m_input, flex.usd_per_1m_output) == (0.25, 2.0)
    assert row.fact_usd == round((10_000 * 0.25 + 3_000 * 2.0) * 0.5 / 1_000_000, 4)
    assert row.promised_usd == flex.tokens_usd


async def test_the_plan_estimate_takes_the_judge_tier(session: AsyncSession) -> None:
    """Тариф плана — необязательный: не назван — смета плана прежняя; Flex — те же сайты, токены и
    выдача, деньги модели вдвое ниже; вызов с целью и без тарифа — как раньше."""
    plan = draft(("Пилот", changed(), KEYS))

    plain = await plan_estimate(session, plan, SerpMode.STANDARD, goal=50)
    standard = await plan_estimate(
        session, plan, SerpMode.STANDARD, goal=50, judge_tier=JudgeTier.STANDARD
    )
    flex = await plan_estimate(session, plan, SerpMode.STANDARD, goal=50, judge_tier=JudgeTier.FLEX)

    assert plain == standard
    assert (plain.judge_tier, flex.judge_tier) == ("standard", "flex")
    for before, after in ((plain.low, flex.low), (plain.high, flex.high)):
        assert (after.sites, after.tokens, after.serp_usd) == (
            before.sites,
            before.tokens,
            before.serp_usd,
        )
        assert after.tokens_usd == pytest.approx(before.tokens_usd / 2, abs=1e-4)
        assert after.tokens_usd < before.tokens_usd
