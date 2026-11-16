"""План работ — в базу и из базы (`sales/plan_store.py`).

Проверяется то, ради чего модуль: лист становится гипотезой продаж по правилу заведения (журнал,
поле «страны компаний» — правкой), строкой плана и реестром ключей на каждом рынке; цель — своя или
общая; правила сохранения — отказ словами, все нарушения сразу и до первой записи: листы с ошибками,
нет цели, нет потолков, имя существующей гипотезы или рассылки; чтение — по порядку листов с числами
реестра; план держит свои гипотезы от удаления; отпечаток меняется от того, что видел человек.
Книги — из тестов разбора (`test_sales_plan_file`), данные выдуманные.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

import pytest
from backend.features.core.domain import Stage
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.outreach import CampaignModel
from backend.features.sales import hypotheses, hypothesis_holds, plan_store
from backend.features.sales.models import SalesChainTemplateModel, SalesHypothesisModel
from backend.features.sales.plan_estimate import PlanEstimate, Spend
from backend.features.sales.plan_file import PlanDraft, read_plan
from backend.features.sales.plan_models import (
    PlanMode,
    PlanStage,
    SalesPlanHypothesisModel,
    SalesPlanKeywordModel,
    SalesPlanModel,
)
from backend.features.sales.plan_store import PlanAsk, PlanRefusedError, plan_digest, save_plan
from backend.features.serp.modes import SerpMode
from backend.features.serp.protocol import keyword_key
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_plan_file import KEYS, book, changed, rows

ASK = PlanAsk(
    name="  План   недели ",
    mode=PlanMode.ECONOMY,
    goal=None,
    cap_usd=Decimal("50.00"),
    hypothesis_cap_usd=Decimal("10.00"),
)
ESTIMATE = PlanEstimate("standard", (), Spend(queries=3), Spend(queries=3, serp_usd=0.0105))
SECOND = changed(
    Название="Курьеры — пример",
    Где_ищем="Казахстан, Узбекистан",
    **{"Компании из других стран": "на проверку", "Не пишем компаниям из": ""},
    Найти_сайтов="",
    Глубина_поиска_до="топ-20",
)


def draft(*sheets: tuple[str, dict[str, object], tuple[str, ...]]) -> PlanDraft:
    return read_plan(book(*((title, rows(card, keys)) for title, card, keys in sheets)), "p.xlsx")


def two() -> PlanDraft:
    return draft(("Пилот 1", changed(), KEYS), ("Пилот 2", SECOND, KEYS[:2]))


async def count(session: AsyncSession, model: type[object]) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_sheets_become_hypotheses_plan_rows_and_a_registry_of_keys(
    session: AsyncSession,
) -> None:
    plan = await save_plan(
        session, two(), replace(ASK, goal=300), ESTIMATE, author="owner@example.test"
    )

    assert (plan.name, plan.mode, plan.target_default, plan.cap_usd, plan.status) == (
        "План недели",
        "economy",
        300,
        Decimal("50.00"),
        "running",
    )
    assert plan.digest == plan_digest(two(), replace(ASK, goal=300))
    assert plan.estimate["high"]["serp_usd"] == pytest.approx(0.0105)
    found = list(
        await session.scalars(
            select(SalesPlanHypothesisModel).order_by(SalesPlanHypothesisModel.position)
        )
    )
    assert [
        (row.position, row.sheet, row.markets, row.language, row.target, row.max_pages)
        for row in found
    ] == [
        (1, "Пилот 1", ["kz"], "ru", 5000, 5),
        (2, "Пилот 2", ["kz", "uz"], "ru", 300, 2),
    ]
    first = found[0]
    assert (first.cap_usd, first.stage, first.wave_page, first.problems, first.found) == (
        Decimal("10.00"),
        PlanStage.WAITING,
        1,
        [],
        0,
    )
    made = await session.get(SalesHypothesisModel, first.hypothesis_id)
    assert made is not None
    assert (made.name, made.other_countries, made.excluded_countries) == (
        "Логистика — пример",
        "accept",
        ["UA"],
    )
    assert made.description == "Транспортные компании со своим сайтом; не каталоги и не СМИ."


async def test_the_registry_has_every_key_on_every_market_in_sheet_order(
    session: AsyncSession,
) -> None:
    await save_plan(session, two(), replace(ASK, goal=300), ESTIMATE)

    keys = list(
        await session.execute(
            select(
                SalesPlanKeywordModel.market,
                SalesPlanKeywordModel.position,
                SalesPlanKeywordModel.keyword,
                SalesPlanKeywordModel.keyword_key,
                SalesPlanKeywordModel.pages,
                SalesPlanKeywordModel.run_id,
            )
            .join(SalesPlanHypothesisModel)
            .where(SalesPlanHypothesisModel.position == 2)
            .order_by(SalesPlanKeywordModel.market, SalesPlanKeywordModel.position)
        )
    )
    assert [tuple(key) for key in keys] == [
        (market, position, phrase, keyword_key(phrase), 0, None)
        for market in ("kz", "uz")
        for position, phrase in enumerate(KEYS[:2], 1)
    ]
    assert await count(session, SalesPlanKeywordModel) == len(KEYS) + 2 * 2


async def test_a_sheet_of_many_keys_goes_in_chunks(session: AsyncSession) -> None:
    many = tuple(f"доставка грузов {n:05d}" for n in range(plan_store.CHUNK * 2 + 7))

    await save_plan(session, draft(("Пилот", changed(), many)), ASK, ESTIMATE)

    assert await count(session, SalesPlanKeywordModel) == len(many)


async def test_the_plan_and_the_hypotheses_are_in_the_journal(session: AsyncSession) -> None:
    plan = await save_plan(session, two(), replace(ASK, goal=300), ESTIMATE)

    said = [
        (target, (details or {}).get("действие"))
        for target, details in await session.execute(
            select(AuditLogModel.target, AuditLogModel.details).order_by(AuditLogModel.id)
        )
    ]
    first, second = (
        await session.scalars(
            select(SalesPlanHypothesisModel.hypothesis_id).order_by(
                SalesPlanHypothesisModel.position
            )
        )
    ).all()
    assert said == [
        (f"sales_hypothesis:{first}", hypotheses.MADE),
        (f"sales_hypothesis:{first}", hypotheses.COUNTRIES),
        (f"sales_hypothesis:{second}", hypotheses.MADE),
        (f"sales_plan:{plan.id}", plan_store.MADE),
    ]


async def test_every_rule_is_named_at_once_and_nothing_is_written(session: AsyncSession) -> None:
    await hypotheses.add(session, "Курьеры — пример", None)
    bad = changed(Название="С ошибкой", Язык_писем="немецкий")
    aimless = changed(Название="Курьеры — пример", Найти_сайтов=None)
    plan = draft(("Пилот", changed(), KEYS), ("Ошибка", bad, KEYS), ("Без цели", aimless, KEYS))

    with pytest.raises(PlanRefusedError) as refused:
        await save_plan(
            session, plan, replace(ASK, cap_usd=Decimal(0), hypothesis_cap_usd=Decimal(0)), ESTIMATE
        )

    words = str(refused.value)
    assert words.startswith("план не заведён: листы с ошибками: «Ошибка» — поправьте книгу")
    for said in (
        "у листов «Без цели» нет цели «Найти сайтов», и общая цель не задана",
        "потолок плана обязателен",
        "потолок гипотезы обязателен",
        "лист «Без цели»: имя «Курьеры — пример» уже у гипотезы №",
        "— переименуйте лист",
    ):
        assert said in words
    assert (await count(session, SalesPlanModel), await count(session, SalesHypothesisModel)) == (
        0,
        1,
    )


async def test_a_sales_campaign_of_the_same_name_refuses_the_sheet(session: AsyncSession) -> None:
    session.add(CampaignModel(name="Продажи: Логистика — пример", stage=Stage.SALES))
    await session.flush()

    with pytest.raises(PlanRefusedError, match="рассылка «Продажи: Логистика — пример» уже есть"):
        await save_plan(session, draft(("Пилот", changed(), KEYS)), ASK, ESTIMATE)


async def test_plans_read_back_newest_first_with_numbers_of_the_registry(
    session: AsyncSession,
) -> None:
    older = await save_plan(session, two(), replace(ASK, goal=300), ESTIMATE)
    newer = await save_plan(
        session, draft(("Лист", changed(Название="Третий"), KEYS[:1])), ASK, ESTIMATE
    )
    first_row = await session.scalar(
        select(SalesPlanHypothesisModel.id).where(SalesPlanHypothesisModel.position == 1)
    )
    await session.execute(
        update(SalesPlanKeywordModel)
        .where(
            SalesPlanKeywordModel.plan_hypothesis_id == first_row,
            SalesPlanKeywordModel.position == 1,
        )
        .values(pages=1)
    )

    listed = await plan_store.plans(session)
    one = await plan_store.plan(session, older.id)

    assert [view.plan.id for view in listed] == [newer.id, older.id]
    assert one is not None
    assert [(row.name, row.row.sheet, row.keywords, row.bought) for row in one.hypotheses] == [
        ("Логистика — пример", "Пилот 1", 3, 1),
        ("Курьеры — пример", "Пилот 2", 4, 0),
    ]
    assert await plan_store.plan(session, newer.id + 1) is None
    assert await plan_store.plans(session, limit=1) == listed[:1]


async def test_the_plan_holds_its_hypotheses_from_deletion(session: AsyncSession) -> None:
    await save_plan(session, draft(("Пилот", changed(), KEYS)), ASK, ESTIMATE)
    hypothesis_id = await session.scalar(select(SalesPlanHypothesisModel.hypothesis_id))
    assert hypothesis_id is not None

    found = await hypothesis_holds.count(session, hypothesis_id)

    assert found == {"plans": 1}
    assert hypothesis_holds.words(found) == "план работ — 1"


def test_the_digest_follows_what_the_human_saw_but_not_the_plan_name() -> None:
    plan = two()
    base = plan_digest(plan, ASK)

    assert plan_digest(two(), replace(ASK, name="другое имя")) == base
    for changed_ask in (
        replace(ASK, mode=PlanMode.FAST),
        replace(ASK, goal=10),
        replace(ASK, cap_usd=Decimal("51")),
        replace(ASK, hypothesis_cap_usd=Decimal("11")),
    ):
        assert plan_digest(plan, changed_ask) != base
    other_keys = draft(("Пилот 1", changed(), KEYS[:2]), ("Пилот 2", SECOND, KEYS[:2]))
    other_card = draft(("Пилот 1", changed(Найти_сайтов=10), KEYS), ("Пилот 2", SECOND, KEYS[:2]))
    assert plan_digest(other_keys, ASK) != base
    assert plan_digest(other_card, ASK) != base
    assert len(base) == 16


def test_plan_modes_buy_the_serp_in_their_mode() -> None:
    assert plan_store.SERP_MODES == {
        PlanMode.ECONOMY: SerpMode.STANDARD,
        PlanMode.FAST: SerpMode.LIVE,
    }


async def test_a_sheet_whose_letters_have_no_chain_is_named_for_the_preview(
    session: AsyncSession,
) -> None:
    english = changed(Название="Английский лист", Язык_писем="английский")
    plan = draft(("Пилот", changed(), KEYS), ("English", english, KEYS[:1]))
    for step in (1, 2, 3):
        session.add(
            SalesChainTemplateModel(
                hypothesis_id=None,
                step=step,
                language="ru",
                subject="Тема" if step == 1 else None,
                body="[offer] fixed\nВыдуманный текст шага: ничего настоящего.",
                active=True,
            )
        )
    await session.flush()

    gaps = await plan_store.chain_gaps(session, plan)

    assert gaps == {
        "English": "письма не соберутся: в общей цепочке на английском нет первого письма, первой "
        "добивки, второй добивки — задайте цепочку до первых писем гипотезы"
    }
