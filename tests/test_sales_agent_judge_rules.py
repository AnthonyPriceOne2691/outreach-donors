"""Детерминированные проверки судьи продаж — срез 3.2a, шаг 5 (Spec 3.3, A1–A4).

Чистые функции, без базы и модели. Проверяется то, чего не видно по зелёному
прогону: сумма не из базы не проходит (J1), два призыва не проходят (J2), чужая
ссылка не проходит (J3), черновик не на языке письма не проходит (J4) — и
чистый черновик проходит все правила разом (иначе «всё красное» тоже было бы
зелёным тестом).
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from backend.features.sales.agent import judge_rules
from backend.features.sales.agent.facts import Context
from backend.features.sales.agent.moves import Cta

CALL = "https://call.example.test/slot"
SITE = "https://site.example.test"
TG = "@sales_desk_chat"

CONTEXT = Context(
    kb={
        3: "Цены: Цены в письме не называем, предлагаем короткий созвон.",
        4: "Магазин: Трафик вырос в 3,7 раза за 41 день.",
    },
    links={"website": SITE, "call": CALL, "telegram": TG},
    cta=(Cta.CALL, CALL),
    move="price",
    language="ru",
)
LETTER = "Добрый день. Сколько стоит аудит для сайта на 120 страниц?"
CLEAN = (
    "Добрый день,\n"
    "Спасибо за вопрос. Цену для сайта на 120 страниц обсудим на коротком созвоне, "
    f"выберите удобное время: {CALL}. Для примера: у магазина трафик вырос в 3,7 раза.\n"
    "Отдел продаж"
)


SUM_500 = (
    "сумма 500 не из базы — суммы называем только из базы знаний, даже если собеседник назвал "
    "свою: уберите её или возьмите из фактов"
)


def broken(draft: str, *, letter: str = LETTER, context: Context = CONTEXT) -> list[str]:
    return judge_rules.violations(draft, incoming=letter, context=context)


def test_clean_draft_passes_every_rule() -> None:
    assert broken(CLEAN) == []


def test_j1_sum_that_is_not_in_the_base_is_blocked() -> None:  # J1 (3.3 A1)
    draft = CLEAN.replace("Цену для сайта на 120 страниц", "Аудит стоит $500, детали")

    assert broken(draft) == [SUM_500]


def test_j1_number_from_the_letter_or_the_base_passes_and_an_invented_one_does_not() -> None:
    assert broken(CLEAN) == []  # 120 — из письма, 3,7 — из базы
    invented = CLEAN.replace("в 3,7 раза", "в 5 раз")

    assert broken(invented) == [
        "число 5 не из базы и не из письма собеседника — уберите его или возьмите из фактов"
    ]


def test_sum_the_correspondent_named_is_still_not_ours() -> None:  # O1
    """Решение владельца 07.10: сумма с валютой — только из базы, даже из письма собеседника."""
    letter = "Наш бюджет — 500 $ в месяц. Сколько стоит аудит для сайта на 120 страниц?"
    draft = CLEAN.replace("Цену для сайта на 120 страниц", "В 500 $ в месяц уложимся, детали")

    assert broken(draft, letter=letter) == [SUM_500]
    assert broken(CLEAN, letter=letter) == []  # 120 без валюты — из письма, как раньше


def test_sum_named_by_the_base_passes() -> None:  # O1
    priced = replace(CONTEXT, kb={**CONTEXT.kb, 5: "Аудит: Технический аудит — 500 USD."})
    draft = CLEAN.replace("Цену для сайта на 120 страниц", "Аудит стоит $500, детали")

    assert broken(draft, context=priced) == []


def test_number_of_the_base_without_currency_does_not_back_a_sum() -> None:  # O1
    """«500 страниц» в базе — не цена: сумма 500 опоры в базе не имеет."""
    pages = replace(CONTEXT, kb={**CONTEXT.kb, 5: "Объём: Берём сайты до 500 страниц."})
    draft = CLEAN.replace("Цену для сайта на 120 страниц", "Аудит стоит $500, детали")

    assert broken(draft, context=pages) == [SUM_500]


def test_j2_two_calls_to_action_are_blocked() -> None:  # J2 (3.3 A2)
    draft = CLEAN.replace(
        "Для примера: у магазина трафик вырос в 3,7 раза.", "Или просто ответьте на это письмо."
    )

    assert broken(draft) == ["призывов 2 (на созвон, ответить письмом) — нужен один: на созвон"]


def test_j3_link_not_from_the_sender_settings_is_blocked() -> None:  # J3 (3.3 A3)
    draft = CLEAN.replace(CALL, "https://pay.other.example.test/now")

    assert broken(draft) == [
        "ссылка не из настроек отправителя: https://pay.other.example.test/now — оставьте "
        "только сайт, созвон или Telegram из настроек",
        f"призыв без ссылки из настроек — нужна эта: {CALL}",
    ]


def test_j3_whitelisted_link_passes_whatever_its_case_and_slash() -> None:
    draft = CLEAN.replace(CALL, "HTTPS://Call.Example.test/slot/")

    assert broken(draft) == []


def test_j3_address_in_the_draft_is_blocked_too() -> None:
    draft = CLEAN.replace("Отдел продаж", "Пишите на desk@other.example.test\nОтдел продаж")

    assert "ссылка не из настроек отправителя: desk@other.example.test" in broken(draft)[0]


def test_j4_draft_not_in_the_letters_language_is_blocked() -> None:  # J4 (3.3 A4)
    english = "Hello. How much is an audit for a 120 page site?"

    assert broken(CLEAN, letter=english) == [
        "язык черновика — ru, а письма собеседника — en: пишите на языке письма"
    ]


@pytest.mark.parametrize(
    ("draft", "problem"),
    [
        (f"Цену обсудим на созвоне: {CALL}.", "предложений 1, нужно от 2 до 5"),
        (CLEAN.replace("Спасибо за вопрос.", "Спасибо! Вопрос хороший."), "без «!»"),
        (CLEAN.replace("Спасибо за вопрос.", "Спасибо за вопрос 🙂."), "без эмодзи"),
        (
            CLEAN.replace("Спасибо за вопрос.", "Раз. Два. Три. Четыре. Пять."),
            "предложений 7, нужно от 2 до 5",
        ),
    ],
)
def test_form_two_to_five_sentences_no_exclamation_no_emoji(draft: str, problem: str) -> None:
    assert any(problem in found for found in broken(draft))


def test_deferral_already_said_in_the_thread_is_not_repeated() -> None:  # A6
    draft = CLEAN.replace(
        "Для примера: у магазина трафик вырос в 3,7 раза.", "Кейс пришлю на днях."
    )
    said = replace(CONTEXT, deferred=("Пришлю кейс на днях.",))

    assert broken(draft) == []  # первая отсрочка — можно
    assert broken(draft, context=said) == [
        "отсрочка уже была: «Пришлю кейс на днях.» — не откладывайте снова («Кейс пришлю "
        "на днях.»): выполните обещание фактом из базы"
    ]


@pytest.mark.parametrize(
    ("draft", "cta", "problem"),
    [
        (
            CLEAN.replace(
                f"обсудим на коротком созвоне, выберите удобное время: {CALL}",
                f"подскажем в Telegram: {TG}",
            ),
            (Cta.CALL, CALL),
            "призыв не тот: в Telegram вместо на созвон",
        ),
        (
            CLEAN.replace(f", выберите удобное время: {CALL}", ""),
            (Cta.CALL, CALL),
            f"призыв без ссылки из настроек — нужна эта: {CALL}",
        ),
        (CLEAN, None, "ход без призыва, а черновик зовёт: на созвон — уберите призыв"),
        (
            "Понял, спасибо. Хорошего дня.",
            (Cta.TELEGRAM, TG),
            f"нет призыва — позовите в Telegram этой ссылкой: {TG}",
        ),
    ],
)
def test_call_to_action_is_the_one_the_move_chose(
    draft: str, cta: tuple[Cta, str] | None, problem: str
) -> None:
    context = replace(CONTEXT, cta=cta)

    assert problem in broken(draft, context=context)
