"""Что код читает в тексте письма: ссылки, язык, предложения, отсрочки, числа.

Одно чтение на бриф и судью продаж: язык письма в `meta` брифа (A7) и язык
черновика у судьи считает одна функция, отсрочку в нашем прежнем письме и в
черновике узнаёт одно правило. Иначе бриф и судья однажды разошлись бы в том,
на каком языке письмо.

**Язык — по алфавиту** (Spec 3.3): кириллица — `ru`, латиница — `en`, иное или
ничья — `None`, и это решает человек. Немецкое письмо латиницей — `en`: правило
грубое, и это известно; язык сверяется с письмом собеседника, а не с
полным списком языков.

**Отсрочка** — фраза, где мы обещаем прислать или вернуться позже: «пришлю на
днях», «I'll send it later», «вернусь к вам». Обещание без «позже» — не
отсрочка: «пришлю кейс: вот он» обещание выполняет.

**Числа и суммы — без ссылок и адресов**: в ссылке на созвон бывают цифры, и
они не сумма. Сами числа читает общее правило разбора ответов
(`replies/money.py`): «1 200», «1,200» и «1200» — одно число.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from decimal import Decimal

from backend.features.replies import money

#: Ссылка со схемой или с `www.`; последним знаком не бывает точка или скобка —
#: так пишут ссылку в конце предложения.
_URL = r"(?:https?://|www\.)[^\s<>\"'«»]*[^\s<>\"'«».,;:!?)\]]"
_EMAIL = r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"
#: Telegram без схемы: `t.me/имя`.
_TME = r"(?<![\w/.])(?:t\.me|telegram\.me)/\w+"
#: Имя в Telegram: `@имя` от пяти знаков, не часть адреса почты.
_HANDLE = r"(?<![\w.@])@[A-Za-z0-9_]{5,32}\b"
_LINK = re.compile(f"{_URL}|{_EMAIL}|{_TME}|{_HANDLE}", re.IGNORECASE)

_SPLIT = re.compile(r"(?<=[.?!…])\s+|\n+")
_ENDS = (".", "?", "!", "…")

#: Алфавит → язык. Остальные алфавиты — язык не определён.
_SCRIPTS = {"CYRILLIC": "ru", "LATIN": "en"}

#: Мы обещаем: первое лицо будущего времени.
_PROMISE = re.compile(
    r"\b(?:пришл(?:ю|ём|ем)|вышл(?:ю|ем)|отправ(?:лю|им)|подготов(?:лю|им)|расскаж(?:у|ем)"
    r"|верн(?:усь|ёмся|емся)|напиш(?:у|ем)|уточн(?:ю|им)|собер(?:у|ём|ем)|покаж(?:у|ем)"
    r"|свяж(?:усь|емся|ёмся)|подел(?:юсь|имся))\b"
    r"|\b(?:i|we)(?:'ll|’ll| will| shall| am going to| are going to)\s+(?:\w+\s+)?"
    r"(?:send|share|get back|follow up|come back|prepare|check|circle back|reach out|forward)\b",
    re.IGNORECASE,
)
#: …позже.
_LATER = re.compile(
    r"\b(?:позже|попозже|на днях|завтра|скоро|в ближайшее время|на следующей неделе|как только"
    r"|через (?:пару|несколько) (?:дней|недель)|в течение (?:дня|недели)"
    r"|later|soon|shortly|tomorrow|next week|in a (?:few|couple of) days|asap|as soon as)\b",
    re.IGNORECASE,
)
#: Отсрочка сама по себе, без «позже».
_PUT_OFF = re.compile(
    r"\b(?:верн(?:усь|ёмся|емся) к вам|верн(?:усь|ёмся|емся) с\b|get back to you|circle back)",
    re.IGNORECASE,
)


def links_in(text: str) -> list[str]:
    """Ссылки, адреса почты и имена Telegram — в порядке, как стоят в тексте."""
    return [found.group(0) for found in _LINK.finditer(text)]


def without_links(text: str) -> str:
    return _LINK.sub(" ", text)


def normalized(link: str) -> str:
    """Ссылка для сравнения: без схемы, `www.`, косой черты в конце и регистра;
    Telegram — одним видом (`@имя` ≡ `t.me/имя`)."""
    value = link.strip().lower().rstrip("/")
    value = re.sub(r"^https?://", "", value)
    value = re.sub(r"^www\.", "", value)
    value = re.sub(r"^telegram\.me/", "t.me/", value)
    return f"t.me/{value[1:]}" if value.startswith("@") else value


def language_of(text: str) -> str | None:
    """Язык по алфавиту букв, без ссылок и адресов. Ничья или иной алфавит — `None`."""
    scripts = Counter(
        unicodedata.name(char, "?").split(" ", 1)[0]
        for char in without_links(text)
        if char.isalpha()
    )
    ranked = scripts.most_common(2)
    if not ranked or (len(ranked) == 2 and ranked[0][1] == ranked[1][1]):
        return None
    return _SCRIPTS.get(ranked[0][0])


def sentences(text: str) -> list[str]:
    """Куски текста по концам предложений и строкам. Ссылки их не рвут:
    точка внутри ссылки стоит не перед пробелом."""
    return [piece.strip() for piece in _SPLIT.split(text) if piece.strip()]


def ended(piece: str) -> bool:
    """Предложение ли это: кончается точкой, вопросом, восклицанием или многоточием.
    Обращение («Добрый день,») и подпись предложениями не считаются."""
    return piece.endswith(_ENDS)


def deferrals(text: str) -> list[str]:
    """Наши отсрочки в тексте — предложениями, как написаны."""
    return [
        piece
        for piece in sentences(text)
        if _PUT_OFF.search(piece) or (_PROMISE.search(piece) and _LATER.search(piece))
    ]


def numbers_in(text: str) -> set[Decimal]:
    """Все числа текста, кроме цифр в ссылках и адресах."""
    return money.numbers_in(without_links(text))


def amounts_in(text: str) -> set[Decimal]:
    """Денежные суммы текста — числа с валютой рядом, без ссылок и адресов."""
    return money.amounts_in(without_links(text))
