"""Ключи прогона сбора (`sales/segment_keywords.py`): отсев — только мусор и точный повтор, почти
повтор — пометка с именем похожего ключа; в отчёте прогона — взятые, отсеянные, пропавшие и пустые.

Проверка прода 14.10: общий ключ отсеял как почти повторы все свои «… + город», а у каждого
города своя выдача. Наборы — разных ниш и языков (правка — под любую нишу и рынок, а не под
пул, на котором нашлась ошибка). Домены и ключи выдуманы.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.sales.segment_keywords import REPEAT, clean_keywords, run_keywords
from backend.features.sales.segment_models import SalesSegmentRunModel


def run_of(*, estimate: Any = None, report: Any = None) -> SalesSegmentRunModel:
    """Строка прогона без базы: отчёт ключей читает только её поля."""
    return SalesSegmentRunModel(
        hypothesis_id=1,
        keywords=["plumber london", "emergency plumber london"],
        country="gb",
        language="en",
        depth_pages=1,
        mode="shadow",
        estimate=estimate if estimate is not None else {},
        report=report if report is not None else {},
    )


@pytest.mark.parametrize(
    ("text", "taken", "twins"),
    [
        (
            "доставка грузов\nдоставка грузов москва\nдоставка грузов казань\nдоставка грузов по россии",
            [
                "доставка грузов",
                "доставка грузов москва",
                "доставка грузов казань",
                "доставка грузов по россии",
            ],
            {
                "доставка грузов москва": "доставка грузов",
                "доставка грузов казань": "доставка грузов",
            },
        ),
        (
            "dentist berlin\ndentist munich\nzahnarzt berlin",
            ["dentist berlin", "dentist munich", "zahnarzt berlin"],
            {},
        ),
        (
            "abogado laboral madrid\nabogado laboral\nabogado laboral barcelona",
            ["abogado laboral madrid", "abogado laboral", "abogado laboral barcelona"],
            {"abogado laboral": "abogado laboral madrid"},
        ),
    ],
    ids=["ru-cargo-cities", "de-dentists-distinct", "es-lawyers-general-after-city"],
)
def test_a_near_repeat_stays_with_a_note_naming_its_twin_and_the_order_is_kept(
    text: str, taken: list[str], twins: dict[str, str]
) -> None:
    found = clean_keywords(text)

    assert found.taken == taken
    assert found.rejected == {}
    assert {phrase: note.split("«")[1].split("»")[0] for phrase, note in found.notes.items()} == (
        twins
    )


def test_the_note_counts_the_shared_words_like_the_shared_rule() -> None:
    found = clean_keywords("стоматологическая клиника\nстоматологическая клиника алматы")

    assert found.notes == {
        "стоматологическая клиника алматы": "похож на «стоматологическая клиника» — общих слов 2 "
        "из 3: выдача может частично совпасть, ключ взят",
    }


@pytest.mark.parametrize(
    ("text", "taken", "repeat"),
    [
        ("Plumber London\nplumber   london", ["plumber london"], "plumber london"),
        (
            "пиццерия — доставка\nпиццерия – доставка",
            ["пиццерия - доставка"],
            "пиццерия - доставка",
        ),
        ("straße kaufen\nSTRASSE kaufen", ["straße kaufen"], "strasse kaufen"),
        ("café paris\ncafé  PARIS", ["café paris"], "café paris"),
    ],
    ids=["case-and-spaces", "dashes", "casefold-of-the-serp", "diacritics-kept"],
)
def test_an_exact_repeat_is_cut_with_its_reason(text: str, taken: list[str], repeat: str) -> None:
    """Точный повтор — тот же ключ после общей нормализации и для выдачи: регистр, пробелы,
    виды тире; `ß` и `ss` выдача считает одним запросом — платить за второй не за что."""
    found = clean_keywords(text)

    assert found.taken == taken
    assert found.rejected == {repeat: REPEAT}
    assert found.notes == {}


def test_garbage_keeps_the_hygiene_reason_even_when_repeated() -> None:
    found = clean_keywords(
        "site:shop.example.test купить\nSite:shop.example.test купить\nкупить велосипед"
    )

    assert found.taken == ["купить велосипед"]
    assert found.rejected == {
        "site:shop.example.test купить": "поисковый оператор: так пользователи не ищут"
    }


def test_blank_lines_are_not_keywords() -> None:
    assert clean_keywords(" \n\n\t\n").taken == []


def test_the_run_report_names_taken_rejected_lost_and_empty_keywords() -> None:
    run = run_of(
        estimate={
            "rejected": {"plumber london": REPEAT, "site:x.example.test": "поисковый оператор"},
            "notes": {"emergency plumber london": "похож на «plumber london»"},
        },
        report={
            "serp": {
                "keywords": 1,
                "lost_keywords": ["emergency plumber london"],
                "empty_keywords": ["plumber london"],
            }
        },
    )

    found = run_keywords(run)

    assert found.taken == ["plumber london", "emergency plumber london"]
    assert found.rejected == {"plumber london": REPEAT, "site:x.example.test": "поисковый оператор"}
    assert found.notes == {"emergency plumber london": "похож на «plumber london»"}
    assert (found.lost, found.empty) == (["emergency plumber london"], ["plumber london"])


@pytest.mark.parametrize(
    ("estimate", "report"),
    [
        ({}, {}),
        ({"rejected": "не словарь", "notes": None}, {"serp": "не словарь"}),
        ({"lines": ["смета"]}, {"serp": {"lost_keywords": None}}),
    ],
    ids=["before-serp", "malformed", "old-estimate"],
)
def test_a_run_before_the_serp_or_of_an_old_estimate_has_no_lists_instead_of_failing(
    estimate: dict[str, Any], report: dict[str, Any]
) -> None:
    found = run_keywords(run_of(estimate=estimate, report=report))

    assert (found.notes, found.rejected, found.lost, found.empty) == ({}, {}, [], [])
    assert found.taken == ["plumber london", "emergency plumber london"]
