"""Поле гипотезы «вид сайта»: какие виды сайтов гипотезе подходят — данные гипотезы, а не правило судьи.

Судья сегмента v4 называет вид сайта фактом (`segment_facts.KINDS`: свой сайт компании, посредник,
СМИ, другое, не видно). Доработка «План работ» (решение владельца 16.11): «изменить качество сайтов» —
это и вид сайта, его выбирает человек. Колонку заводит срез данных плана; правило полосы по ней
(`segment_rules.py`) и окно — срез вкладки «Гипотезы».

**Пусто — как сейчас**: виды сайтов решает описание сегмента и судья. Колонка — миксином, как поле
«страны компаний» (`hypothesis_countries.py`), и оба поля окна гипотезы входят в модель одним
миксином (`HypothesisFieldsMixin`): модель гипотезы живёт в `models.py` у предела длины.
"""

from __future__ import annotations

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.sales.hypothesis_countries import HypothesisCountriesMixin

#: Длина кода вида — как у кодов полос и этапов модуля.
KIND_LENGTH = 16


class SiteKindsMixin:
    """Колонка «вид сайта» у гипотезы: коды видов, которые проходят; пусто — как сейчас."""

    site_kinds: Mapped[list[str]] = mapped_column(
        ARRAY(String(KIND_LENGTH)), nullable=False, default=list, server_default=text("'{}'")
    )


class HypothesisFieldsMixin(HypothesisCountriesMixin, SiteKindsMixin):
    """Поля окна гипотезы, кроме имени и описания: страны компаний и вид сайта."""
