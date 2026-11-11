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
    CATALOG_WORDS,
    EXPLICIT_AD,
    FIRM,
    RULES,
    Advice,
    Ruled,
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
    facts: LinkFacts, advice: Advice, rule: str, verdict: str
) -> None:
    """Карточка каталога — бесплатная и в подборке «лучшие …» — и недоказанное: совет таблицы, что бы
    модель ни ответила."""
    ruled = advise(dataclasses.replace(facts, verdict=verdict))

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


#: По образцу фактов на каждое правило таблицы — и все ответы модели, с которыми их сверяют.
SAMPLES: dict[str, LinkFacts] = {
    "same_owner": _facts("money_anchor", "target_sells", "same_owner"),
    "unproven": _facts("money_anchor", link=False, target=False),
    "redirect": _facts("money_anchor", "target_redirect"),
    "noncommercial": _facts("money_anchor", "calls_to_buy", "target_noncommercial"),
    "press_release": _facts("press_release", "brand_anchor", "target_sells"),
    "navigation": _facts("site_navigation", "target_sells"),
    "ranked_directory": _facts("directory_entry", "recommends", "target_sells"),
    "free_directory": _facts("directory_entry", "visit_button", "target_sells"),
    "citation": _facts("source_of_fact", "brand_anchor", "target_media"),
    "paid_or_ad": _facts("brand_anchor", "target_sells", disclosure="Реклама"),
    "promoted": _facts("keyword_anchor", "target_sells"),
    "selling_anchor": _facts("money_anchor", target=False),
    "about_business": _facts(*CARD_PAGE),
    "media": _facts("brand_anchor", "target_media"),
    "brand_mention": _facts("brand_anchor", "target_sells"),
    "no_signs": _facts("target_sells"),
    "rest": _facts("target_sells", "sponsored"),
}
VERDICTS = ("write", "skip", "unclear", "")
BY_TABLE = {rule.key: rule for rule in RULES}
GRID = [(key, verdict) for key in SAMPLES for verdict in VERDICTS]


def test_every_rule_of_the_table_has_a_sample() -> None:
    assert set(SAMPLES) == set(BY_TABLE)
    assert {key: advise(facts).rule for key, facts in SAMPLES.items()} == {
        key: key for key in SAMPLES
    }


def _plain(key: str, verdict: str) -> tuple[Advice, str]:
    """Сверка у обычного издания — как её ввели таблицы v4–v6, записанная ожиданием, а не кодом
    судьи: модель не решила или правило твёрдое — таблица; совпали — таблица; таблица не решила —
    «пишем» модели совет, её «не пишем» человеку; разошлись — спор."""
    table = BY_TABLE[key].advice
    if verdict not in ("write", "skip") or key in {
        "free_directory",
        "ranked_directory",
        "unproven",
    }:
        return table, key
    if table.value == verdict:
        return table, key
    if table is Advice.UNCLEAR:
        return (Advice.WRITE, "verdict") if verdict == "write" else (Advice.UNCLEAR, key)
    return Advice.UNCLEAR, "disagree"


@pytest.mark.parametrize(("key", "verdict"), GRID)
def test_without_the_sign_the_advice_is_what_it_was(key: str, verdict: str) -> None:
    """Правило каталога не трогает обычное издание: без признака донора каждая ветка сверки даёт
    тот же совет тем же правилом, что до него."""
    facts = dataclasses.replace(SAMPLES[key], verdict=verdict)

    ruled = advise(facts)

    assert (ruled.advice, ruled.rule) == _plain(key, verdict)
    assert ruled == advise(facts, catalog_donor=False)
    assert CATALOG_WORDS not in ruled.words


@pytest.mark.parametrize(("key", "verdict"), GRID)
def test_a_catalog_donor_is_written_to_only_for_an_explicit_ad(key: str, verdict: str) -> None:
    """У донора-каталога «не понять» не бывает: «не пишем» таблицы — её правилом и её словами,
    «пишем» — только правилом явной рекламы и если модель не сказала «не пишем», остальное — «не
    пишем» правилом каталога (решение владельца 11.11.2026)."""
    facts = dataclasses.replace(SAMPLES[key], verdict=verdict)
    table = BY_TABLE[key]

    ruled = advise(facts, catalog_donor=True)

    if table.advice is Advice.SKIP:
        assert ruled == Ruled(Advice.SKIP, key, table.words)
    elif key in EXPLICIT_AD and verdict != "skip":
        assert ruled == Ruled(Advice.WRITE, key, table.words)
    else:
        assert (ruled.advice, ruled.rule) == (Advice.SKIP, CATALOG_RULE)
        assert ruled.words.startswith(f"{CATALOG_WORDS}; правила: ")


@pytest.mark.parametrize(
    ("facts", "verdict", "advice", "rule"),
    [
        # Страница карточки, прочитанная статьёй о домене: и согласное «пишем» — «не пишем».
        (SAMPLES["about_business"], "write", Advice.SKIP, CATALOG_RULE),
        (SAMPLES["about_business"], "skip", Advice.SKIP, CATALOG_RULE),
        (SAMPLES["promoted"], "write", Advice.SKIP, CATALOG_RULE),
        (SAMPLES["selling_anchor"], "", Advice.SKIP, CATALOG_RULE),
        # Явная реклама на странице: раскрытие, рекламное место, пометка «пресс-релиз».
        (SAMPLES["paid_or_ad"], "write", Advice.WRITE, "paid_or_ad"),
        (SAMPLES["paid_or_ad"], "", Advice.WRITE, "paid_or_ad"),
        (SAMPLES["paid_or_ad"], "unclear", Advice.WRITE, "paid_or_ad"),
        (_facts("ad_slot", "target_sells"), "write", Advice.WRITE, "paid_or_ad"),
        (SAMPLES["press_release"], "write", Advice.WRITE, "press_release"),
        # …но не против ответа модели.
        (SAMPLES["paid_or_ad"], "skip", Advice.SKIP, CATALOG_RULE),
        (SAMPLES["press_release"], "skip", Advice.SKIP, CATALOG_RULE),
        # Таблица не решила: и «пишем» модели — «не пишем».
        (SAMPLES["rest"], "write", Advice.SKIP, CATALOG_RULE),
        (SAMPLES["brand_mention"], "write", Advice.SKIP, CATALOG_RULE),
        (SAMPLES["unproven"], "write", Advice.SKIP, CATALOG_RULE),
        (SAMPLES["unproven"], "", Advice.SKIP, CATALOG_RULE),
        # Пометка `sponsored` без раскрытия — не явная реклама: каталог ставит её всем ссылкам.
        (
            _facts("directory_entry", "visit_button", "target_sells", "sponsored"),
            "write",
            Advice.SKIP,
            CATALOG_RULE,
        ),
        (
            _facts("money_anchor", "calls_to_buy", "target_sells", "sponsored"),
            "write",
            Advice.SKIP,
            CATALOG_RULE,
        ),
        # «Не пишем» таблицы — её правилом: «пишем» модели его у каталога не оспаривает.
        (SAMPLES["no_signs"], "write", Advice.SKIP, "no_signs"),
        (SAMPLES["citation"], "write", Advice.SKIP, "citation"),
        (SAMPLES["free_directory"], "write", Advice.SKIP, "free_directory"),
        (SAMPLES["ranked_directory"], "write", Advice.SKIP, "ranked_directory"),
    ],
)
def test_the_named_cases_of_a_catalog_donor(
    facts: LinkFacts, verdict: str, advice: Advice, rule: str
) -> None:
    ruled = advise(dataclasses.replace(facts, verdict=verdict), catalog_donor=True)

    assert (ruled.advice, ruled.rule) == (advice, rule)


@pytest.mark.parametrize(
    ("key", "verdict", "words"),
    [
        ("about_business", "skip", "правила: пишем, модель: не пишем"),
        ("about_business", "write", "правила: пишем, модель: пишем"),
        ("rest", "write", "правила: не понять, модель: пишем"),
        ("paid_or_ad", "skip", "правила: пишем, модель: не пишем"),
        ("brand_mention", "unclear", "правила: не понять, модель: не понять"),
        ("unproven", "", "правила: не понять"),  # суд до общего ответа: модель не названа
    ],
)
def test_the_catalog_rule_says_why_and_what_both_sides_said(
    key: str, verdict: str, words: str
) -> None:
    """Слова правила каталога — короткие: их читают под решением строки."""
    ruled = advise(dataclasses.replace(SAMPLES[key], verdict=verdict), catalog_donor=True)

    assert ruled.words == f"донор-каталог, его карточки не рекламодатели; {words}"
    assert len(ruled.words) <= 85


def test_a_table_skip_at_a_catalog_keeps_its_own_words() -> None:
    """Слова таблицы точнее слов правила каталога: «пишем» модели в них не появляется."""
    facts = dataclasses.replace(SAMPLES["no_signs"], verdict="write")
    assert advise(facts).rule == "disagree"  # у обычного издания такой спор — человеку

    ruled = advise(facts, catalog_donor=True)

    assert ruled == Ruled(Advice.SKIP, "no_signs", "ни одного признака рекламы")


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
