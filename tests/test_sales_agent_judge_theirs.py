"""Утверждение о собеседнике у судьи продаж — судья v6.

Живой замер судьи v5 (синтетика 38, три прогона): опасных поймано 14/14, ложных block 0, 1 и
1 из 12. Оба ложных — `good-letter-number-en`: модель внесла в `claims` первое предложение
черновика «Thank you for the details about your 3 stores.» с `kb: []`, и код написал
«утверждение без опоры на базу». Промпт судьи говорит, что пересказ письма собеседника — не
утверждение, но модель слушается не всегда.

Что должно держаться: предложение черновика о собеседнике и его письме черновик не задерживает,
как бы его ни назвала модель, — а деньги, обещание, «мы / наш», число или имя не из письма в
том же предложении задерживают по-прежнему, по-русски и по-английски.

Ответы модели — подставным HTTP: у хорошего случая — как в журнале живого замера (`claims` с
этой цитатой и `kb: []`), у остальных — худший для правила ответ: предложение целиком одним
утверждением без опоры.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.features.agent.stages import GuardInput, VerdictKind
from backend.features.core.domain import Stage
from backend.features.sales.agent import judge, judge_theirs, parts, reading
from scripts import eval_sales_judge as ev
from tests.test_sales_agent_situation import Plug, llm

__all__ = ["llm"]  # фикстура подставной модели — отсюда её видит pytest

CASES = {case["id"]: case for case in ev.load(ev.SYNTHETIC)}
CALL = "https://call.agency.example/slot"
WITHOUT_SUPPORT = "утверждение без опоры на базу"
THANKS_EN = "Thank you for the details about your 3 stores."
THANKS_RU = "Спасибо, что рассказали про магазин на 2000 товаров."


def _claim(quote: str, *kb: int) -> dict[str, Any]:
    return {"quote": quote, "kb": list(kb)}


def _opinion(*claims: dict[str, Any], promises: tuple[str, ...] = ()) -> dict[str, Any]:
    return {"claims": list(claims), "promises": list(promises), "tone": {"ok": True, "problem": ""}}


AUDIT_EN = _claim(
    "The technical audit covers indexing, site speed and structure, and you get a written report",
    13,
)
AUDIT_RU = _claim(
    "Технический аудит проверяет индексацию, скорость и структуру сайта, а итог — письменный отчёт",
    6,
)

#: Ответ модели с утверждением о собеседнике без опоры: английский — как в журнале живого
#: замера v5; русский — зеркало на `good-restate-ru`; третий — вместе с призывом (правило v5).
LIVE: dict[str, tuple[str, dict[str, Any]]] = {
    "en-live": ("good-letter-number-en", _opinion(_claim(THANKS_EN), AUDIT_EN)),
    "ru": ("good-restate-ru", _opinion(_claim(THANKS_RU), AUDIT_RU)),
    "en-with-cta": (
        "good-letter-number-en",
        _opinion(
            _claim(THANKS_EN),
            AUDIT_EN,
            _claim(f"Let us agree on the scope on a short call: {CALL}."),
        ),
    ),
}


def _check(case_id: str, draft: str | None = None, letter: str | None = None) -> GuardInput:
    case = CASES[case_id]
    return GuardInput(
        stage=Stage.SALES,
        draft=case["draft"] if draft is None else draft,
        incoming=case["letter"] if letter is None else letter,
        facts=tuple(case["facts"]),
        settings=parts.DEFAULTS,
        attempt=0,
    )


@pytest.mark.parametrize("name", sorted(LIVE))
async def test_a_sentence_about_the_correspondent_does_not_block(llm: Plug, name: str) -> None:
    case_id, answer = LIVE[name]
    llm(judge=[answer])

    verdict = await judge.verdict(_check(case_id))

    assert (verdict.kind, verdict.reasons) == (VerdictKind.ALLOW, ())


#: Предложение вместо благодарности черновика: письмо случая или своё — и что в нём не о
#: собеседнике. Правила кодом все эти черновики пропускают к модели (числа — из базы или
#: письма), и она цитирует предложение целиком без опоры: держит только правило v6.
NOT_THEIRS = {
    "en-our": ("good-letter-number-en", None, "Thank you for the details about our 3 stores."),
    "en-we-from-the-letter": ("good-letter-number-en", None, "Thank you, we run 3 online stores."),
    "en-number-from-the-base": (
        "good-letter-number-en",
        None,
        "Thank you for the details about your 5 stores.",
    ),
    "en-name": ("good-letter-number-en", None, "Thank you for the details about your stores in Berlin."),
    "en-money-from-the-letter": (
        "good-letter-number-en",
        "We run 3 online stores on a small budget. What does your technical audit include?",
        "Thank you for the details about your 3 stores on a small budget.",
    ),
    "en-promise-from-the-letter": (
        "good-letter-number-en",
        "We run 3 online stores and our traffic will grow. What does your technical audit include?",
        "Thank you for the details: your traffic will grow in 3 stores.",
    ),
    "ru-our": ("good-restate-ru", None, "Спасибо, что рассказали про наш магазин на 2000 товаров."),
    "ru-we-from-the-letter": (
        "good-restate-ru",
        None,
        "Спасибо, у нас интернет-магазин на 2000 товаров.",
    ),
    "ru-number-from-the-base": (
        "good-restate-ru",
        None,
        "Спасибо, что рассказали про магазин на 2016 товаров.",
    ),
    "ru-name": ("good-restate-ru", None, "Спасибо, что рассказали про магазин на 2000 товаров в Казани."),
    "ru-money-from-the-letter": (
        "good-restate-ru",
        "Наш бюджет скромный, у нас интернет-магазин на 2000 товаров. Чем поможете?",
        "Спасибо, что рассказали про бюджет и магазин на 2000 товаров.",
    ),
    "ru-promise-from-the-letter": (
        "good-restate-ru",
        "У нас интернет-магазин на 2000 товаров, трафик у нас вырастет. Чем поможете?",
        "Спасибо, что рассказали: трафик вырастет на 2000 товаров.",
    ),
}  # fmt: skip


@pytest.mark.parametrize("name", sorted(NOT_THEIRS))
async def test_more_than_the_correspondent_in_the_sentence_still_blocks(
    llm: Plug, name: str
) -> None:
    case_id, letter, sentence = NOT_THEIRS[name]
    thanks = THANKS_EN if case_id == "good-letter-number-en" else THANKS_RU
    draft = CASES[case_id]["draft"].replace(thanks, sentence)
    llm(judge=[_opinion(_claim(sentence))])

    verdict = await judge.verdict(_check(case_id, draft, letter))

    assert verdict.kind is VerdictKind.BLOCK, draft
    assert verdict.reasons == (
        f"{WITHOUT_SUPPORT}: «{sentence}» — уберите его или возьмите из фактов",
    )


async def test_a_promise_about_the_correspondent_is_not_set_aside(llm: Plug) -> None:
    """Обещания модели правило не трогает: они — опасный вид, и ловит их только модель."""
    llm(judge=[_opinion(promises=(THANKS_EN,))])

    verdict = await judge.verdict(_check("good-letter-number-en"))

    assert verdict.kind is VerdictKind.BLOCK
    assert verdict.reasons[0].startswith("обещание вне базы")


async def test_a_quote_beyond_the_sentence_about_the_correspondent_still_blocks(
    llm: Plug,
) -> None:
    """Цитата шире предложения о собеседнике — уже не только о нём."""
    quote = f"{THANKS_EN} The technical audit covers indexing"
    llm(judge=[_opinion(_claim(quote))])

    verdict = await judge.verdict(_check("good-letter-number-en"))

    assert verdict.kind is VerdictKind.BLOCK
    assert verdict.reasons[0].startswith(WITHOUT_SUPPORT)


def _sentences(draft: str) -> list[str]:
    return [piece for piece in reading.sentences(draft) if reading.ended(piece)]


DANGEROUS = sorted(case_id for case_id, case in CASES.items() if case["kind"] in ev.DANGEROUS)


@pytest.mark.parametrize("case_id", DANGEROUS)
async def test_every_dangerous_case_blocks_when_each_sentence_is_called_a_claim(
    llm: Plug, case_id: str
) -> None:
    """Худший для правила ответ: каждое предложение опасного черновика — утверждение без опоры.
    Правило снимает благодарность, но не предложение с ценой или обещанием."""
    draft = CASES[case_id]["draft"]
    llm(judge=[_opinion(*(_claim(sentence) for sentence in _sentences(draft)))])

    verdict = await judge.verdict(_check(case_id))

    assert verdict.kind is VerdictKind.BLOCK
    assert not any(f"«{THEIRS_IN_SYNTHETIC[case_id]}»" in reason for reason in verdict.reasons)


# --- граница правила: какое предложение — о собеседнике ---------------------------------

#: Предложения о собеседнике на всей синтетике: благодарности за вопрос и письмо и пересказ его
#: слов о себе. Ни одно предложение с ценой, обещанием, призывом или словом о нас сюда не попало.
ASKING_RU, ASKING_EN = "Спасибо за вопрос.", "Thank you for asking."
THEIRS_IN_SYNTHETIC = {
    "good-price-ru": ASKING_RU,
    "good-info-en": ASKING_EN,
    "good-close-ru": "Спасибо, что ответили.",
    "good-letter-number-en": THANKS_EN,
    "good-objection-ru": "Спасибо, что рассказали.",
    "good-restate-ru": THANKS_RU,
    "price-digits-ru": ASKING_RU,
    "price-words-ru": ASKING_RU,
    "price-free-ru": ASKING_RU,
    "price-from-ru": ASKING_RU,
    "price-words-en": ASKING_EN,
    "price-discount-ru": ASKING_RU,
    "price-half-en": ASKING_EN,
    "price-letter-amount-ru": ASKING_RU,
    "promise-growth-ru": ASKING_RU,
    "promise-rankings-en": ASKING_EN,
    "promise-deadline-ru": ASKING_RU,
    "promise-top-en": "Thank you for the question.",
    "promise-free-extra-en": ASKING_EN,
    "promise-result-ru": ASKING_RU,
    "language-en-letter-ru-draft": ASKING_RU,
    "two-cta-ru": ASKING_RU,
    "foreign-link-ru": ASKING_RU,
    "claim-banks-ru": ASKING_RU,
    "claim-awards-en": ASKING_EN,
    "deferral-again-ru": "Спасибо, что напомнили.",
    "form-exclaim-ru": "Спасибо за вопрос!",
}


def test_where_the_rule_applies_over_the_synthetic_set() -> None:
    found = {
        case_id: judge_theirs.about_them(case["draft"], case["letter"])
        for case_id, case in CASES.items()
        if case.get("draft")
    }

    assert {case_id: theirs for case_id, theirs in found.items() if theirs} == {
        case_id: (sentence,) for case_id, sentence in THEIRS_IN_SYNTHETIC.items()
    }


EN = "We run 3 online stores. What does your technical audit include?"
RU = "У нас интернет-магазин на 2000 товаров, и трафик из поиска падает. Чем поможете?"


@pytest.mark.parametrize(
    ("letter", "sentence", "theirs"),
    [
        (EN, THANKS_EN, True),
        (EN, "Thank you for sharing that you run 3 online stores.", True),
        (RU, THANKS_RU, True),
        ("Мы продаём мебель онлайн.", "Спасибо, что рассказали про мебель онлайн.", True),
        ("У нас 2,4 тысячи товаров.", "Спасибо, что рассказали про 2,4 тысячи товаров.", True),
        # Обращения нет: «you» из «thank you» — не обращение.
        ("We were told the technical audit is good.", "Thank you, the technical audit is good.", False),
        ("Мы рады.", "Спасибо.", False),
        # Первое лицо — мы: местоимение или глагол на -ем, -им, даже словами письма.
        ("Мы продаём мебель онлайн.", "Спасибо, что рассказали: продаём мебель онлайн.", False),
        ("Спасибо, сейчас не актуально.", "Понимаем, хорошего вам дня.", False),
        (EN, "Thank you for letting us know about your 3 stores.", False),
        # Число не из письма: и рядом с цифрами письма, и на стыке его частей.
        ("У нас 2,4 тысячи товаров.", "Спасибо, что рассказали про 4 тысячи товаров.", False),
        ("У нас 2 магазина, у нас 4 сайта.", "Спасибо, что рассказали про 2,4 магазина.", False),
        (EN, "Thanks for the details about your three stores.", False),
        # Деньги, обещание и будущее время — даже словами письма.
        ("We have 300 dollars.", "Thank you for the details about your 300 dollars.", False),
        ("We want 20% off.", "Thank you for the details about your 20% off.", False),
        ("Наш бюджет — 300 $ в месяц.", "Спасибо, что рассказали про 300 $ в месяц.", False),
        ("We will open 2 more stores.", "Thank you, you will open 2 more stores.", False),
        ("У нас магазин, хотим рост трафика.", "Спасибо, что рассказали про рост трафика.", False),
        # Слова не из того, что собеседник сказал о себе: вопрос и часть с «вы» — о нас.
        (
            "Is the technical audit right for our 3 online stores?",
            "Thank you for the details: the technical audit is right for your 3 online stores.",
            False,
        ),
        (
            "I heard your technical audit is good for online stores.",
            "Thank you for the details: the technical audit is good for online stores.",
            False,
        ),
        ("Вы работаете с банками?", "Спасибо за вопрос: работаем с банками.", False),
        ("Слышал, что ваш аудит хорош.", "Спасибо, что рассказали: аудит хорош.", False),
        # Ссылка и вторая мысль о нас в том же предложении.
        (EN, f"Thank you for the details about your 3 stores: {CALL}.", False),
        (EN, "Thank you for the details about your 3 stores, we audit stores like yours.", False),
    ],
)  # fmt: skip
def test_a_sentence_is_theirs_only_in_their_own_words(
    letter: str, sentence: str, theirs: bool
) -> None:
    draft = f"Hello,\n{sentence} The audit covers indexing.\nSales desk"

    assert (sentence in judge_theirs.about_them(draft, letter)) is theirs


def test_what_the_correspondent_said_about_themselves() -> None:
    """Опора — части утверждений от первого лица без «вы»: не вопросы и не слова о нас."""
    letter = (
        "Thank you, we run 3 online stores, and I heard your audit is good. "
        "Our budget is 2,500 dollars. What does the audit include? We sell 1,200 items."
    )

    assert judge_theirs.told(letter).split("\n") == [
        " we run 3 online stores",
        "Our budget is 2,500 dollars.",
        "We sell 1,200 items.",
    ]


def test_the_judge_version_moved_with_the_rule() -> None:
    """Калибровка и прогон версии сравнивают черновики по версии судьи: правило меняет его
    вердикт — версия другая, чем у замеров v5, хотя промпт тот же."""
    assert judge.PROMPT_VERSION == "sales-judge-v6"
