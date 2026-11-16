"""Карточка листа «Плана работ»: подписи в колонке A, значения в колонке B — в поля гипотезы.

Лист книги плана — гипотеза (`plan_file.py`). Над строкой «Ключи» — карточка: подпись поля в
колонке A, значение — в B, подсказка — в C (её не читаем). **Поле находится по подписи, а не по
номеру строки**: строки переставляют, вставляют пустые, удаляют ненужные. Регистр, лишние пробелы,
«ё» и двоеточие после подписи не важны.

Значения — теми же правилами и таблицами, что у гипотезы с экрана и у прогона сбора; своих списков
стран и языков здесь нет:

- «Название» — имя гипотезы по правилу заведения (`hypotheses._name_of`): пробелы сведены, не
  длиннее колонки;
- «Кому пишем» — описание сегмента: по нему судья проверяет сайты, без него прогон не запустится;
- «Где ищем» — страны через запятую, названием или кодом (`geo.country_code`); страна — рынок
  выдачи (`serp/markets.SERP_LANGUAGE`): по другой выдачу не купить;
- «Язык писем» — язык цепочки (`chain_text.LANGUAGES`) именем, кодом или самоназванием из таблицы
  языков модуля (`segment_alphabets.LANGUAGES`);
- «Компании из других стран» — «подходят» или «на проверку» (`OtherCountries`); пусто — «на
  проверку», как у каждой новой гипотезы;
- «Не пишем компаниям из» — страны через запятую, в коды ISO (`checked_codes`); пусто — пишем всем;
- «Найти сайтов» — цель: сколько сайтов, прошедших судью, набрать (решение владельца 16.11); целое
  больше нуля; пусто — общая цель из окна загрузки;
- «Глубина поиска до» — топ-10, 20, 30 или 50, страницами выдачи по 10; пусто — топ-50.

Поля «Название», «Кому пишем», «Где ищем» и «Язык писем» обязательны: без них гипотезу не завести
и не собрать. Ошибка поля — словами с подписью поля: что не так и что вписать. Все ошибки листа
называются сразу, а не по одной за загрузку.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from typing import Any

from backend.features.sales import chain_text, segment_alphabets
from backend.features.sales.geo import country_code
from backend.features.sales.hypotheses import HypothesisError, _name_of
from backend.features.sales.hypothesis_countries import OtherCountries, checked_codes
from backend.features.sales.segment_spend import number
from backend.features.serp.markets import SERP_LANGUAGE

logger = logging.getLogger(__name__)


class Field(StrEnum):
    """Поле карточки — именем поля гипотезы и прогона, куда оно ляжет."""

    NAME = "name"
    SEGMENT = "segment"
    MARKETS = "markets"
    LANGUAGE = "language"
    OTHER = "other_countries"
    EXCLUDED = "excluded_countries"
    TARGET = "target"
    DEPTH = "depth_pages"


#: Подписи полей в колонке A — как в книге-примере плана (лист «Как заполнять» там же).
LABELS: dict[Field, str] = {
    Field.NAME: "Название",
    Field.SEGMENT: "Кому пишем",
    Field.MARKETS: "Где ищем",
    Field.LANGUAGE: "Язык писем",
    Field.OTHER: "Компании из других стран",
    Field.EXCLUDED: "Не пишем компаниям из",
    Field.TARGET: "Найти сайтов",
    Field.DEPTH: "Глубина поиска до",
}
#: Подпись строки, под которой идут ключи.
KEYS_LABEL = "Ключи"
#: Без этих полей гипотезу не завести и не собрать: имя, вопрос судье, рынок и язык цепочки.
REQUIRED = (Field.NAME, Field.SEGMENT, Field.MARKETS, Field.LANGUAGE)
#: «Компании из других стран» словами книги — список проверки данных в примере.
OTHER_CHOICES: dict[str, OtherCountries] = {
    "подходят": OtherCountries.ACCEPT,
    "на проверку": OtherCountries.REVIEW,
}
#: «Глубина поиска до» словами книги → страниц выдачи по 10 результатов.
DEPTH_CHOICES: dict[str, int] = {"топ-10": 1, "топ-20": 2, "топ-30": 3, "топ-50": 5}
#: Пусто — топ-50: подсказка примера «дальше этой позиции не идём; пусто — топ-50».
DEFAULT_DEPTH = DEPTH_CHOICES["топ-50"]
#: Цель гипотезы — не больше: план на неделю или месяц находит тысячи сайтов (в словах владельца —
#: «5 000», «10 000»); миллион и больше — опечатка в разрядах, а не цель.
MAX_TARGET = 1_000_000
#: Чем разделены страны в ячейке: запятая — как в подсказке; точка с запятой — тоже частая.
_SEPARATORS = re.compile(r"[,;]")
#: Тире всех видов в «топ-10»: Excel и телефоны сами меняют дефис на длинное тире.
_DASHES = re.compile(r"[‐-―−]")


@dataclass(frozen=True, slots=True)
class SheetIssue:
    """Ошибка листа словами: какое поле (`None` — лист, а не поле) и что не так."""

    field: Field | None
    words: str


@dataclass(frozen=True, slots=True)
class Card:
    """Поля гипотезы из карточки листа. У поля с ошибкой — пусто или умолчание: лист с ошибкой
    к запуску не идёт, а прочитанное показывается."""

    name: str = ""
    segment: str = ""
    #: Рынки выдачи — коды ISO 3166-1 строчными, как у прогона сбора, без повторов.
    markets: tuple[str, ...] = ()
    #: Язык писем — код цепочки.
    language: str = ""
    other_countries: OtherCountries = OtherCountries.REVIEW
    #: «Не пишем компаниям из» — коды ISO 3166-1 заглавными, по алфавиту, как у гипотезы.
    excluded_countries: tuple[str, ...] = ()
    #: Сколько сайтов, прошедших судью, набрать; `None` — общая цель окна загрузки.
    target: int | None = None
    #: Предельная глубина выдачи страницами по 10 результатов.
    depth_pages: int = DEFAULT_DEPTH


def flat(text: str) -> str:
    """Слово книги для сверки: без регистра, «ё», лишних пробелов и двоеточия в конце."""
    plain = text.replace("ё", "е").replace("Ё", "Е").strip().rstrip(":")
    return " ".join(plain.casefold().split())


#: Подпись колонки A → поле карточки.
FIELDS: dict[str, Field] = {flat(label): field for field, label in LABELS.items()}


def _quoted(values: list[str]) -> str:
    return ", ".join(f"«{value}»" for value in values)


def _parts(raw: str) -> list[str]:
    return [part.strip() for part in _SEPARATORS.split(raw) if part.strip()]


def _countries(raw: str) -> tuple[dict[str, str], list[str]]:
    """Страны ячейки → код ISO строчными по каждой, как вписана, и нераспознанные — как вписаны."""
    found: dict[str, str] = {}
    unknown: list[str] = []
    for part in _parts(raw):
        code = country_code(part)
        if code is None:
            unknown.append(part)
        else:
            found.setdefault(code, part)
    return found, unknown


def _not_countries(unknown: list[str]) -> str:
    return f"не страны: {_quoted(unknown)} — впишите название страны или код ISO, например KZ"


def _markets(raw: str) -> tuple[str, ...]:
    found, unknown = _countries(raw)
    outside = [part for code, part in found.items() if code not in SERP_LANGUAGE]
    problems = [_not_countries(unknown)] if unknown else []
    if outside:
        problems.append(
            f"нет среди рынков выдачи: {_quoted(outside)} — там сбор выдачу не покупает"
        )
    if problems:
        raise ValueError("; ".join(problems))
    return tuple(found)


def _excluded(raw: str) -> tuple[str, ...]:
    found, unknown = _countries(raw)
    if unknown:
        raise ValueError(_not_countries(unknown))
    return tuple(checked_codes(found))


@lru_cache(maxsize=1)
def _languages() -> dict[str, str]:
    """Язык писем словом книги → код цепочки: код, имя и самоназвания из таблицы языков."""
    found: dict[str, str] = {}
    for language in segment_alphabets.LANGUAGES:
        if language.code in chain_text.LANGUAGES:
            for word in (language.code, language.name, *language.switch):
                found.setdefault(flat(word), language.code)
    return found


def _language(raw: str) -> str:
    code = _languages().get(flat(raw))
    if code is None:
        names = [
            found.name
            for found in segment_alphabets.LANGUAGES
            if found.code in chain_text.LANGUAGES
        ]
        raise ValueError(
            f"«{raw}» — письма пишутся на языках {_quoted(names)}: впишите один из них"
        )
    return code


def _other(raw: str) -> OtherCountries:
    found = OTHER_CHOICES.get(flat(raw))
    if found is None:
        raise ValueError(f"«{raw}» — нужно одно из: {_quoted(list(OTHER_CHOICES))}")
    return found


def _target(raw: str) -> int:
    # «5 000» — с пробелами разрядов, и неразрывными тоже.
    digits = "".join(raw.split())
    if not digits.isdecimal() or len(digits) > len(str(MAX_TARGET)):
        raise ValueError(f"«{raw}» — не число: впишите, сколько сайтов найти, например 5000")
    if not 1 <= int(digits) <= MAX_TARGET:
        raise ValueError(
            f"«{raw}» — нужно число от 1 до {number(MAX_TARGET)}; пусто — общая цель окна загрузки"
        )
    return int(digits)


def _depth(raw: str) -> int:
    pages = DEPTH_CHOICES.get("-".join(flat(_DASHES.sub("-", raw)).replace("-", " ").split()))
    if pages is None:
        raise ValueError(f"«{raw}» — нужно одно из: {_quoted(list(DEPTH_CHOICES))}")
    return pages


def _name(raw: str) -> str:
    try:
        return _name_of(raw)
    except HypothesisError as exc:
        raise ValueError(str(exc)) from exc


#: Как читается значение поля. Ошибка — `ValueError` словами без подписи: её ставит `card_of`.
PARSERS: dict[Field, Callable[[str], Any]] = {
    Field.NAME: _name,
    Field.SEGMENT: str.strip,
    Field.MARKETS: _markets,
    Field.LANGUAGE: _language,
    Field.OTHER: _other,
    Field.EXCLUDED: _excluded,
    Field.TARGET: _target,
    Field.DEPTH: _depth,
}


def card_of(values: Mapping[Field, str]) -> tuple[Card, list[SheetIssue]]:
    """Значения карточки по подписям → поля гипотезы и ошибки полей. Нет подписи или пусто —
    одно и то же: обязательное поле — ошибка, прочее — умолчание."""
    read: dict[str, Any] = {}
    issues: list[SheetIssue] = []
    for field, parse in PARSERS.items():
        raw = values.get(field, "").strip()
        if not raw:
            if field in REQUIRED:
                issues.append(SheetIssue(field, f"«{LABELS[field]}» не заполнено"))
            continue
        try:
            read[field.value] = parse(raw)
        except ValueError as exc:
            # Ошибка поля — данные листа, а не сбой: её показывает черновик, журнал — поле.
            logger.info("план работ: поле карточки не годится", extra={"field": field.value})
            issues.append(SheetIssue(field, f"«{LABELS[field]}»: {exc}"))
    return Card(**read), issues
