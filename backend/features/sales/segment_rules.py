"""Таблица правил судьи сегмента v4: полосу ставит код по фактам модели и полю гипотезы.

Модель отдаёт факты о сайте с дословными цитатами (`segment_facts`), описание сегмента разобрано один
раз (`segment_scope`), поле «страны компаний» — данные гипотезы (`hypothesis_countries`). Правила ниже
идут по порядку, решает первое сработавшее; у каждого исхода — свои слова для человека. Правила общие
для любых ниш, стран и языков: ни ниши, ни страны, ни языка здесь нет — каким странам не пишем и что
делать с чужой географией, задаёт гипотеза.

Первые три правила решаются до модели (`segment_judge.decide`): правило по домену и пустой текст —
до главной и модели, язык сайта — по буквам, до модели. Остальные — по фактам; факты лежат в кэше
вердиктов, и правка поля гипотезы пересчитывает полосу без модели (пересуд).

Страна бизнеса — из текста сайта (адрес, телефон, валюта — с цитатой); текст её не показал — по
доменной зоне (`zone_country`). Зона — только подсказка о стране бизнеса: «мест в тексте не видно»
решает текст, а не зона.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from functools import cached_property
from typing import Protocol

from backend.features.sales.hypothesis_countries import CountryPolicy, OtherCountries
from backend.features.sales.segment_facts import SiteFacts
from backend.features.sales.segment_models import Decider, Lane
from backend.features.sales.segment_scope import Scope, country_words
from backend.features.sales.zone_country import country_of_host

#: Страна сегмента названа как рынок сайта: строка сайта называет не больше стольких стран.
MARKET_MAX = 3
#: Виды сайта, которые подходят, только если сегмент сам их просит.
KIND_WORDS = {
    "intermediary": "посредник — каталог, биржа, площадка или агрегатор чужих компаний",
    "media": "издание — пишет о чужих компаниях",
    "other": "не компания — госорган, школа, ассоциация или конструктор сайтов",
}
SCOPE_FAILED = (
    "описание сегмента не разобрано — судья не сверил место сайта; следующий проход спросит снова"
)
#: «Не понять» — словами того, что видел судья: текст сайта целиком — или один сниппет выдачи, когда
#: главная не открылась (`SiteFacts.snippet_only`): тогда «не видно» честно и решает человек.
UNCLEAR = "по тексту не видно, чем занят сайт, — решает человек"
SNIPPET_ONLY = "главная сайта не открылась — судья видел только сниппет выдачи; решает человек"


class Early(Protocol):
    """Вердикт до модели — правила по домену и тексту или языка сайта: полоса и слова."""

    @property
    def lane(self) -> Lane: ...

    @property
    def reason(self) -> str: ...


@dataclass(frozen=True)
class Case:
    """Всё, по чему решает таблица: правило до модели, язык сайта, факты модели (или почему их нет),
    разбор описания и поле гипотезы."""

    host: str
    policy: CountryPolicy = field(default_factory=CountryPolicy)
    scope: Scope | None = None
    facts: SiteFacts | None = None
    #: Почему фактов нет — словами (модель не ответила, ответ не разобран, цитата не нашлась).
    failure: str = ""
    ruled: Early | None = None
    language: Early | None = None

    @property
    def known(self) -> SiteFacts:
        """Факты — правила после «сбой модели» без них не спрашиваются."""
        assert self.facts is not None
        return self.facts

    @cached_property
    def segment(self) -> frozenset[str]:
        return self.scope.countries if self.scope is not None else frozenset()

    @cached_property
    def home(self) -> str:
        """Страна бизнеса: по тексту, иначе по доменной зоне; пусто — не видна."""
        return self.known.home or country_of_host(self.host)

    @cached_property
    def places(self) -> frozenset[str]:
        """Страны, где бизнес работает по тексту, и страна бизнеса — если её показал текст."""
        return self.known.places | ({self.known.home} if self.known.home else frozenset())

    @cached_property
    def market(self) -> bool:
        """Страна сегмента — страна бизнеса или рынок сайта (строка называет не больше трёх стран)."""
        if self.home in self.segment:
            return True
        return any(
            set(place.countries) & self.segment and len(place.countries) <= MARKET_MAX
            for place in self.known.served
        )

    @property
    def foreign(self) -> frozenset[str]:
        return self.places - self.segment


@dataclass(frozen=True, slots=True)
class Rule:
    """Строка таблицы: имя, условие, полоса и слова для человека."""

    name: str
    applies: Callable[[Case], bool]
    lane: Lane
    words: Callable[[Case], str]


def _early(found: Early | None, lane: Lane) -> bool:
    return found is not None and found.lane is lane


def _early_words(found: Early | None) -> str:
    return found.reason if found is not None else ""


def _seen(case: Case) -> bool:
    """Модель видит, что за сайт: при виде «не понять» её «другое дело» и «смежное» — не отказ, а
    противоречие ответа — сайт идёт к человеку правилом «не понять»."""
    return case.known.kind != "unclear"


def _unwanted(case: Case) -> bool:
    kind = case.known.kind
    return kind in KIND_WORDS and (case.scope is None or kind not in case.scope.kinds)


def _home_words(case: Case) -> str:
    origin = "по тексту сайта" if case.known.home else "по доменной зоне"
    return f"страна бизнеса — {country_words({case.home})} ({origin})"


def _foreign_words(case: Case) -> str:
    """Чем география сайта чужая: страна сегмента — лишь одна из многих у бизнеса из другой страны;
    видны только другие страны; или сайт говорит лишь «по всему миру» (живой проход 20.10: слова
    «видна только чужая география» стояли и там, где страна сегмента в списке есть)."""
    if case.places & case.segment:
        return f"{_home_words(case)}; страна сегмента — лишь одна из многих, где он работает"
    if case.foreign:
        world = " и «по всему миру»" if case.known.world else ""
        return f"видна только чужая география — {country_words(case.foreign)}{world}"
    return "сайт работает «по всему миру» — страна сегмента отдельно не названа"


def _excluded(case: Case) -> bool:
    return bool(case.home) and case.home in case.policy.excluded


def _only_elsewhere(case: Case) -> bool:
    return bool(case.known.only) and not (case.places & case.segment) and not case.market


def _local_elsewhere(case: Case) -> bool:
    away = bool(case.foreign) or (bool(case.home) and case.home not in case.segment)
    return case.known.local and away


def _no_places(case: Case) -> bool:
    return not case.places and not case.known.world


def _among(case: Case) -> bool:
    """Страна сегмента — в длинном списке мест или в группе стран («по всей Европе»): модель
    раскрывает группу в страны, и страна сегмента попадает туда сама. Поэтому так — только когда
    страна бизнеса не видна: бизнес из другой страны «для клиентов по всей Европе» — чужая география."""
    return bool(case.places & case.segment) and not case.home


def _market_words(case: Case) -> str:
    seen = case.segment & (case.places | {case.home})
    return f"страна сегмента — среди мест работы сайта ({country_words(seen)})"


def _among_words(case: Case) -> str:
    seen = country_words(case.places & case.segment)
    return f"страна сегмента — среди многих мест работы сайта ({seen})"


#: Таблица по порядку: решает первое сработавшее правило.
RULES: tuple[Rule, ...] = (
    Rule(
        "domain",
        lambda c: _early(c.ruled, Lane.REJECT),
        Lane.REJECT,
        lambda c: _early_words(c.ruled),
    ),
    Rule(
        "no_text",
        lambda c: _early(c.ruled, Lane.REVIEW),
        Lane.REVIEW,
        lambda c: _early_words(c.ruled),
    ),
    Rule(
        "language",
        lambda c: _early(c.language, Lane.REJECT),
        Lane.REJECT,
        lambda c: _early_words(c.language),
    ),
    Rule("model_failed", lambda c: c.facts is None, Lane.REVIEW, lambda c: c.failure),
    Rule("scope_failed", lambda c: c.scope is None, Lane.REVIEW, lambda _c: SCOPE_FAILED),
    Rule(
        "kind_unwanted",
        _unwanted,
        Lane.REJECT,
        lambda c: f"{KIND_WORDS[c.known.kind]}: сегмент таких сайтов не просит",
    ),
    Rule(
        "other_business",
        lambda c: c.known.core == "no" and _seen(c),
        Lane.REJECT,
        lambda _c: "другая деятельность — не то, что ищет сегмент",
    ),
    Rule(
        "adjacent",
        lambda c: c.known.core == "adjacent" and _seen(c),
        Lane.REJECT,
        lambda _c: "смежная деятельность — не то, что ищет сегмент",
    ),
    Rule(
        "network_excluded",
        lambda c: c.known.network and c.scope is not None and c.scope.excludes_networks,
        Lane.REJECT,
        lambda _c: "региональный сайт крупной международной компании: описание таких не берёт",
    ),
    Rule(
        "excluded_country",
        _excluded,
        Lane.REJECT,
        lambda c: f"{_home_words(c)}: гипотеза не пишет компаниям оттуда",
    ),
    Rule(
        "language_unnamed",
        lambda c: _early(c.language, Lane.REVIEW),
        Lane.REVIEW,
        lambda c: _early_words(c.language),
    ),
    Rule(
        "unclear",
        lambda c: c.known.core == "unsure" or c.known.kind == "unclear",
        Lane.REVIEW,
        lambda c: SNIPPET_ONLY if c.known.snippet_only else UNCLEAR,
    ),
    Rule(
        "segment_anywhere",
        lambda c: not c.segment,
        Lane.ACCEPT,
        lambda _c: "дело сайта — то, что ищет сегмент; места сегмент не называет",
    ),
    Rule(
        "only_elsewhere",
        _only_elsewhere,
        Lane.REJECT,
        lambda c: f"сайт работает только в другом месте: «{c.known.only}»",
    ),
    Rule("segment_market", lambda c: c.market, Lane.ACCEPT, _market_words),
    Rule("segment_among", _among, Lane.ACCEPT, _among_words),
    Rule(
        "local_elsewhere",
        _local_elsewhere,
        Lane.REJECT,
        lambda _c: "местный бизнес в другой стране: работает только у себя — сегменту не подходит",
    ),
    Rule(
        "no_places",
        _no_places,
        Lane.ACCEPT,
        lambda _c: "мест работы в тексте не видно — это не отказ: дело сайта то, что ищет сегмент",
    ),
    Rule(
        "foreign_review",
        lambda c: c.policy.other is OtherCountries.REVIEW,
        Lane.REVIEW,
        lambda c: f"{_foreign_words(c)}: по настройке гипотезы решает человек",
    ),
    Rule(
        "foreign_accept",
        lambda _c: True,
        Lane.ACCEPT,
        lambda c: f"{_foreign_words(c)}: гипотеза берёт компании из других стран",
    ),
)
#: Решили до модели: правило — по домену и тексту, язык — по буквам сайта.
BY_RULE = frozenset({"domain", "no_text"})
BY_LANGUAGE = frozenset({"language", "language_unnamed"})


def with_lane(rules: tuple[Rule, ...], name: str, lane: Lane) -> tuple[Rule, ...]:
    """Та же таблица, но правило `name` ставит другую полосу — для замера вариантов (eval)."""
    return tuple(replace(rule, lane=lane) if rule.name == name else rule for rule in rules)


def first(case: Case, rules: tuple[Rule, ...] = RULES) -> Rule:
    """Первое сработавшее правило таблицы; последнее правило срабатывает всегда."""
    return next(rule for rule in rules if rule.applies(case))


def decider(rule: Rule) -> Decider:
    """Кто решил по правилу: до модели — правило или язык; остальное — модель фактами."""
    if rule.name in BY_RULE:
        return Decider.RULE
    if rule.name in BY_LANGUAGE:
        return Decider.LANGUAGE
    return Decider.MODEL
