"""Поле гипотезы «вид сайта»: какие виды сайтов гипотезе подходят — данные гипотезы, а не правило судьи.

Судья сегмента v4 называет вид сайта фактом (`segment_facts.KINDS`: свой сайт компании, посредник,
СМИ, другое, не видно). Доработка «План работ» (решение владельца 16.11): «изменить качество сайтов» —
это и вид сайта, его выбирает человек. Колонку заводит срез данных плана; правило полосы по ней
(`segment_rules.py`) и окно — срез вкладки «Гипотезы».

**Пусто — как сейчас**: виды сайтов решает описание сегмента и судья. Колонка — миксином, как поле
«страны компаний» (`hypothesis_countries.py`), и оба поля окна гипотезы входят в модель одним
миксином (`HypothesisFieldsMixin`): модель гипотезы живёт в `models.py` у предела длины.

**Виды — те, что различает судья**, своих здесь нет: виды, которые может просить описание сегмента
(`segment_scope.KINDS`), они же — факт модели о сайте (`segment_facts.KINDS`) без «не видно». «Не
видно» не выбирается: такой сайт решает человек при любом поле. Список — здесь, а не импортом: разбор
описания тянет реестр моделей, а эту колонку реестр грузит сам (круг импорта). Совпадение держит тест.
Выбор правится окном «Качество сайтов» плана (`plan_actions.change_quality` → `hypotheses.edit`) и
действует на новые суды: собранное правка не пересуживает.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.sales.hypothesis_countries import HypothesisCountriesMixin

logger = logging.getLogger(__name__)

#: Длина кода вида — как у кодов полос и этапов модуля.
KIND_LENGTH = 16
#: Виды, которые выбирает человек, по порядку выбора (шапка модуля).
SITE_KINDS = ("own", "intermediary", "media", "other")
#: Вид словами — журнал правки, слова судьи и окно.
KIND_WORDS = {
    "own": "сайт самой компании",
    "intermediary": "каталог, биржа, площадка",
    "media": "издание или блог",
    "other": "не компания — госорган, школа, ассоциация",
}
#: Поле пусто — словами журнала и судьи.
ANY_KIND = "как просит описание сегмента"


class SiteKindsMixin:
    """Колонка «вид сайта» у гипотезы: коды видов, которые проходят; пусто — как сейчас."""

    site_kinds: Mapped[list[str]] = mapped_column(
        ARRAY(String(KIND_LENGTH)), nullable=False, default=list, server_default=text("'{}'")
    )


class HypothesisFieldsMixin(HypothesisCountriesMixin, SiteKindsMixin):
    """Поля окна гипотезы, кроме имени и описания: страны компаний и вид сайта."""


class SiteKindError(ValueError):
    """Вид сайта не из выбора. Текст — почему."""


def checked_kinds(values: Iterable[str]) -> list[str]:
    """Виды правки — строчными, по порядку выбора, без повторов; не из выбора — отказ словами."""
    found = {value.strip().lower() for value in values}
    unknown = sorted(found - set(SITE_KINDS))
    if unknown:
        allowed = ", ".join(SITE_KINDS)
        raise SiteKindError(f"вида сайта «{unknown[0][:KIND_LENGTH]}» нет: нужен один из {allowed}")
    return [kind for kind in SITE_KINDS if kind in found]


def kinds_of(values: Iterable[str] | None) -> frozenset[str]:
    """Поле из колонки — глазами судьи. Вид, которого код не знает (строку правили руками), не
    считается, громко в лог: поле из одних незнакомых видов иначе отклоняло бы каждый сайт."""
    found = frozenset(value.strip().lower() for value in values or () if value.strip())
    unknown = found - set(SITE_KINDS)
    if unknown:
        logger.warning(
            "продажи: у гипотезы неизвестный вид сайта", extra={"kinds": sorted(unknown)[:4]}
        )
    return found - unknown


def kinds_words(kinds: Iterable[str]) -> str:
    """Поле словами — для журнала и судьи: виды по порядку выбора; пусто — «как просит описание»."""
    chosen = set(kinds)
    said = [KIND_WORDS[kind] for kind in SITE_KINDS if kind in chosen]
    return "; ".join(said) if said else ANY_KIND
