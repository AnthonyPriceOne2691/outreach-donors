"""Язык, страна и пояс лида — одно правило у загрузки базы, правки лида на экране и сборки.

**Язык — основой кода.** `ru-RU`, `ru_RU`, `RU` — это `ru`: цепочки писем — на основе языка
(`chain_text.LANGUAGES`), и лид с `ru-RU` иначе ждал бы в очереди «язык не задан» навсегда.
Основу берёт и загрузка (в базу ложится `ru`), и сборка очереди — для лидов, загруженных до
этого правила с регионом. База знаний свою запись с регионом (`pt-br`) держит как есть: агент
сводит её с языком лида по началу кода (`kb.speaks`).

**Значения по умолчанию загрузки** — язык и страна или пояс на всю загрузку. Подставляются
только туда, где колонка пуста: колонка файла точнее умолчания (подстановку делает
`intake.preview`). Пояс по умолчанию — именем из базы поясов, страна — кодом или названием,
как в колонке; у страны с несколькими поясами — столичный, с замечанием (решение 04.10).

**Без чего письмо не уйдёт** — словами до загрузки (`warnings`): без языка — ни одного письма
(сборка ждёт «язык не задан»), язык без цепочки — тоже, без страны и пояса — окно отправки не
знает часов получателя (`core/window.py`), письмо ждёт всегда.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from backend.features.sales import geo
from backend.features.sales.chain_text import LANGUAGES
from backend.features.sales.policy import Located, zones_of

#: Код языка с хвостом региона или письменности: `ru`, `ru-RU`, `en_us`, `zh-Hant-TW`.
_CODE = re.compile(r"(?P<base>[a-z]{2,3})(?:[-_][a-z0-9]{1,8})*")
#: Как ждут язык — в отказах загрузки, правки и умолчания.
LANGUAGE_HINT = "ждём код — en, ru"
PLACE_HINT = (
    "ждём код или название страны — de, Germany, Германия — или пояс из базы поясов — Europe/Berlin"
)


class ValueRefusedError(ValueError):
    """Значение не годится полю лида. Текст — что поправить."""


def language_code(text: str | None) -> str | None:
    """Код языка основой: `ru-RU` → `ru`. Пусто или не код — `None`."""
    found = _CODE.fullmatch((text or "").strip().lower())
    return found["base"] if found else None


def language_base(text: str | None) -> str:
    """Язык записанного лида основой — для сборки: `ru-ru` → `ru`; не код — как есть,
    строчными (цепочки на нём нет, и лид ждёт с причиной)."""
    plain = (text or "").strip().lower()
    return language_code(plain) or plain


@dataclass(frozen=True, slots=True)
class Place:
    """Страна кодом ISO-2 и пояс — или только пояс."""

    country: str | None
    timezone: str | None
    #: Пояс — столичный: у страны их несколько.
    by_capital: bool = False


def place(text: str) -> Place | None:
    """Страна или пояс словами человека: `Europe/Berlin`, `de`, `Germany`, «Германия».
    Непонятое — `None`."""
    if zone := geo.zone_name(text):
        return Place(country=None, timezone=zone)
    if code := geo.country_code(text):
        return Place(country=code, timezone=geo.timezone_for(code), by_capital=geo.by_capital(code))
    return None


@dataclass(frozen=True, slots=True)
class Defaults:
    """Значения по умолчанию на одну загрузку: что подставить, где колонка пуста."""

    language: str | None = None
    place: Place | None = None

    @property
    def note(self) -> str | None:
        """Замечание к умолчанию: пояс страны — столичный, а не единственный."""
        found = self.place
        if found is None or not found.by_capital:
            return None
        return (
            f"пояс по умолчанию — по столице: {found.timezone}; у страны {found.country} их "
            "несколько, точнее скажет колонка пояса"
        )


#: Загрузка без умолчаний: подставлять нечего.
NO_DEFAULTS = Defaults()


def defaults(language: str | None, where: str | None) -> Defaults:
    """Умолчания загрузки словами человека → проверенные. Не годится — отказ словами."""
    said, text = (language or "").strip(), (where or "").strip()
    code = language_code(said)
    if said and code is None:
        raise ValueRefusedError(f"язык по умолчанию «{said}» — не код языка: {LANGUAGE_HINT}")
    found = place(text) if text else None
    if text and found is None:
        raise ValueRefusedError(
            f"страна или пояс по умолчанию «{text}» не распознаны: {PLACE_HINT}"
        )
    return Defaults(language=code, place=found)


class Placed(Located, Protocol):
    """Лид, чьи язык, страну и пояс судят: загружаемый (`intake.Lead`) или записанный."""

    @property
    def language(self) -> str | None: ...


def _leads(count: int) -> str:
    """«у 1 лида», «у 3 лидов», «у 21 лида» — число и слово после «у»."""
    word = "лида" if count % 10 == 1 and count % 100 != 11 else "лидов"
    return f"у {count} {word}"


def warnings(leads: Iterable[Placed]) -> list[str]:
    """Без чего письма не уйдут — словами, с числом лидов: язык, цепочка на языке, пояс."""
    found = list(leads)
    silent = sum(1 for lead in found if not lead.language)
    foreign = Counter(
        lead.language for lead in found if lead.language and lead.language not in LANGUAGES
    )
    nowhere = sum(1 for lead in found if not any(zones_of(lead)))
    said: list[str] = []
    if silent:
        said.append(
            f"{_leads(silent)} нет языка — им не уйдёт ни одно письмо: задайте язык по "
            "умолчанию или колонку языка"
        )
    if foreign:
        codes = ", ".join(sorted(foreign))
        said.append(
            f"{_leads(sum(foreign.values()))} язык без цепочки писем ({codes}) — им не уйдёт ни "
            f"одно письмо: цепочки есть на {', '.join(LANGUAGES)}"
        )
    if nowhere:
        said.append(
            f"{_leads(nowhere)} нет ни страны, ни пояса — окно отправки не знает их часов, и "
            "письмо не уйдёт: задайте страну или пояс по умолчанию"
        )
    return said
