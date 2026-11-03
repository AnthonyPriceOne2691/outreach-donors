"""Совет судьи рекламодателей — таблицей правил по фактам, а не словом модели.

Модель называет факты с цитатами (`judge_facts.py`), а совет «пишем», «не пишем» или «не понять»
ставит код: первое правило таблицы, которое подошло. Так правка политики — правка этой таблицы и
`RULES_VERSION`, а не подсказки: пересчёт идёт по сохранённым фактам без модели
(`judge_store.relabel`), и токены за уже известное не платятся (урок судьи продаж, #440).

Таблица — форма первой версии; калибруется она на эталоне, до него судья работает в тени. Каждое
правило опирается только на доказанное: роль ссылки — на цитату статьи, вид сайта — на цитату
главной, раскрытие рекламы — само цитата. То, что модель знает о домене помимо текста, совета не
ставит: «некоммерческих опознаёт знание, а не текст» (`okf/selection-audit.md`) — такой случай
остаётся человеку.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from backend.features.crawl.judge_facts import LinkFacts

#: Версия таблицы. Меняется при КАЖДОЙ правке правил: совет строки суда помнит её, и пересчёт
#: (`judge_store.relabel`) ставит совет заново по фактам строки.
RULES_VERSION = "advertiser-rules-v1"


class Advice(StrEnum):
    """Совет судьи: писать ли домену как рекламодателю."""

    WRITE = "write"
    SKIP = "skip"
    UNCLEAR = "unclear"


#: Совет словами — как на экране решения человека.
ADVICE_WORDS = {Advice.WRITE: "пишем", Advice.SKIP: "не пишем", Advice.UNCLEAR: "не понять"}
#: Сайт не бизнес, а издание, госорган или личная страница: без раскрытия рекламы он не покупал.
NOT_A_BUSINESS = frozenset({"public", "personal", "media"})
#: Роль ссылки, которая продаёт: реклама или строка подборки предложений.
SELLING_ROLES = frozenset({"promotes", "listing"})


@dataclass(frozen=True, slots=True)
class Rule:
    """Строка таблицы: ключ, совет, слова человеку и условие по фактам."""

    key: str
    advice: Advice
    words: str
    applies: Callable[[LinkFacts], bool]


def _unproven(facts: LinkFacts) -> bool:
    return not facts.link_proven and not facts.target_proven


def _not_a_business(facts: LinkFacts) -> bool:
    return facts.target_proven and facts.target_kind in NOT_A_BUSINESS and not facts.disclosure


def _citation(facts: LinkFacts) -> bool:
    return facts.link_proven and facts.link_role == "cites" and not facts.disclosure


def _paid(facts: LinkFacts) -> bool:
    sold = facts.link_proven and facts.link_role in SELLING_ROLES
    return facts.target_proven and facts.target_kind == "sells" and (sold or bool(facts.disclosure))


#: Таблица правил, первое совпадение. Последняя строка подходит всегда.
RULES: tuple[Rule, ...] = (
    Rule("unproven", Advice.UNCLEAR, "факты не доказаны цитатами", _unproven),
    Rule(
        "not_a_business",
        Advice.SKIP,
        "сайт домена — издание, госорган или личная страница, раскрытия рекламы нет",
        _not_a_business,
    ),
    Rule("citation", Advice.SKIP, "ссылка — источник факта, раскрытия рекламы нет", _citation),
    Rule(
        "paid",
        Advice.WRITE,
        "домен продаёт своё, а ссылка его рекламирует, стоит в подборке предложений или под "
        "раскрытием рекламы",
        _paid,
    ),
    Rule("rest", Advice.UNCLEAR, "по фактам не решить — решает человек", lambda _facts: True),
)


@dataclass(frozen=True, slots=True)
class Ruled:
    """Совет по фактам: какое правило его поставило и его слова."""

    advice: Advice
    rule: str
    words: str


def advise(facts: LinkFacts, rules: tuple[Rule, ...] = RULES) -> Ruled:
    """Первое правило таблицы, которое подошло к фактам."""
    for rule in rules:
        if rule.applies(facts):
            return Ruled(rule.advice, rule.key, rule.words)
    raise AssertionError("последнее правило таблицы подходит всегда")  # pragma: no cover
