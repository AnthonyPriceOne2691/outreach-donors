"""Судья сегмента v6: язык, который буквы не назвали, называет модель фактом с цитатой.

Замер 19.11 на прогонах сбора: сайт латиницей при письмах на русском ушёл человеку правилом
«язык сайта — не понять по буквам (латиница)». Буквы знают письменность, а язык без своих букв
(английский, немецкий или испанский с редкими особыми буквами) не различают. Теперь модель отдаёт
язык фактом — код ISO 639-1 и дословная цитата строки сайта (`segment_facts.SiteFacts.language`), — и
сверка языка берёт его, только где буквы язык не назвали и только если он сходится с буквами
(`segment_language.named_by_model`): язык из таблицы, пишется письменностью сайта, буквы цитаты — из
его алфавита. Сошёлся — то же несовпадение, что названное буквами (правило `language`, «отказал бы»
словами с названием языка); нет факта или не сошёлся — «не понять», как раньше. Факты кэша без языка
читаются. Версия на языке писем по-прежнему снимает несовпадение буквами, до модели.

Модель — подставным транспортом, сеть не ходит. Сайты выдуманы; ниши, языки писем и письменности
разные.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.config.judge import JudgeMode
from backend.features.sales import segment_facts as sf
from backend.features.sales import segment_judge as sj
from backend.features.sales import segment_window as sw
from backend.features.sales.segment_cache import Known
from backend.features.sales.segment_facts import SiteFacts, read
from backend.features.sales.segment_language import Mismatch, mismatch, named_by_model
from backend.features.sales.segment_models import Decider, Lane
from backend.features.sales.segment_text import HOME_TEXT, PAGE_DESCRIPTION, PAGE_TITLE, SiteText
from scripts.eval_sales_segment import RULES
from tests.segment_facts_fakes import ANYWHERE, decide_any, facts
from tests.test_sales_segment_judge import client
from tests.test_sales_segment_second_chance import in_turn

#: Сайт латиницей без особых букв: при письмах другой письменности буквы язык не называют.
BAKERY = SiteText(
    (
        (PAGE_TITLE, "Fresh bakery in the city center"),
        (PAGE_DESCRIPTION, "We bake bread, cakes and pastries every morning"),
        (
            HOME_TEXT,
            "Our family bakery opened twenty years ago · We bake bread, cakes and pastries every "
            "morning · Order a birthday cake online and pick it up the same day · Contact us",
        ),
    )
)
EN_QUOTE = "We bake bread, cakes and pastries every morning"
#: Немецкий без умлаутов в тексте: буквы его тоже не называют.
GERMAN = SiteText(
    (
        (PAGE_TITLE, "Tischlerei in Graz"),
        (
            HOME_TEXT,
            "Wir bauen Tische, Schranke und Kuchen nach Mass · Unsere Tischlerei arbeitet seit "
            "vielen Jahren in Graz und liefert in ganz Steiermark · Wir planen Ihre neue Kuche "
            "gemeinsam mit Ihnen und montieren sie in wenigen Tagen · Rufen Sie uns an",
        ),
    )
)
DE_QUOTE = "Unsere Tischlerei arbeitet seit vielen Jahren in Graz"


def _unnamed(text: SiteText, letters: str) -> Mismatch:
    words = "\n".join(words for _, words in text.lines)
    found = mismatch(words, letters)
    assert found is not None
    assert found.code is None
    return found


# --- сверка факта модели с буквами ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "letters", "code", "quote", "words"),
    [
        (BAKERY, "ru", "en", EN_QUOTE, "язык сайта — английский (назвала модель), письма — русский"),
        (GERMAN, "ru", "de", DE_QUOTE,
         "язык сайта — немецкий (назвала модель), письма — русский; примете — английский"),
        (GERMAN, "el", "de", DE_QUOTE,
         "язык сайта — немецкий (назвала модель), письма — греческий; примете — английский"),
        (BAKERY, "kk", "en", EN_QUOTE, "язык сайта — английский (назвала модель), письма — казахский"),
    ],
    ids=["en-at-ru", "de-at-ru", "de-at-el", "en-at-kk"],
)  # fmt: skip
def test_a_language_the_letters_did_not_name_is_named_by_the_model_fact(
    text: SiteText, letters: str, code: str, quote: str, words: str
) -> None:
    found = named_by_model(_unnamed(text, letters), code, quote, letters)

    assert (found.code, found.by_model, found.script) == (code, True, "LATIN")
    assert found.words == words


@pytest.mark.parametrize(
    ("code", "quote"),
    [
        ("ru", EN_QUOTE),  # язык другой письменности: латиница не пишет русский
        ("en", "Über uns und unsere Kuchen"),  # буквы не из алфавита названного языка
        ("sw", EN_QUOTE),  # языка нет в таблице — назвать его по-русски нечем
        ("en", "Свежий хлеб каждое утро"),  # цитата не той письменности, что сайт
        ("", EN_QUOTE),
    ],
    ids=["other-script", "foreign-letters", "not-in-table", "quote-of-another-script", "no-code"],
)
def test_a_model_fact_that_does_not_fit_the_letters_leaves_the_mismatch_unnamed(
    code: str, quote: str
) -> None:
    unnamed = _unnamed(BAKERY, "ru")

    assert named_by_model(unnamed, code, quote, "ru") == unnamed


def test_one_borrowed_word_in_a_longer_quote_keeps_the_language() -> None:
    """Порог — доля слов с чужими буквами, как у сверки (`FOREIGN_SHARE_MIN`): имя кафе с «é» в
    цитате из шести слов английский не отменяет, а одно слово с «ü» из пяти — отменяет."""
    unnamed = _unnamed(BAKERY, "ru")

    assert named_by_model(unnamed, "en", "Welcome to Café Rossi in London", "ru").by_model
    assert named_by_model(unnamed, "en", "Über uns und unsere Kuchen", "ru") == unnamed


def test_a_quote_not_in_the_site_script_names_no_language_even_without_an_alphabet() -> None:
    """У языка без алфавита в таблице (иероглифы, деванагари) буквы цитаты не сверить — и тем
    важнее, что сама цитата написана письменностью сайта, а не строкой меню на другой."""
    unnamed = mismatch("北京家具工厂 实木桌椅定制 全国配送 " * 6, "ru")
    assert unnamed is not None

    assert named_by_model(unnamed, "zh", "Beijing furniture factory custom tables", "ru") == unnamed
    assert named_by_model(unnamed, "zh", "北京家具工厂 实木桌椅定制", "ru").code == "zh"


def test_a_language_named_by_the_letters_is_not_renamed_by_the_model() -> None:
    named = Mismatch("uk", "украинский", "CYRILLIC", letters="русский")

    assert named_by_model(named, "ru", "Ремонт квартир под ключ", "ru") == named


@pytest.mark.parametrize(
    ("words", "code", "quote", "name"),
    [
        ("दिल्ली में फर्नीचर की दुकान ठोस लकड़ी की मेज़ और कुर्सियाँ " * 4, "hi", "दिल्ली में फर्नीचर की दुकान", "хинди"),
        ("北京家具工厂 实木桌椅定制 全国配送 " * 6, "zh", "北京家具工厂 实木桌椅定制", "китайский"),
    ],
    ids=["devanagari", "han"],
)  # fmt: skip
def test_a_script_of_several_languages_takes_the_model_language_and_the_letters_of_the_run(
    words: str, code: str, quote: str, name: str
) -> None:
    """Письменность без строки в таблице письменностей: лиду — язык писем прогона, и слова вердикта
    говорят это: язык, который получит лид, другой, чем язык сайта."""
    unnamed = mismatch(words, "ru")
    assert unnamed is not None
    assert unnamed.code is None

    found = named_by_model(unnamed, code, quote, "ru")

    assert (found.code, found.said) == (code, name)
    assert (
        found.words == f"язык сайта — {name} (назвала модель), письма — русский; примете — русский"
    )


# --- факт модели: строгая форма, проверка цитаты, кэш ------------------------------------------------


def test_the_strict_form_asks_the_language_after_the_quote_and_before_the_russian_why() -> None:
    schema = sf.SCHEMA["schema"]

    assert list(schema["properties"])[-3:] == ["language", "language_quote", "why"]
    assert schema["required"] == list(schema["properties"])
    assert sf.KEYS == (
        "kind",
        "home",
        "home_quote",
        "served",
        "only",
        "network",
        "local",
        "core",
        "quote",
        "why",
    )


def _answer(**over: Any) -> str:
    return json.dumps(facts("yes", EN_QUOTE, "пекарня", **over), ensure_ascii=False)


def test_a_language_fact_with_its_line_in_the_text_is_kept() -> None:
    found = read(_answer(language=" EN ", language_quote=EN_QUOTE), BAKERY).facts

    assert found is not None
    assert (found.language, found.language_quote, found.dropped) == ("en", EN_QUOTE, 0)


@pytest.mark.parametrize(
    ("language", "quote"),
    [
        ("en", "We sell fresh fish and seafood daily"),  # строки нет в тексте
        ("xx", EN_QUOTE),  # кода нет в таблице языков
        ("en", "fresh bakery"),  # короче трёх слов
    ],
    ids=["quote-not-in-text", "unknown-code", "short-quote"],
)
def test_a_language_fact_not_proven_by_the_text_is_dropped_and_counted(
    language: str, quote: str
) -> None:
    found = read(_answer(language=language, language_quote=quote), BAKERY).facts

    assert found is not None
    assert (found.language, found.language_quote, found.dropped) == ("", "", 1)


def test_an_answer_without_the_language_keys_is_read_as_language_not_named() -> None:
    """Подставная модель и провайдер без строгой формы: ключей языка нет — ответ разобран."""
    found = read(_answer(), BAKERY).facts

    assert found is not None
    assert (found.language, found.language_quote, found.dropped) == ("", "", 0)


def test_cached_facts_keep_the_language_and_old_cached_facts_read_without_it() -> None:
    fresh = SiteFacts("own", "yes", EN_QUOTE, "пекарня", language="en", language_quote=EN_QUOTE)
    old = {key: value for key, value in fresh.as_json().items() if not key.startswith("language")}

    assert SiteFacts.from_json(fresh.as_json()) == fresh
    restored = SiteFacts.from_json(old)
    assert restored is not None
    assert (restored.language, restored.language_quote) == ("", "")


# --- судья: язык модели решает только там, где буквы молчат -------------------------------------------


async def _judged(text: SiteText, letters: str, *bodies: Any) -> sj.SegmentVerdict:
    model, _ = client(in_turn(*bodies))
    return await decide_any(
        model, host="bakery-sample.com", segment="Пекарни", text=text, letters=letters
    )


async def test_the_model_language_turns_an_unnamed_mismatch_into_a_named_one() -> None:
    verdict = await _judged(
        BAKERY, "ru", facts("yes", EN_QUOTE, "пекарня", language="en", language_quote=EN_QUOTE)
    )

    assert (verdict.lane, verdict.rule, verdict.decided_by) == (
        Lane.REJECT,
        "language",
        Decider.LANGUAGE,
    )
    assert verdict.reason == "язык сайта — английский (назвала модель), письма — русский"
    assert verdict.proven  # факты с языком — в кэш: повтор по ним даст тот же вердикт
    assert verdict.tokens == 700
    assert sj.effective(verdict, JudgeMode.SHADOW)[0] is Lane.WOULD_REJECT


@pytest.mark.parametrize(
    "body",
    [
        facts("yes", EN_QUOTE, "пекарня"),
        facts("yes", EN_QUOTE, "пекарня", language="ru", language_quote=EN_QUOTE),
    ],
    ids=["no-language", "language-of-another-script"],
)
async def test_without_a_fitting_model_language_the_site_stays_unclear_as_before(
    body: dict[str, Any],
) -> None:
    verdict = await _judged(BAKERY, "ru", body)

    assert (verdict.lane, verdict.rule) == (Lane.REVIEW, "language_unnamed")
    assert (
        verdict.reason
        == "язык сайта — не понять по буквам (латиница), письма — русский; примете — английский"
    )


async def test_a_site_with_a_version_in_the_letters_language_is_judged_whatever_the_model_says() -> (
    None
):
    """Страница выдачи на языке писем — версия на нём есть: несовпадения нет до модели, и язык из
    её фактов ничего не решает."""
    text = SiteText(
        (
            (PAGE_TITLE, "Свежая выпечка и торты на заказ в центре города"),
            (
                PAGE_DESCRIPTION,
                "Печём хлеб, торты и пирожные каждое утро и доставляем заказы по всему городу",
            ),
            *BAKERY.lines[2:],
        )
    )
    quote = "Печём хлеб, торты и пирожные каждое утро"

    verdict = await _judged(
        text, "ru", facts("yes", quote, "пекарня", language="en", language_quote=EN_QUOTE)
    )

    assert (verdict.lane, verdict.rule) == (Lane.ACCEPT, "segment_anywhere")


def test_cached_facts_name_the_language_again_without_the_model() -> None:
    """Вердикт по кэшу (`segment_window._recalled`): тот же текст, те же факты — тот же язык; факты
    без языка — «не понять», как раньше."""
    site = sw.Site(1, 1, 1, "bakery-sample.com", None, None, stored=BAKERY.for_model())
    ask = sw.Ask(
        "Пекарни", JudgeMode.SHADOW, None, None, datetime.now(UTC), letters="ru", scope=ANYWHERE
    )
    named = SiteFacts("own", "yes", EN_QUOTE, "пекарня", language="en", language_quote=EN_QUOTE)
    silent = SiteFacts("own", "yes", EN_QUOTE, "пекарня")

    found = sw._recalled(site, ask, Known(facts=named, model="gpt-5-mini")).verdict
    before = sw._recalled(site, ask, Known(facts=silent, model="gpt-5-mini")).verdict

    assert found is not None
    assert before is not None
    assert (found.lane, found.rule) == (Lane.REJECT, "language")
    assert (before.lane, before.rule) == (Lane.REVIEW, "language_unnamed")


# --- промпт -----------------------------------------------------------------------------------------


def test_the_prompt_asks_the_language_as_a_fact_of_the_site_words() -> None:
    prompt = sj.load_prompt()

    assert sj.PROMPT_VERSION == "sales-segment-v6"
    assert '4. "language" — a fact only, never a verdict' in prompt
    assert "never our labels, the segment or your own answer" in prompt
    assert '"quote", "language", "language_quote", "why"' in prompt
    assert RULES["quote"] in prompt  # строка дословной цитаты, которую портит eval, — прежняя
