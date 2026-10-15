"""Проверка цитаты судьи сегмента: типографика не в счёт, выдумка — по-прежнему «на проверку».

Тест прогона: три сайта ушли человеку с «модель не прочла, а сочинила», хотя цитата на
сайте была — сайт и модель ставили разные кавычки и пробелы. Сверка теперь поверх общего
`keywords/hygiene.normalize`: двойные кавычки, многоточие-пропуск, знаки по краям, мягкий
перенос и нулевой ширины, ё/е, `casefold`; слова — по порядку внутри ОДНОЙ строки. Ложные «цитаты
нет», записанные в кэш вердиктов прежней сверкой, кэш больше не отдаёт.

Сайты и описание выдуманы; модель — подставным транспортом.
"""

from __future__ import annotations

import re

import pytest
from backend.config.judge import JudgeMode
from backend.features.sales import segment_judge as sj
from backend.features.sales.segment_judge import SiteText
from backend.features.sales.segment_judging import judge_run
from backend.features.sales.segment_models import (
    Decider,
    Lane,
    SalesSegmentVerdictModel,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_judge import judged
from tests.test_sales_segment_judging import candidates, judge
from tests.test_sales_segment_run import SEGMENT, SHOP, Model, hypothesis, make_run

TITLE = "ООО «Образец Мебель» — мебель на заказ по России и СНГ, шоурум в Москве"
MENU = "Каталог · Доставка и сборка · Контакты"


def site(title: str = TITLE) -> SiteText:
    return SiteText(((sj.PAGE_TITLE, title), (sj.HOME_MENU, MENU)))


# --- типографика не в счёт -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("line", "quote"),
    [
        (TITLE, 'ООО "Образец Мебель" — мебель на заказ'),
        (TITLE, "ООО “Образец Мебель” — мебель на заказ"),
        (TITLE, "„Образец Мебель“ — мебель на заказ по России"),
        (TITLE, "ООО Образец Мебель мебель на заказ"),
        (TITLE, "мебель на заказ по России … шоурум в Москве"),
        (TITLE, "мебель на заказ по России... шоурум в Москве"),
        (TITLE, "мебель на заказ по России и СНГ."),
        (TITLE, "«мебель на заказ по России и СНГ»,"),
        ("Вело\u00adсипеды и запчасти для горных трасс", "Велосипеды и запчасти для"),
        ("Вело\u200bсипеды и запчасти для горных трасс", "Велосипеды и запчасти для"),
        ("Ёлки, шишки и венки к празднику", "елки, шишки и венки к празднику"),
        ("Елки, шишки и венки к празднику", "Ёлки, шишки и венки"),
        ("GROSSE STRASSE Bäckerei Berlin", "Große Straße Bäckerei"),
        ("東京“家具”工房のサイトです", "東京家具工房のサイト"),
        ("東京“家具”工房のサイトです", "「東京家具工房のサイト」。"),
    ],
    ids=[
        "straight-quotes",
        "curly-quotes",
        "low-quotes",
        "no-quotes",
        "ellipsis-gap",
        "three-dots-gap",
        "trailing-dot",
        "quoted-with-comma",
        "soft-hyphen",
        "zero-width",
        "yo-in-site",
        "yo-in-quote",
        "casefold",
        "cjk",
        "cjk-closing-mark",
    ],
)
def test_an_honest_quote_holds_whatever_the_typography(line: str, quote: str) -> None:
    assert sj.quote_holds(quote, line)


async def test_a_typographic_quote_of_the_model_is_accepted_not_sent_to_review() -> None:
    """Как на тесте прогона: цитата есть на сайте, кавычки — другие."""
    verdict, _ = await judged(
        {"fit": "yes", "quote": 'ООО "Образец Мебель" — мебель на заказ', "why": "мастерская"},
        site(),
    )

    assert (verdict.lane, verdict.quote) == (Lane.ACCEPT, 'ООО "Образец Мебель" — мебель на заказ')


# --- выдумка — по-прежнему человеку ---------------------------------------------------------------


@pytest.mark.parametrize(
    "quote",
    [
        "лучшая мебельная фабрика Европы с 1990 года",
        # Куски из двух строк — не цитата, и через многоточие тоже.
        "шоурум в Москве Каталог Доставка и сборка",
        "шоурум в Москве … Доставка и сборка",
        # Куски одной строки, но не по порядку.
        "шоурум в Москве … мебель на заказ",
        # Слово переделано: «мебели» — не «мебель».
        "мебели на заказ по России",
    ],
    ids=["made-up", "two-lines", "two-lines-gap", "out-of-order", "changed-word"],
)
async def test_a_made_up_quote_still_goes_to_review(quote: str) -> None:
    verdict, _ = await judged({"fit": "yes", "quote": quote, "why": "мастерская"}, site())

    assert (verdict.lane, verdict.reason, verdict.quote) == (Lane.REVIEW, sj.MADE_UP, None)


@pytest.mark.parametrize(
    ("quote", "lane"),
    [
        ("Образец Мебель", Lane.REVIEW),
        ("«Образец» — Мебель", Lane.REVIEW),
        ("ООО Образец Мебель", Lane.ACCEPT),
    ],
    ids=["two-words", "two-words-and-signs", "three-words"],
)
async def test_the_quote_is_at_least_three_words_as_the_prompt_asks(quote: str, lane: Lane) -> None:
    verdict, _ = await judged({"fit": "yes", "quote": quote, "why": "мастерская"}, site())

    assert verdict.lane is lane
    if lane is Lane.REVIEW:
        assert verdict.reason == sj.SHORT_QUOTE


def test_the_minimum_is_the_lower_bound_the_prompt_names() -> None:
    """Согласовано с промптом: он просит «3 to 30 words», код не берёт короче."""
    bounds = re.search(r"(\d+) to (\d+) words", sj.load_prompt())

    assert bounds is not None
    assert int(bounds.group(1)) == sj.MIN_QUOTE_WORDS == 3


def test_a_word_of_a_script_without_spaces_counts_by_its_length() -> None:
    """Японская цитата без пробелов — не «одно слово»: иначе любой японский сайт уходил бы
    человеку с «цитата короче трёх слов»."""
    assert sj.quote_words("東京家具工房のサイト") >= sj.MIN_QUOTE_WORDS
    assert sj.quote_words("家具") < sj.MIN_QUOTE_WORDS
    assert sj.quote_words("мебель — на заказ …") == 3


# --- кэш: ложные «цитаты нет» прежней сверки не возвращаются --------------------------------------


async def _legacy_row(session: AsyncSession, domain_id: int, owner_id: int, **row: object) -> None:
    """Строка кэша, как её писала прежняя сверка: «на проверку», цитаты нет."""
    fields: dict[str, object] = {
        "verdict": Lane.REVIEW,
        "quote": None,
        "reason": sj.MADE_UP,
        "model": "gpt-5-mini",
        "decided_by": Decider.MODEL,
        "tokens": 587,
    } | row
    session.add(
        SalesSegmentVerdictModel(
            domain_id=domain_id,
            hypothesis_id=owner_id,
            prompt_version=sj.PROMPT_VERSION,
            segment_digest=sj.segment_digest(SEGMENT),
            **fields,
        )
    )
    await session.commit()


async def test_an_old_false_no_quote_from_the_cache_does_not_come_back(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    await _legacy_row(session, shop.domain_id, owner.id)
    model = Model(
        {SHOP: {"fit": "yes", "quote": "интернет-магазин «Велошоп»", "why": "магазин велосипедов"}}
    )

    tally = await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert model.asked == [SHOP]
    assert (tally.from_cache, tally.judged) == (0, 1)
    assert (shop.lane, shop.quote) == (Lane.ACCEPT, "интернет-магазин «Велошоп»")
    row = await session.scalar(select(SalesSegmentVerdictModel))
    assert row is not None
    assert (row.verdict, row.quote) == (Lane.ACCEPT, "интернет-магазин «Велошоп»")


async def test_a_cached_verdict_with_a_short_quote_is_asked_again(session: AsyncSession) -> None:
    """Вердикт с цитатой в два слова прежнее правило принимало; нынешнее — нет: кэш его не отдаёт."""
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    await _legacy_row(
        session, shop.domain_id, owner.id, verdict=Lane.ACCEPT, quote="Горные велосипеды"
    )
    model = Model({SHOP: {"fit": "yes", "quote": "интернет-магазин Велошоп", "why": "магазин"}})

    await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert model.asked == [SHOP]


async def test_a_rule_verdict_from_the_cache_is_still_trusted(session: AsyncSession) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    await _legacy_row(
        session,
        shop.domain_id,
        owner.id,
        verdict=Lane.REVIEW,
        reason="выдача и главная не дали текста — судить не по чему",
        decided_by=Decider.RULE,
        model=None,
        tokens=0,
    )
    model = Model({})

    tally = await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert model.asked == []
    assert tally.from_cache == 1


async def test_an_unproven_answer_is_not_written_to_the_cache(session: AsyncSession) -> None:
    """Сочинённая цитата — «на проверку» человеку, но не вердикт: в кэш она не ложится, и
    следующая правка сверки не оставит за собой ложных «цитаты нет»."""
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    [shop] = await candidates(session, run.id, owner.id, SHOP)
    model = Model({SHOP: {"fit": "yes", "quote": "лучшие велосипеды Европы", "why": "магазин"}})

    await judge_run(
        session,
        run_id=run.id,
        segment=SEGMENT,
        mode=JudgeMode.ENFORCE,
        client=judge(model),
        home=None,
    )

    assert (shop.lane, shop.reason) == (Lane.REVIEW, sj.MADE_UP)
    assert await session.scalar(select(SalesSegmentVerdictModel)) is None
