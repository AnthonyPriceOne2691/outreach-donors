"""Язык словами после «на» (`chain_text.in_language`) — у каждого языка, который знает модуль: коды
людям не показываем (`docs/UI_RULES.md`, 16.10). Слова стоят в причинах сборки («цепочка на … задана
не целиком»), в пустых разделах базы знаний и в наборах «Отправителя».

Имена — одна таблица языков модуля (`segment_alphabets.LANGUAGES`, там же имя в причине «язык лида —
португальский (pt)»): имя-прилагательное — в предложном падеже, другое — словом таблицы. Незнакомый
код — общими словами с кодом в скобках, как у незнакомой причины отказа лида. Записи базы знаний
выдуманы.
"""

from __future__ import annotations

import re

import pytest
from backend.features.sales import chain_text, kb, kb_gaps, queue_plan, segment_alphabets
from backend.features.sales.chain_text import in_language
from backend.features.sales.models import KbKind

#: Таблица языков модуля по коду.
TABLE = {found.code: found for found in segment_alphabets.LANGUAGES}


def test_every_letter_language_is_named_in_words() -> None:
    """Языки цепочек писем — в таблице, и у каждого — слова, а не код."""
    assert set(chain_text.LANGUAGES) <= set(TABLE)
    assert {code: in_language(code) for code in chain_text.LANGUAGES} == {
        "ru": "на русском",
        "en": "на английском",
    }


def test_every_language_the_module_knows_has_its_words_after_na() -> None:
    """Каждый язык таблицы — словами после «на»: имя-прилагательное — падежом, иначе словом таблицы.
    Новый язык с именем-существительным без слова таблицы здесь красный, а не «на ивритом»."""
    for code, found in TABLE.items():
        assert found.prepositional or found.name.endswith("ий"), f"{code}: нет слова после «на»"
        words = in_language(code)
        assert re.fullmatch(r"на [а-яё]+", words), words
        assert words != f"на {found.name}" or found.prepositional == found.name


@pytest.mark.parametrize(
    ("code", "words"),
    [
        ("pt", "на португальском"),
        ("de", "на немецком"),
        ("uk", "на украинском"),
        ("el", "на греческом"),
        ("zh", "на китайском"),
        ("he", "на иврите"),
        ("hi", "на хинди"),
        ("pt-br", "на португальском"),
        ("en-us", "на английском"),
    ],
)
def test_a_language_is_named_in_the_prepositional_case(code: str, words: str) -> None:
    assert in_language(code) == words


@pytest.mark.parametrize("code", ["xx", "tlh", "xx-yy"])
def test_an_unknown_code_is_named_in_general_words_with_the_code_in_brackets(code: str) -> None:
    assert in_language(code) == f"на другом языке ({code})"


def test_the_words_reach_the_reasons_and_the_knowledge_base_gaps() -> None:
    """Причина сборки и пустой раздел базы знаний — словами языка, без кодов."""
    assert queue_plan.incomplete("de") == "цепочка на немецком задана не целиком"
    facts = [
        kb.Fact(1, KbKind.CASE, "pt", "Выдуманный кейс", "Выдуманный текст.", ()),
        kb.Fact(2, KbKind.CASE, "xx", "Выдуманный кейс", "Выдуманный текст.", ()),
    ]

    [ru, en] = [gap for gap in kb_gaps.gaps(facts) if gap.kind is KbKind.CASE]

    expected = "агент возьмёт записи на португальском, другом языке (xx) и переведёт"
    assert ru.words == f"Кейсов на русском нет — {expected}"
    assert en.words == f"Кейсов на английском нет — {expected}"
