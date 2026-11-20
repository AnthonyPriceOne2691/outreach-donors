"""Экономный режим плана — судья на Flex (`plan_store.PLAN_MODES`): одно соответствие «режим плана →
выдача и тариф судьи» у сметы превью и сохранения и у отпечатка превью.

- economy — выдача «стандарт» и тариф Flex: токены те же, деньги судьи вдвое ниже; тариф — в ответе
  превью и в сохранённой смете плана; отпечаток называет тариф;
- fast — синхронная выдача и обычный тариф: смета и отпечаток — как до тарифа.

Книги — тестов разбора, маршруты — приложение в памяти, настройки сметы — её тестов; числа выдуманы.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace

import pytest
from backend.api.sales.plan_schemas import SpendView
from backend.features.sales import plan_store
from backend.features.sales.plan_estimate import plan_estimate
from backend.features.sales.plan_file import PlanDraft, read_plan
from backend.features.sales.plan_models import DIGEST_LENGTH, PlanMode, SalesPlanModel
from backend.features.sales.plan_store import PlanAsk, plan_digest
from backend.features.sales.segment_tier import JudgeTier
from backend.features.serp.modes import SerpMode
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_api_sales_plans import FORM, URL, headers, post, workbook
from tests.test_sales_plan_store import ASK
from tests.test_sales_segment_estimate import _settings

#: Фикстуры: настройки сметы — её тестов, вход продавца — тестов маршрутов плана.
__all__ = ["_settings", "headers"]


def test_one_place_maps_the_plan_mode_to_the_serp_and_the_judge_tier() -> None:
    assert {
        PlanMode.ECONOMY: plan_store.Pace(SerpMode.STANDARD, JudgeTier.FLEX),
        PlanMode.FAST: plan_store.Pace(SerpMode.LIVE, JudgeTier.STANDARD),
    } == plan_store.PLAN_MODES
    assert plan_store.SERP_MODES == {
        PlanMode.ECONOMY: SerpMode.STANDARD,
        PlanMode.FAST: SerpMode.LIVE,
    }


def digest_before_the_tier(draft: PlanDraft, ask: PlanAsk) -> str:
    """Отпечаток превью правилом до тарифа судьи."""
    seen = {
        "sheets": [
            {
                "sheet": found.sheet,
                "card": asdict(found.card),
                "keys": hashlib.sha256("\n".join(found.keys.taken).encode()).hexdigest(),
            }
            for found in draft.hypotheses
        ],
        "mode": ask.mode.value,
        "goal": ask.goal,
        "caps": [str(ask.cap_usd), str(ask.hypothesis_cap_usd)],
    }
    text = json.dumps(seen, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:DIGEST_LENGTH]


def test_the_economy_digest_names_the_tier_and_the_fast_one_is_as_before() -> None:
    """Превью экономного плана до тарифа показывало деньги судьи по прайсу — его отпечаток не
    сохранит план с половиной; быстрый план — отпечаток прежний до байта."""
    plan = read_plan(workbook(), "plan.xlsx")
    economy, fast = replace(ASK, mode=PlanMode.ECONOMY), replace(ASK, mode=PlanMode.FAST)

    assert plan_digest(plan, fast) == digest_before_the_tier(plan, fast)
    assert plan_digest(plan, economy) != digest_before_the_tier(plan, economy)
    assert plan_digest(plan, economy) != plan_digest(plan, fast)


async def test_the_economy_preview_halves_the_judge_money_and_names_the_tier(
    client: AsyncClient, headers: dict[str, str]
) -> None:
    economy = (await post(client, f"{URL}/preview", headers, FORM)).json()
    fast = (await post(client, f"{URL}/preview", headers, {**FORM, "mode": "fast"})).json()

    assert (economy["serp_mode"], economy["judge_tier"]) == ("standard", "flex")
    assert (fast["serp_mode"], fast["judge_tier"]) == ("live", "standard")
    for end in ("low", "high"):
        assert (economy[end]["sites"], economy[end]["tokens"]) == (
            fast[end]["sites"],
            fast[end]["tokens"],
        )
        # Деньги каждой из двух гипотез плана округлены до четырёх знаков, потом сложены: половина
        # суммы и сумма половин расходятся до 0,75·10⁻⁴ на гипотезу.
        assert economy[end]["tokens_usd"] == pytest.approx(fast[end]["tokens_usd"] / 2, abs=2e-4)
        assert 0 < economy[end]["tokens_usd"] < fast[end]["tokens_usd"]


async def test_the_fast_preview_is_the_estimate_it_always_was(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Быстрый план: смета превью — смета плана синхронной выдачей без тарифа, как до правки."""
    fast = (await post(client, f"{URL}/preview", headers, {**FORM, "mode": "fast"})).json()

    before = await plan_estimate(
        session, read_plan(workbook(), "plan.xlsx"), SerpMode.LIVE, goal=300
    )

    assert fast["low"] == SpendView.of(before.low).model_dump(mode="json")
    assert fast["high"] == SpendView.of(before.high).model_dump(mode="json")


async def test_the_saved_economy_plan_remembers_the_judge_tier(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    seen = (await post(client, f"{URL}/preview", headers, FORM)).json()

    saved = await post(client, URL, headers, {**FORM, "digest": seen["digest"]})

    assert saved.status_code == 201, saved.text
    stored = await session.scalar(select(SalesPlanModel.estimate))
    assert stored is not None
    assert (stored["serp_mode"], stored["judge_tier"]) == ("standard", "flex")
    assert stored["high"]["tokens_usd"] == seen["high"]["tokens_usd"]
