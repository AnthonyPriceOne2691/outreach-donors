"""Пустые разделы базы знаний — словами, на каждом языке писем: чего агент не скажет.

Агент работает и при пустых кейсах или ценах: промпт велит не называть цен, судья ловит
выдуманное. Но экран об этом молчал — и человек не знал, что агент на английском не приведёт
ни одного примера. Здесь — строка у каждого пустого раздела нужного языка (языки цепочек писем,
`chain_text.LANGUAGES`): что агент сделает без него.

**Правило — агента, не своё** (`agent/facts.py`): запись берётся на языке письма (`kb.speaks`),
а нет записей вида на нём — на любом, и писатель переведёт. Поэтому пустой раздел называется
по-разному: «на английском нет — агент возьмёт записи на русском и переведёт» и «на английском
нет — агент не приведёт ни одного примера», когда их нет ни на одном языке. Считаются только
включённые записи: выключенную агент не видит.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from backend.features.sales.chain_text import LANGUAGES
from backend.features.sales.kb import Fact, speaks
from backend.features.sales.models import KbKind

#: Язык письма словами — после «на»: «на английском».
IN_LANGUAGE = {"ru": "на русском", "en": "на английском"}

#: Вид записи — словами «чего нет» и что агент сделает без неё.
WITHOUT: dict[KbKind, tuple[str, str]] = {
    KbKind.BRIEF: ("Записей «о компании»", "агент пишет без фона: кто мы и что делаем"),
    KbKind.SERVICE: ("Услуг", "на вопрос об услугах агенту нечего ответить фактами"),
    KbKind.CASE: ("Кейсов", "агент не приведёт ни одного примера"),
    KbKind.OBJECTION: ("Возражений", "на возражение агент ответит без заготовленного ответа"),
    KbKind.PRICE_POLICY: ("Записей о ценах", "агент цен не назовёт и позовёт на созвон"),
    KbKind.FORBIDDEN: ("Записей «нельзя»", "агент не знает, чего не обещать, сверх правил судьи"),
    KbKind.CTA: ("Призыва", "агент позовёт по ссылке из «Отправителя» своими словами"),
}


@dataclass(frozen=True, slots=True)
class Gap:
    """Пустой раздел на языке: вид, язык, на каких языках записи вида есть, и слова."""

    kind: KbKind
    language: str
    #: Языки, на которых записи этого вида есть: их агент переведёт. Пусто — нет ни на одном.
    elsewhere: tuple[str, ...]
    words: str


def _in(code: str) -> str:
    """«на английском» — и у кода с регионом (`en-us`): язык записи тот же."""
    base = code.partition("-")[0]
    return IN_LANGUAGE.get(code) or IN_LANGUAGE.get(base) or f"на языке {code}"


def _gap(kind: KbKind, language: str, of_kind: Sequence[Fact]) -> Gap:
    what, without = WITHOUT[kind]
    elsewhere = tuple(sorted({fact.language for fact in of_kind}))
    if elsewhere:
        others = dict.fromkeys(_in(code).removeprefix("на ") for code in elsewhere)
        without = f"агент возьмёт записи на {', '.join(others)} и переведёт"
    return Gap(kind, language, elsewhere, f"{what} {_in(language)} нет — {without}")


def gaps(found: Sequence[Fact]) -> list[Gap]:
    """Пустые разделы по языкам писем — в порядке языков и видов набора."""
    empty: list[Gap] = []
    for language in LANGUAGES:
        for kind in KbKind:
            of_kind = [fact for fact in found if fact.kind is kind]
            if not any(speaks(fact, language) for fact in of_kind):
                empty.append(_gap(kind, language, of_kind))
    return empty
