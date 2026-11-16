"""Смета плана работ до запуска (`sales/plan_estimate.py`): по гипотезе и на план, диапазоном.

Главное — своего расчёта у плана нет: на тех же ключах, рынке, глубине и режиме выдачи числа те же,
что у сметы прогона сбора (`segment_estimate.estimate`), на обоих концах диапазона — топ-10 и
предельной глубине. Рынки гипотезы и гипотезы плана — суммой; листы с ошибками в смету не входят;
режим выдачи меняет цену выдачи и только её; цель, которой ключам не набрать, — словами. Настройки
сметы — те же, что у её тестов (`test_sales_segment_estimate._settings`).
"""

from __future__ import annotations

import pytest
from backend.config import contacts as contacts_cfg
from backend.config import sales as sales_cfg
from backend.features.runs.estimate import UNIQUE_SHARE
from backend.features.sales.plan_estimate import Spend, plan_estimate
from backend.features.sales.plan_file import PlanDraft, read_plan
from backend.features.sales.segment_estimate import TOKENS_PER_SITE, VERIFIER_NOT_SET, estimate
from backend.features.serp.modes import SerpMode, serp_cost_usd
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_plan_file import book, changed, rows
from tests.test_sales_segment_estimate import _settings, ask
from tests.test_sales_segment_judge import MODEL_KEY
from tests.test_sales_segment_run import hypothesis

__all__ = ["_settings"]  # настройки сметы — те же, что у её тестов (фикстура видна отсюда)

CITIES = ("алматы", "астана", "шымкент", "караганда", "актобе", "тараз", "павлодар", "костанай")
KEYS = tuple(f"доставка грузов {city}" for city in CITIES)


def draft(*sheets: tuple[str, dict[str, object], tuple[str, ...]]) -> PlanDraft:
    return read_plan(
        book(*((title, rows(card, keys)) for title, card, keys in sheets)), "plan.xlsx"
    )


async def test_both_ends_are_the_collection_run_estimate_with_the_same_numbers(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Платный поиск адреса и живая проверка включены — Hunter сверяется тоже."""
    monkeypatch.setattr(contacts_cfg, "HUNTER_API_KEY", MODEL_KEY)
    monkeypatch.setattr(sales_cfg, "SEGMENT_PAID_CONTACTS", True)
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "live")
    monkeypatch.setattr(sales_cfg, "VERIFIER_MONTH_CAP", "1500")
    plan = draft(("Пилот", changed(Глубина_поиска_до="топ-50"), KEYS))

    found = await plan_estimate(session, plan, SerpMode.STANDARD)

    [row] = found.hypotheses
    owner = await hypothesis(session)  # без прошлых прогонов: вычитать из сметы нечего
    for pages, spend in ((1, row.low), (5, row.high)):
        run = await estimate(
            session,
            ask(
                owner.id,
                keywords="\n".join(KEYS),
                country="kz",
                language="ru",
                depth_pages=pages,
                serp_mode="standard",
            ),
        )
        assert run.refusal is None
        assert spend == Spend(
            queries=len(run.keywords),
            results=run.results,
            sites=run.sites,
            serp_usd=run.serp_usd,
            tokens=run.tokens,
            tokens_usd=run.tokens_usd,
            hunter_searches=run.hunter_searches,
            hunter_checks=run.hunter_checks,
        )
    assert (row.low.results, row.high.results, row.high.hunter_checks) == (80, 400, 340)
    assert (found.low, found.high) == (row.low, row.high)
    assert (found.share, found.share_runs, found.tokens_per_site) == (
        UNIQUE_SHARE,
        0,
        TOKENS_PER_SITE,
    )
    assert found.notes == ()


async def test_markets_and_hypotheses_add_up_and_top_ten_is_one_pass(session: AsyncSession) -> None:
    plan = draft(
        ("Два рынка", changed(Где_ищем="Казахстан, Узбекистан", Глубина_поиска_до="топ-30"), KEYS),
        ("Только топ-10", changed(Название="Только топ-10", Глубина_поиска_до="топ-10"), KEYS[:4]),
    )

    found = await plan_estimate(session, plan, SerpMode.LIVE)

    two, top_ten = found.hypotheses
    assert (two.low.queries, two.low.results, two.high.results) == (16, 160, 480)
    assert two.high.serp_usd == pytest.approx(2 * serp_cost_usd(len(KEYS), 3, SerpMode.LIVE))
    assert (two.depth_pages, top_ten.depth_pages) == (3, 1)
    assert top_ten.low == top_ten.high
    assert (found.low, found.high) == (two.low + top_ten.low, two.high + top_ten.high)
    assert found.low.usd == pytest.approx(found.low.serp_usd + found.low.tokens_usd)
    assert found.serp_mode == "live"


async def test_the_serp_mode_changes_the_price_of_the_serp_only(session: AsyncSession) -> None:
    plan = draft(("Пилот", changed(), KEYS))

    cheap = await plan_estimate(session, plan, SerpMode.STANDARD)
    fast = await plan_estimate(session, plan, SerpMode.LIVE)

    assert cheap.high.serp_usd == pytest.approx(len(KEYS) * 5 * 0.0007)
    assert fast.high.serp_usd == pytest.approx(len(KEYS) * 5 * 0.0021)
    assert (cheap.high.sites, cheap.high.tokens, cheap.high.tokens_usd) == (
        fast.high.sites,
        fast.high.tokens,
        fast.high.tokens_usd,
    )


async def test_sheets_with_errors_stay_out_and_a_target_out_of_reach_is_named(
    session: AsyncSession,
) -> None:
    plan = draft(
        ("Своя цель", changed(), KEYS),
        ("Общая цель", changed(Название="Общая цель", Найти_сайтов=None), KEYS[:2]),
        ("Ошибка", changed(Название="Ошибка", Язык_писем="немецкий"), KEYS),
    )

    found = await plan_estimate(session, plan, SerpMode.STANDARD, goal=10)

    own, common = found.hypotheses
    assert [row.sheet for row in found.hypotheses] == ["Своя цель", "Общая цель"]
    # 8 ключей × 5 страниц × 10 результатов × 0,85 — 340 сайтов при цели 5 000.
    assert (own.target, own.high.sites) == (5000, 340)
    assert own.warning == (
        "ключей мало для цели: сайтов в выдаче даже до топ-50 — ≈ 340, а цель — 5 000 "
        "прошедших судью; добавьте ключей"
    )
    assert (common.target, common.warning) == (10, None)


async def test_no_ready_sheet_is_an_estimate_of_nothing(session: AsyncSession) -> None:
    plan = draft(("Ошибка", changed(Язык_писем="немецкий"), KEYS))

    found = await plan_estimate(session, plan, SerpMode.STANDARD)

    assert (found.hypotheses, found.low, found.high, found.share) == (
        (),
        Spend(),
        Spend(),
        UNIQUE_SHARE,
    )


async def test_a_settings_refusal_of_the_run_estimate_is_a_note_of_the_plan(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sales_cfg, "VERIFIER_PROVIDER", "нет такого")
    plan = draft(("Пилот", changed(), KEYS), ("Второй", changed(Название="Второй"), KEYS[:2]))

    found = await plan_estimate(session, plan, SerpMode.STANDARD)

    assert found.notes == (VERIFIER_NOT_SET,)
    assert found.high.serp_usd > 0
