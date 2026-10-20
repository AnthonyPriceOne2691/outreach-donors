"""Ответ модели о теме: код из списка, причина — короткой и чистой, сбой формы — не знание.

**«Не ясна» — только явный `"topic": null`.** Модель прочла текст и сказала, что темы по нему
не понять, — это знание: оно ложится на домен (на месяц, `TOPIC_UNCLEAR_TTL_DAYS`). Всё прочее
без темы — сбой формы, а не знание (`Parsed.unreadable`): проза вместо JSON, нет поля темы,
тема не из списка («finance, business», «cooking»). Такой ответ на домен не пишется и известную
тему не затирает: следующий проход спросит снова, а сбой считается своим числом.

**Подпись вместо кода — тот же код** («Финансы», «Finance», «real estate»). Битый JSON (кавычки
внутри причины) спасается по полям: тема стоит до свободного текста и обычно цела.

**Причина — для глаза оператора** (`TopicLine`), поэтому короткая и без того, что сайт мог
вписать в свой текст: адресов почты, ссылок и телефонов. Текст сайта — чужие данные, и модель,
пересказав его, пронесла бы их в интерфейс.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from backend.features.topics.taxonomy import BY_CODE, TOPICS

logger = logging.getLogger(__name__)

#: Сколько знаков причины видит оператор: фраза модели — до 12 слов, с запасом.
REASON_LIMIT = 120
#: Сколько знаков ответа модели просматривается чисткой — с запасом на вырезанное.
_SCANNED = 600

#: Код — латиница, цифры и подчёркивание; подпись и пробелы сводятся к нему.
_SEPARATORS = re.compile(r"[\s\-/]+")
#: Подписи и коды, которые модель может прислать вместо кода, → код.
_ALIASES: dict[str, str] = {
    **{topic.code: topic.code for topic in TOPICS},
    **{topic.label.lower(): topic.code for topic in TOPICS},
    **{topic.code.replace("_", " "): topic.code for topic in TOPICS},
}

#: Чего в причине быть не должно: адрес почты, ссылка или голое имя сайта, телефон.
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_LINK = re.compile(r"(?:https?://|www\.)\S+|(?<![\w@.])[\w-]+(?:\.[\w-]+)*\.[^\W\d_]{2,24}\b")
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
#: Что осталось от вырезанного: пробел перед знаком и знаки подряд.
_LOOSE = re.compile(r"\s+(?=[,;:.!?…])")
_REPEATED = re.compile(r"([,;:])(?:\s*[,;:])+")


def code_of(raw: object) -> str | None:
    """Код темы из ответа модели или `None`, если такой темы в списке нет.

    Регистр, пробелы, дефис вместо подчёркивания и русская подпись — та же тема;
    единственное и множественное число английского кода («sport» — «sports») — тоже.
    """
    if not isinstance(raw, str):
        return None
    word = raw.strip().strip("\"'«»").lower()
    for candidate in (word, _SEPARATORS.sub("_", word), word.replace("_", " ")):
        if candidate in _ALIASES:
            return _ALIASES[candidate]
    snake = _SEPARATORS.sub("_", word)
    for variant in (f"{snake}s", snake.removesuffix("s")):
        if variant in BY_CODE:
            return variant
    return None


def clean_reason(text: str) -> str:
    """Причина для экрана: без адресов почты, ссылок и телефонов — и не длиннее предела.

    Чистится начало текста (`_SCANNED`): длиннее причина всё равно не встанет, а регулярки
    на чужой простыне без границы стоили бы квадрат её длины."""
    head = text[:_SCANNED]
    clean = " ".join(_PHONE.sub("", _LINK.sub("", _EMAIL.sub("", head))).split())
    clean = _REPEATED.sub(r"\1", _LOOSE.sub("", clean)).strip(" ,;:—-")
    if len(clean) <= REASON_LIMIT:
        return clean
    cut = clean[:REASON_LIMIT].rsplit(" ", 1)[0].rstrip(" ,;:—-")
    return f"{cut}…"


_FIELD = r'"{name}"\s*:\s*(?:"([^"]*)"|(null))'


def _salvage(content: str) -> dict[str, Any] | None:
    """Поля ответа из битого JSON — по одному: тема и вторая стоят до причины и целы."""
    found: dict[str, Any] = {}
    for name in ("topic", "secondary", "why"):
        match = re.search(_FIELD.format(name=name), content)
        if match is not None:
            found[name] = match.group(1)
    return found if "topic" in found else None


def _body_of(content: str) -> dict[str, Any] | None:
    try:
        body = json.loads(content[content.find("{") : content.rfind("}") + 1])
    except (ValueError, TypeError):
        logger.info("тематика сайта: ответ не JSON, спасаем по полям: %r", content[:120])
        return _salvage(content)
    return body if isinstance(body, dict) else None


@dataclass(frozen=True, slots=True)
class Parsed:
    """Что сказала модель: тема, вторая, почему — или сбой формы (`unreadable`)."""

    topic: str | None
    secondary: str | None
    reason: str
    unreadable: bool = False


def _unreadable(reason: str) -> Parsed:
    return Parsed(None, None, reason, unreadable=True)


def parse(content: str) -> Parsed:
    """Ответ модели → тема. Явный `null` — «не ясна»; прочее без темы из списка — сбой формы."""
    body = _body_of(content)
    if body is None:
        return _unreadable("ответ модели не разобран: не JSON")
    if "topic" not in body:
        return _unreadable("в ответе модели нет темы")
    raw = body["topic"]
    why = clean_reason(str(body.get("why") or ""))
    if raw is None:
        return Parsed(None, None, why or "по тексту тему не понять")
    topic = code_of(raw)
    if topic is None:
        named = clean_reason(str(raw))[:40]
        return _unreadable(f"модель назвала тему не из списка: «{named}»")
    secondary = code_of(body.get("secondary"))
    return Parsed(topic, None if secondary == topic else secondary, why or "по тексту сайта")
