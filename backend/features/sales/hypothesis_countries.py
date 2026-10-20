"""Поле гипотезы «страны компаний»: какие страны бизнеса подходят гипотезе — данные гипотезы, а не
правило судьи.

Судья сегмента v4 берёт из текста сайта страну самого бизнеса (адрес, телефон, валюта; не видна —
доменная зона), а полосу ставит код (`segment_rules.py`) по двум колонкам гипотезы — решения
владельца 20.10.2026:

- **«Компании из других стран»** (`other_countries`): сегмент называет страну или регион, а на сайте
  видна только другая география. `review` — решает человек («на проверку»; так у каждой новой
  гипотезы), `accept` — такие компании гипотезе подходят.
- **«Не пишем компаниям из»** (`excluded_countries`): коды ISO 3166-1 alpha-2. Компания из такой
  страны отклоняется при любом положении переключателя.

Стран «по умолчанию» нет ни в коде, ни в базе: пустой список — не пишем всем, кто подходит.
Колонки — миксином (`HypothesisCountriesMixin`): модель гипотезы живёт в `models.py`, а он у предела
длины. Коды — заглавными, как в ISO 3166-1 и в ответе модели; годный код — тот, что есть в таблице
стран модуля (`geo.COUNTRIES`, CLDR), — своего списка стран здесь нет.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache

from sqlalchemy import String, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.sales.geo import COUNTRIES

logger = logging.getLogger(__name__)

#: Длина кода ISO 3166-1 alpha-2.
CODE_LENGTH = 2


class OtherCountries(StrEnum):
    """Что делать с компанией, у которой видна только чужая для сегмента география."""

    REVIEW = "review"  # решает человек — так у каждой новой гипотезы
    ACCEPT = "accept"  # подходит


class CountryCodeError(ValueError):
    """Код страны не годится: не ISO 3166-1 alpha-2 или такой страны нет. Текст — почему."""


class HypothesisCountriesMixin:
    """Колонки поля «страны компаний» у гипотезы: переключатель и список «не пишем»."""

    #: `OtherCountries` строкой: новое положение — без миграции типа.
    other_countries: Mapped[str] = mapped_column(
        String(16),
        nullable=False,
        default=OtherCountries.REVIEW.value,
        server_default=OtherCountries.REVIEW.value,
    )
    #: Коды ISO 3166-1 alpha-2 заглавными, по алфавиту, без повторов.
    excluded_countries: Mapped[list[str]] = mapped_column(
        ARRAY(String(CODE_LENGTH)), nullable=False, default=list, server_default=text("'{}'")
    )


@dataclass(frozen=True, slots=True)
class CountryPolicy:
    """Поле гипотезы глазами судьи: что делать с чужой географией и каким странам не пишем."""

    other: OtherCountries = OtherCountries.REVIEW
    excluded: frozenset[str] = frozenset()


@lru_cache(maxsize=1)
def known_codes() -> frozenset[str]:
    """Коды стран из таблицы модуля — заглавными."""
    return frozenset(code.upper() for code, *_ in COUNTRIES)


def checked_codes(values: Iterable[str]) -> list[str]:
    """Коды «не пишем» — заглавными, по алфавиту, без повторов; негодный — отказ словами."""
    found: set[str] = set()
    for value in values:
        code = value.strip().upper()
        if len(code) != CODE_LENGTH or not code.isascii() or not code.isalpha():
            raise CountryCodeError(
                f"«{value.strip()}» — не код страны: нужен двухбуквенный код ISO 3166-1, например DE"
            )
        if code not in known_codes():
            raise CountryCodeError(f"страны с кодом {code} нет в ISO 3166-1 — проверьте код")
        found.add(code)
    return sorted(found)


#: Положение переключателя словами — журнал правки и слова судьи.
OTHER_WORDS = {
    OtherCountries.REVIEW: "компании из других стран — на проверку",
    OtherCountries.ACCEPT: "компании из других стран — подходят",
}


def checked_other(value: str) -> OtherCountries:
    """Положение переключателя «компании из других стран»; не из списка — отказ словами."""
    try:
        return OtherCountries(value.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(item.value for item in OtherCountries)
        raise CountryCodeError(
            f"«компании из других стран» — {value.strip()!r}: нужно одно из {allowed}"
        ) from exc


def field_words(other: str, excluded: Iterable[str]) -> str:
    """Поле гипотезы словами — для журнала: положение и коды «не пишем» (`—` — список пуст)."""
    codes = ", ".join(sorted(excluded)) or "—"
    try:
        said = OTHER_WORDS[OtherCountries(other)]
    except ValueError:
        # Значения вне выбора в базе быть не должно (правку проверяет `checked_other`): журнал пишет
        # его как есть, а лог называет — значит, его вписали мимо правки.
        logger.warning("поле гипотезы: значение вне выбора", extra={"other_countries": other})
        said = f"компании из других стран — {other}"
    return f"{said}; не пишем компаниям из: {codes}"


def policy_of(
    other_countries: str | None, excluded_countries: Iterable[str] | None
) -> CountryPolicy:
    """Поле гипотезы из колонок. Положение, которого код не знает (строку правили руками), —
    «решает человек», громко в лог: молча принять чужую географию было бы хуже."""
    try:
        other = OtherCountries(other_countries or OtherCountries.REVIEW)
    except ValueError:
        logger.warning(
            "продажи: у гипотезы неизвестное положение «компании из других стран»",
            extra={"value": str(other_countries)[:32]},
        )
        other = OtherCountries.REVIEW
    excluded = frozenset(code.strip().upper() for code in excluded_countries or () if code.strip())
    return CountryPolicy(other, excluded)
