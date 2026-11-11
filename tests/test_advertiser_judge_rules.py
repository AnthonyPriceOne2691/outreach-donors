"""Совет судьи рекламодателей: таблица правил по фактам, сверка с общим ответом модели и признак
донора-каталога."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from backend.features.crawl.judge_facts import FLAGS, LinkFacts
from backend.features.crawl.judge_rules import (
    CARD_FACT,
    CATALOG_MIN_PAIRS,
    CATALOG_RULE,
    CATALOG_SHARE,
    FIRM,
    RULES,
    Advice,
    advise,
    is_catalog,
)
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
            Advice.SKIP,
            "ranked_directory",
        ),
        (
            _facts("directory_entry", "offer_list", "visit_button", "target_sells"),
            Advice.SKIP,
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


@pytest.mark.parametrize("catalog_donor", [False, True])
@pytest.mark.parametrize("verdict", ["write", "skip", "unclear", ""])
@pytest.mark.parametrize(
    ("facts", "advice", "rule"),
    [
        (_facts("directory_entry", "visit_button", "target_sells"), Advice.SKIP, "free_directory"),
        (_facts("directory_entry", "recommends", "target_sells"), Advice.SKIP, "ranked_directory"),
        (_facts("directory_entry", "offer_list", "target_refers"), Advice.SKIP, "ranked_directory"),
        (_facts("money_anchor", link=False, target=False), Advice.UNCLEAR, "unproven"),
    ],
)
def test_the_models_answer_does_not_override_a_firm_rule(
    facts: LinkFacts, advice: Advice, rule: str, verdict: str, catalog_donor: bool
) -> None:
    """Карточка каталога — бесплатная и в подборке «лучшие …» — и недоказанное: совет таблицы, что бы
    модель ни ответила и у какого бы донора пара ни была."""
    ruled = advise(dataclasses.replace(facts, verdict=verdict), catalog_donor=catalog_donor)

    assert rule in FIRM
    assert (ruled.advice, ruled.rule) == (advice, rule)
    assert "модель" not in ruled.words


def test_a_ranked_card_is_skipped_in_short_russian_words() -> None:
    """Подборке «лучшие …» не пишем (решение владельца 11.11.2026): прежде такая карточка шла
    человеку. Слова — короткие: их читают под решением строки."""
    [ranked] = [rule for rule in RULES if rule.key == "ranked_directory"]

    assert ranked.advice is Advice.SKIP
    assert ranked.words == "карточка каталога в подборке «лучшие\u00a0…»"


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


# --- донор-каталог ---------------------------------------------------------------------------------

#: Страница карточки, прочитанная статьёй о домене: таблица — «пишем» (`about_business`).
CARD_PAGE = ("article_about_target", "visit_button", "target_sells")


@pytest.mark.parametrize(
    "on", [CARD_PAGE, ("keyword_anchor", "target_sells"), ("money_anchor", "directory_entry")]
)
def test_a_catalog_donors_dispute_is_skipped_by_its_own_rule(on: tuple[str, ...]) -> None:
    """У донора-каталога спор «таблица — пишем, модель — не пишем» — «не пишем» своим правилом и
    своими словами, а не «не понять» (решение владельца 11.11.2026): его карточки — не рекламодатели."""
    facts = dataclasses.replace(_facts(*on), verdict="skip")
    assert advise(facts).advice is Advice.UNCLEAR  # без признака донора — спор, решает человек

    ruled = advise(facts, catalog_donor=True)

    assert (ruled.advice, ruled.rule) == (Advice.SKIP, CATALOG_RULE)
    assert ruled.words == (
        "донор-каталог, его карточки не рекламодатели; правила: пишем, модель: не пишем"
    )


@pytest.mark.parametrize(
    ("on", "verdict", "advice", "rule"),
    [
        # Согласное «пишем» остаётся: рекламные статьи бывают и у каталога.
        (CARD_PAGE, "write", Advice.WRITE, "about_business"),
        (("keyword_anchor", "target_sells"), "write", Advice.WRITE, "promoted"),
        # Модель сама не решила или ответа нет — совет таблицы.
        (CARD_PAGE, "unclear", Advice.WRITE, "about_business"),
        (CARD_PAGE, "", Advice.WRITE, "about_business"),
        # Спор в другую сторону — человеку, как у обычного издания.
        (("target_sells",), "write", Advice.UNCLEAR, "disagree"),
        # Таблица не решила: «не пишем» модели — человеку, её «пишем» — совет.
        (("brand_anchor", "target_sells"), "skip", Advice.UNCLEAR, "brand_mention"),
        (("target_sells", "sponsored"), "skip", Advice.UNCLEAR, "rest"),
        (("target_sells", "sponsored"), "write", Advice.WRITE, "verdict"),
        # Согласное «не пишем» — правилом таблицы.
        (("target_sells",), "skip", Advice.SKIP, "no_signs"),
    ],
)
def test_a_catalog_donor_changes_nothing_but_its_dispute(
    on: tuple[str, ...], verdict: str, advice: Advice, rule: str
) -> None:
    """Признак донора меняет одну ветку сверки; остальные советы у каталога и у обычного издания
    одни и те же — и правилом, и словами."""
    facts = dataclasses.replace(_facts(*on), verdict=verdict)

    ruled = advise(facts, catalog_donor=True)

    assert (ruled.advice, ruled.rule) == (advice, rule)
    assert ruled == advise(facts)


def test_a_dispute_without_the_sign_stays_with_the_human() -> None:
    """У обычного издания (и у донора, о котором ещё не знаем) тот же спор — «не понять»."""
    facts = dataclasses.replace(_facts(*CARD_PAGE), verdict="skip")

    for ruled in (advise(facts), advise(facts, catalog_donor=False)):
        assert (ruled.advice, ruled.rule) == (Advice.UNCLEAR, "disagree")
        assert ruled.words == (
            "правила: пишем (статья — о самом домене, домен — бизнес); модель: не пишем"
        )


@pytest.mark.parametrize(
    ("pairs", "cards", "catalog"),
    [
        (19, 19, False),  # пар мало: и при одних карточках — ещё не каталог
        (20, 20, True),
        (20, 14, True),  # ровно 70 %
        (20, 13, False),  # 65 %
        (100, 70, True),
        (100, 69, False),  # 69 %
        (1000, 699, False),
        (1000, 700, True),
        (30, 21, True),  # ровно 70 % на другом числе пар
        (30, 20, False),
        (500, 0, False),  # обычное издание: пар много, карточек нет
        (0, 0, False),
    ],
)
def test_a_catalog_is_twenty_pairs_and_seven_cards_in_ten(
    pairs: int, cards: int, catalog: bool
) -> None:
    """Порог каталога: не меньше двадцати рассуженных пар и не меньше 70 % карточек среди них."""
    assert (CATALOG_MIN_PAIRS, CATALOG_SHARE) == (20, 0.7)
    assert is_catalog(pairs, cards) is catalog


def test_the_card_fact_is_a_fact_the_model_is_asked() -> None:
    """Признак каталога читает факт из строки суда по имени: имя обязано быть фактом модели."""
    assert CARD_FACT in FLAGS
    assert getattr(_facts(CARD_FACT), CARD_FACT) is True
