"""Гигиена фразы: канонизация и отсев мусора.

Плохой ключ — это не «некрасиво». Это оплаченный запрос выдачи, который
ничего не найдёт, плюс мусор в базе доноров. Отсев идёт до траты,
а не после.

Канонизация отделена от проверки намеренно: приводить фразу к единому
виду надо всегда, а выбрасывать — только по названной причине.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
import zoneinfo
from collections.abc import Callable
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Виды тире и апострофов, которые модель ставит вперемешку. Для поиска
#: это разные символы, а для человека один и тот же.
_DASHES = "‐‑‒–—―−"
_APOSTROPHES = "‘’‛`"
_TRANSLATION = {ord(c): "-" for c in _DASHES} | {ord(c): "'" for c in _APOSTROPHES}

_SEARCH_OPERATORS = ("site:", "intitle:", "inurl:", "intext:", "inanchor:", "filetype:")
_PLACEHOLDERS = (
    "team name", "club name", "league name", "city name", "insert ", "e.g.",
    "your city", "название", "вставьте",
)  # fmt: skip
#: Отладочные метки заглавными, просочившиеся из промпта.
_SENTINEL_RE = re.compile(r"\b[A-Z]{4,}\b")
_PUNCTUATION_LEAK = set('"[]{}<>|\\')
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
MAX_WORDS = 8
MAX_LENGTH = 256

#: Восточноазиатские письменности считаются одним семейством: японский
#: законно мешает кану и иероглифы в одной фразе. Сверяется вхождением,
#: а не равенством: знак долготы в каталоге называется
#: «KATAKANA-HIRAGANA», и при сверке по равенству он оказывался третьей
#: письменностью — законные японские запросы шли в отсев.
_EAST_ASIAN = ("CJK", "HIRAGANA", "KATAKANA", "HANGUL", "BOPOMOFO", "IDEOGRAPHIC")


def normalize(phrase: str) -> str:
    """Привести фразу к единому виду.

    Диакритика сохраняется: `mañana` и `piñol` — законные слова, а не
    мусор. Складывать их в латиницу значит ломать язык рынка.
    """
    text = unicodedata.normalize("NFKC", phrase or "").translate(_TRANSLATION)
    return " ".join(text.split()).strip().lower()


def _script_families(text: str) -> set[str]:
    families: set[str] = set()
    for char in text:
        if not char.isalpha():
            continue
        script = unicodedata.name(char, "").split(" ", 1)[0]
        if not script or script == "LATIN":
            continue
        families.add("CJK" if any(m in script for m in _EAST_ASIAN) else script)
    return families


def same_script(first: str, second: str) -> bool:
    """Одна ли письменность у двух кусков запроса — с латиницей наравне.

    В правиле отсева латиница нейтральна: марки пишут ею и внутри
    кириллических запросов. А тема футпринта нужна на письме самого
    футпринта: «ставки на спорт write for us» отсев пропускает, но так
    никто не ищет (замер 24.09 — такой запрос встал первым в пул).
    """

    def scripts(text: str) -> set[str]:
        found = _script_families(text)
        if any(ch.isalpha() and unicodedata.name(ch, "").startswith("LATIN") for ch in text):
            found.add("LATIN")
        return found

    return scripts(first) == scripts(second)


def _current_year() -> int:
    return datetime.now(tz=zoneinfo.ZoneInfo("UTC")).year


def _has_stale_year(low: str) -> bool:
    """Год в прошлом. Текущий и будущий допустимы: «выборы 2027» — живой
    запрос, «итоги 2024» в 2026 году — мёртвый."""
    year = _current_year()
    return any(int(match.group()) < year for match in _YEAR_RE.finditer(low))


#: Правила отсева: проверка и то, как она себя называет. Таблицей, а не
#: цепочкой условий: список будет расти — состав мусора зависит от модели
#: и рынка, — а функция от этого расти не должна.
_RULES: tuple[tuple[Callable[[str, str], bool], str], ...] = (
    (lambda value, _l: len(value) > MAX_LENGTH, f"длиннее {MAX_LENGTH} знаков"),
    (
        lambda _v, low: any(op in low for op in _SEARCH_OPERATORS),
        "поисковый оператор: так пользователи не ищут",
    ),
    (
        lambda _v, low: any(marker in low for marker in _PLACEHOLDERS),
        "подсказка из промпта вместо слова",
    ),
    (lambda value, _l: bool(_SENTINEL_RE.search(value)), "отладочная метка заглавными"),
    (
        lambda value, _l: any(ch in value for ch in _PUNCTUATION_LEAK),
        "остаток разметки в тексте",
    ),
    (lambda _v, low: len(low.split()) > MAX_WORDS, f"больше {MAX_WORDS} слов — это предложение"),
    (lambda _v, low: _has_stale_year(low), "прошедший год: выдача по такому запросу мертва"),
    (
        lambda value, _l: len(_script_families(value)) > 1,
        "две письменности в одной фразе — модель смешала языки",
    ),
)


def rejection_reason(phrase: str) -> str | None:
    """Почему фразу нельзя отправлять в выдачу. `None` — можно.

    Причина называется словами: «отсеяно фильтром» не даёт его настроить,
    а настраивать придётся — состав мусора зависит от модели и рынка.
    """
    value = phrase.strip()
    if not value:
        return "пустая строка"

    low = value.lower()
    for matches, title in _RULES:
        if matches(value, low):
            return title
    return None


def clean(phrases: list[str]) -> tuple[list[str], dict[str, str]]:
    """Канонизировать и отсеять. Возвращает годные и причины отказов.

    Отсеянные возвращаются, а не выбрасываются: по ним видно, что именно
    выдаёт модель на этом рынке, и это единственный способ настроить
    и промпт, и фильтр.
    """
    good: list[str] = []
    rejected: dict[str, str] = {}
    seen: set[str] = set()

    for raw in phrases:
        phrase = normalize(str(raw))
        if phrase in seen:
            continue
        seen.add(phrase)

        reason = rejection_reason(phrase)
        if reason:
            rejected[phrase] = reason
        else:
            good.append(phrase)

    return good, rejected


def parse_phrases(content: str) -> list[str]:
    """Достать список фраз из ответа модели.

    Ответ приходит не всегда чистым: обёрнут в тройные кавычки, завёрнут
    в объект или оборван на середине токенного лимита. Оборванный ответ
    спасается по целым строкам — терять весь набор из-за последней
    незакрытой кавычки слишком дорого.
    """
    text = (content or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("[") :] if "[" in text else text

    try:
        data: Any = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        salvaged = re.findall(r'"((?:[^"\\]|\\.)*)"', text)
        if not salvaged:
            logger.warning("ключи: ответ модели не разобран: %r", text[:200])
            return []
        logger.warning("ключи: ответ оборван, спасено %d фраз", len(salvaged))
        return [s.strip() for s in salvaged if s.strip()]

    if isinstance(data, dict):
        # Модель иногда заворачивает список в объект — берём первый список.
        for value in data.values():
            if isinstance(value, list):
                data = value
                break

    if not isinstance(data, list):
        logger.warning("ключи: ждали список, пришло %s", type(data).__name__)
        return []
    return [str(item).strip() for item in data if str(item).strip()]
