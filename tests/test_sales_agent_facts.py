"""Факты под ход и чтение текста — срез 3.2a, шаг 3.

Проверяется то, чего не видно по зелёному прогону: выборка базы берёт виды хода,
язык письма и теги, а не всю базу; призыв — первая заданная ссылка хода, а не
выдуманная; строки брифа читаются назад тем же модулем, что их пишет (судья
видит то, что видел писатель); язык и отсрочку читает код по правилу, а не модель.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from backend.features.sales.agent import facts, moves, reading
from backend.features.sales.agent.moves import Cta
from backend.features.sales.agent.situation import Label, Situation
from backend.features.sales.kb import Fact
from backend.features.sales.models import KbKind
from backend.features.sales.sender import FIELDS, Sender

CALL = "https://call.example.test/slot"
SITE = "https://site.example.test"


def sender(**values: str) -> Sender:
    return Sender({**dict.fromkeys(FIELDS), **values})


BASE = (
    Fact(1, KbKind.BRIEF, "ru", "Кто мы", "Делаем аудит сайтов.", ()),
    Fact(2, KbKind.BRIEF, "en", "Who we are", "We audit websites.", ()),
    Fact(3, KbKind.PRICE_POLICY, "ru", "Цены", "Цены не называем, предлагаем созвон.", ()),
    Fact(4, KbKind.CASE, "en", "Shop", "Traffic grew 3.7 times in 41 days.", ("seo",)),
    Fact(5, KbKind.CASE, "en", "Clinic", "Leads doubled.", ("ads",)),
    Fact(6, KbKind.SERVICE, "ru", "Аудит", "Аудит за 9 рабочих дней.", ("seo",)),
    Fact(7, KbKind.FORBIDDEN, "ru", "Нельзя", "Не обещать позиции в поиске.", ()),
)


def selected(label: Label, **known: object) -> facts.Selected:
    situation = replace(Situation(label=label, confidence=0.9), **known)
    return facts.select(
        BASE,
        move=moves.table().move(label),
        situation=situation,
        language="ru",
        sender=sender(sender_name="Менеджер", call_link=CALL, website=SITE),
    )


def test_price_move_takes_the_policy_and_the_call_link() -> None:  # A1 (3.2)
    found = selected(Label.ASKS_PRICE)

    assert found.kb_ids == (1, 7, 3)  # фон и запреты — каждому письму, затем ход
    assert found.cta is Cta.CALL
    assert f"[cta call] {CALL}" in found.lines
    assert not any(reading.amounts_in(line) for line in found.lines)  # сумм в базе нет


def test_language_of_the_letter_first_any_language_when_the_kind_has_none() -> None:
    found = selected(Label.ASKS_INFO)

    # бриф и услуга — по-русски; кейсов на русском нет — английские
    assert found.kb_ids == (1, 7, 6, 4, 5)


def test_tags_narrow_the_kinds_that_take_tags() -> None:
    found = selected(Label.ASKS_INFO, tags=("seo",))

    assert found.kb_ids == (1, 7, 6, 4)  # кейс «ads» отсеян, фон и запреты — нет


def test_promise_brings_its_kind_even_when_the_move_does_not_take_it() -> None:  # A6
    found = selected(Label.ASKS_PRICE, promised=(KbKind.CASE,))

    assert found.kb_ids == (1, 7, 4, 5, 3)
    assert "[promised] case" in found.lines


def test_cta_is_the_first_link_the_sender_has() -> None:
    talk = moves.table().move(Label.WANTS_TO_TALK)  # Telegram, затем созвон
    situation = Situation(label=Label.WANTS_TO_TALK, confidence=0.9)

    only_call = facts.select(
        BASE, move=talk, situation=situation, language="ru", sender=sender(call_link=CALL)
    )
    none = facts.select(BASE, move=talk, situation=situation, language="ru", sender=sender())

    assert (only_call.cta, only_call.no_cta_link) == (Cta.CALL, False)
    assert (none.cta, none.no_cta_link) == (None, True)
    assert not any(line.startswith("[cta") for line in none.lines)


def test_close_move_calls_nowhere() -> None:
    found = selected(Label.NOT_NOW)

    assert (found.cta, found.no_cta_link) == (None, False)
    assert "[move close]" in " ".join(found.lines)


def test_lines_read_back_as_the_judge_sees_them() -> None:
    found = selected(Label.ASKS_PRICE, promised=(KbKind.CASE,))
    lines = (*found.lines, *facts.deferred_lines(["Пришлю кейс на днях."]))

    seen = facts.read((*lines, "строка без метки", "[kb:x брак] не читается"))

    assert set(seen.kb) == {1, 3, 4, 5, 7}
    assert seen.kb[3] == "Цены: Цены не называем, предлагаем созвон."
    assert seen.links == {"website": SITE, "call": CALL}
    assert (seen.cta, seen.move, seen.language) == ((Cta.CALL, CALL), "price", "ru")
    assert seen.deferred == ("Пришлю кейс на днях.",)
    assert seen.persona == "Менеджер"


def test_long_record_is_cut_and_many_records_are_capped() -> None:
    many = tuple(
        Fact(100 + n, KbKind.CASE, "ru", f"Кейс {n}", "Текст. " * 900, ()) for n in range(40)
    )
    situation = Situation(label=Label.ASKS_INFO, confidence=0.9)

    found = facts.select(
        many,
        move=moves.table().move(Label.ASKS_INFO),
        situation=situation,
        language="ru",
        sender=sender(call_link=CALL),
    )

    assert len(found.kb_ids) == facts.MAX_FACTS
    kb_lines = [line for line in found.lines if line.startswith("[kb:")]
    assert max(len(line) for line in kb_lines) < facts.MAX_FACT_CHARS + 60


# --- чтение текста ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "language"),
    [
        ("Сколько стоит аудит? Напишите на boss@lead.example.test", "ru"),
        ("How much is it? See https://ru.example.test/страница", "en"),
        ("Grüße, wie viel kostet es?", "en"),  # латиница — en, как в Spec 3.3
        ("この監査はいくらですか", None),
        ("12345 !!!", None),
        ("", None),
    ],
)
def test_language_is_read_by_the_alphabet(text: str, language: str | None) -> None:  # A7
    assert reading.language_of(text) == language


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ("Спасибо! Пришлю кейс на днях. Хорошего дня.", ["Пришлю кейс на днях."]),
        ("Вернусь к вам с ответом", ["Вернусь к вам с ответом"]),
        ("Thanks. I'll send the case study later.", ["I'll send the case study later."]),
        ("We will get back to you.", ["We will get back to you."]),
        ("Пришлю кейс: вот он. Напишите, если нужно ещё.", []),  # обещание без «позже»
        ("Please send it later.", []),  # просят нас, а не обещаем мы
    ],
)
def test_deferral_is_a_promise_to_send_or_come_back_later(text: str, found: list[str]) -> None:
    assert reading.deferrals(text) == found


def test_sentences_end_with_a_mark_and_links_do_not_break_them() -> None:
    text = (
        "Добрый день,\nЦены — на созвоне: https://call.example.test/a.b?x=1. Выберите время.\nИмя"
    )

    pieces = reading.sentences(text)

    assert [piece for piece in pieces if reading.ended(piece)] == [
        "Цены — на созвоне: https://call.example.test/a.b?x=1.",
        "Выберите время.",
    ]


def test_links_are_found_and_compared_without_scheme_and_case() -> None:
    text = "Сайт https://Site.example.test/, чат @lead_chat и t.me/other_chat, почта a.b@lead.example.test."

    assert reading.links_in(text) == [
        "https://Site.example.test/",
        "@lead_chat",
        "t.me/other_chat",
        "a.b@lead.example.test",
    ]
    assert reading.normalized("https://Site.example.test/") == reading.normalized(SITE)
    assert reading.normalized("@lead_chat") == reading.normalized("https://t.me/lead_chat")


def test_sender_without_name_has_no_persona_line() -> None:
    found = facts.select(
        BASE,
        move=moves.table().move(Label.NOT_NOW),
        situation=Situation(label=Label.NOT_NOW, confidence=0.9),
        language="ru",
        sender=sender(),
    )

    assert not any(line.startswith("[persona]") for line in found.lines)
    assert facts.read(found.lines).persona is None
