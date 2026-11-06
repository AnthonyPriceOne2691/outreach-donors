"""Совет судьи рекламодателей: таблица правил по фактам и сверка с общим ответом модели."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from backend.features.crawl.judge_facts import LinkFacts
from backend.features.crawl.judge_rules import Advice, advise
from tests.test_advertiser_judge import LINK_QUOTE, TARGET_QUOTE


def _facts(*on: str, link: bool = True, target: bool = True, disclosure: str = "") -> LinkFacts:
    """Факты для таблицы: названные признаки «да», цитаты своей стороны — есть или нет."""
    flags: dict[str, Any] = dict.fromkeys(on, True)
    return LinkFacts(
        **flags,
        disclosure=disclosure,
        link_quote=LINK_QUOTE if link else "",
        target_quote=TARGET_QUOTE if target else "",
    )


@pytest.mark.parametrize(
    ("facts", "advice", "rule"),
    [
        (
            _facts("money_anchor", "target_sells", link=False, target=False),
            Advice.UNCLEAR,
            "unproven",
        ),
        (_facts("money_anchor", "target_redirect"), Advice.SKIP, "redirect"),
        (
            _facts("money_anchor", "calls_to_buy", "target_noncommercial", "target_sells"),
            Advice.SKIP,
            "noncommercial",
        ),
        (_facts("press_release", "brand_anchor", "target_sells"), Advice.WRITE, "press_release"),
        (
            _facts("site_navigation", "target_sells", disclosure="Anzeige"),
            Advice.SKIP,
            "navigation",
        ),
        (
            _facts("site_navigation", "keyword_anchor", "target_refers", disclosure="Werbung"),
            Advice.WRITE,
            "paid_or_ad",
        ),
        (
            _facts("directory_entry", "recommends", "target_sells"),
            Advice.UNCLEAR,
            "ranked_directory",
        ),
        (_facts("directory_entry", "visit_button", "target_sells"), Advice.SKIP, "free_directory"),
        (_facts("directory_entry", "money_anchor", "target_sells"), Advice.WRITE, "promoted"),
        (_facts("source_of_fact", "brand_anchor", "target_media"), Advice.SKIP, "citation"),
        (_facts("source_of_fact", "money_anchor", "target_refers"), Advice.WRITE, "promoted"),
        (_facts("ad_slot", "target_sells", "sponsored"), Advice.WRITE, "paid_or_ad"),
        (_facts("brand_anchor", "target_sells", disclosure="Реклама"), Advice.WRITE, "paid_or_ad"),
        (_facts("keyword_anchor", "target_sells"), Advice.WRITE, "promoted"),
        (_facts("brand_anchor", "named_as_example", "target_sells"), Advice.WRITE, "promoted"),
        (_facts("brand_anchor", "off_topic", "target_affiliate_signs"), Advice.WRITE, "promoted"),
        (_facts("money_anchor", target=False), Advice.WRITE, "selling_anchor"),
        (
            _facts("brand_anchor", "article_about_target", "target_sells"),
            Advice.WRITE,
            "about_business",
        ),
        (_facts("brand_anchor", "target_media"), Advice.SKIP, "media"),
        (_facts("brand_anchor", "target_sells"), Advice.UNCLEAR, "brand_mention"),
        (_facts("target_sells"), Advice.SKIP, "no_signs"),
        (_facts("target_sells", "sponsored"), Advice.UNCLEAR, "rest"),
        (_facts("money_anchor", "target_media"), Advice.UNCLEAR, "rest"),
    ],
)
def test_the_first_rule_that_fits_gives_the_advice(
    facts: LinkFacts, advice: Advice, rule: str
) -> None:
    ruled = advise(facts)

    assert (ruled.advice, ruled.rule) == (advice, rule)
    assert ruled.words


@pytest.mark.parametrize(
    ("on", "verdict", "advice", "rule"),
    [
        (("keyword_anchor", "target_sells"), "write", Advice.WRITE, "promoted"),
        (("keyword_anchor", "target_sells"), "unclear", Advice.WRITE, "promoted"),
        (("keyword_anchor", "target_sells"), "", Advice.WRITE, "promoted"),
        (("keyword_anchor", "target_sells"), "skip", Advice.UNCLEAR, "disagree"),
        (("target_sells",), "skip", Advice.SKIP, "no_signs"),
        (("target_sells",), "unclear", Advice.SKIP, "no_signs"),
        (("target_sells",), "write", Advice.UNCLEAR, "disagree"),
        (("brand_anchor", "target_sells"), "write", Advice.WRITE, "verdict"),
        (("brand_anchor", "target_sells"), "skip", Advice.UNCLEAR, "brand_mention"),
        (("brand_anchor", "target_sells"), "unclear", Advice.UNCLEAR, "brand_mention"),
        (("brand_anchor", "target_sells"), "", Advice.UNCLEAR, "brand_mention"),
        (("money_anchor", "target_media"), "skip", Advice.UNCLEAR, "rest"),
        (("money_anchor", "target_media"), "write", Advice.WRITE, "verdict"),
    ],
)
def test_the_models_answer_is_checked_against_the_table(
    on: tuple[str, ...], verdict: str, advice: Advice, rule: str
) -> None:
    """Таблица не решила: «пишем» модели — совет, её «не пишем» — человеку; разошлись — «не понять»."""
    facts = dataclasses.replace(_facts(*on), verdict=verdict)

    ruled = advise(facts)

    assert (ruled.advice, ruled.rule) == (advice, rule)
    assert ruled.words


@pytest.mark.parametrize("verdict", ["write", "skip"])
@pytest.mark.parametrize(
    ("facts", "advice", "rule"),
    [
        (_facts("directory_entry", "visit_button", "target_sells"), Advice.SKIP, "free_directory"),
        (
            _facts("directory_entry", "recommends", "target_sells"),
            Advice.UNCLEAR,
            "ranked_directory",
        ),
        (_facts("money_anchor", link=False, target=False), Advice.UNCLEAR, "unproven"),
    ],
)
def test_the_models_answer_does_not_override_a_firm_rule(
    facts: LinkFacts, advice: Advice, rule: str, verdict: str
) -> None:
    """Карточка каталога, подборка и недоказанное: совет таблицы, что бы модель ни ответила."""
    ruled = advise(dataclasses.replace(facts, verdict=verdict))

    assert (ruled.advice, ruled.rule) == (advice, rule)
    assert "модель" not in ruled.words


def test_the_models_skip_on_an_undecided_table_is_named_to_the_human() -> None:
    """«Не пишем» только таблицей: ответ модели остаётся словами, совет — «не понять»."""
    facts = dataclasses.replace(_facts("target_sells", "sponsored"), verdict="skip")

    assert advise(facts).words == "по фактам не решить — решает человек; модель: не пишем"


def test_a_disagreement_names_both_sides_in_words() -> None:
    """Признаки остаются объяснением: человеку видно, что сказали правила и что — модель."""
    facts = dataclasses.replace(_facts("keyword_anchor", "target_sells"), verdict="skip")

    assert advise(facts).words == (
        "правила: пишем (домен — бизнес, ссылка его продвигает); модель: не пишем"
    )
    undecided = dataclasses.replace(_facts("target_sells", "sponsored"), verdict="write")
    assert advise(undecided).words == (
        "модель: пишем; правила не решили (по фактам не решить — решает человек)"
    )
