"""Тема ключа без слов футпринта — для слов письма бизнесу ниши (10.11.2026).

Очередь «Бизнесам ниши» собиралась с темой «budgeting write for us»: ключ поиска гостевых
площадок шёл в тему письма целиком — «Sponsored articles on budgeting write for us sites».
Слова футпринта снимаются по той же таблице, по которой их ставит `expand`, — на любом языке
рынка, а не по английскому списку.
"""

from __future__ import annotations

import pytest
from backend.features.keywords.footprints import FOOTPRINTS, expand, niche_topic, topic_of


@pytest.mark.parametrize(
    ("keyword", "topic"),
    [
        ("budgeting write for us", "budgeting"),
        ("credit card reviews guest post", "credit card reviews"),
        ("investing submit a guest post", "investing"),  # длинный хвост целиком, не «… submit a»
        ("stock market sponsored post", "stock market"),
        ("garten gastbeitrag", "garten"),
        ("finanzas escribe para nosotros", "finanzas"),
        ("voyage article sponsorisé", "voyage"),
        ("budżet artykuł sponsorowany", "budżet"),
        ("Budgeting   Write For Us", "Budgeting"),  # регистр и пробелы — как набрал человек
    ],
)
def test_footprint_words_are_cut_off_the_topic(keyword: str, topic: str) -> None:
    assert topic_of(keyword) == topic


@pytest.mark.parametrize(
    "keyword",
    ["best budgeting apps", "guest posting tips", "write for us travel", "sports betting"],
)
def test_a_keyword_without_a_trailing_footprint_stays_as_is(keyword: str) -> None:
    assert topic_of(keyword) == keyword


def test_a_bare_footprint_has_no_topic() -> None:
    assert topic_of("write for us") == ""
    assert topic_of("  submit a guest post ") == ""


@pytest.mark.parametrize("language", sorted(FOOTPRINTS))
def test_every_generated_query_gives_back_its_topic(language: str) -> None:
    """Что поставил `expand`, то и снимается — на каждом языке таблицы."""
    for query in expand(["home garden"], FOOTPRINTS[language]):
        assert topic_of(query) == "home garden", query


def test_niche_topic_takes_the_first_key_that_has_a_topic() -> None:
    assert niche_topic(["write for us", "budgeting guest post"]) == "budgeting"
    assert niche_topic(["budgeting write for us", "saving money"]) == "budgeting"


def test_niche_topic_of_bare_footprints_keeps_the_first_key() -> None:
    assert niche_topic(["write for us", "guest post"]) == "write for us"
    assert niche_topic([]) == ""
