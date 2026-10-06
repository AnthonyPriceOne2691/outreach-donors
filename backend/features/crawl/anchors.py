"""Что говорит анкор ссылки: ничего, слово сделки, товар серой ниши или призыв.

Признаки общие для любой ниши (слово Anthony 06.10.2026: «оценка не под
конкретную нишу»). Слова сравниваются целиком, а не подстрокой: `bet` живёт
внутри `better` и `alphabet`. Анкор-адрес («http://…», «app.dealroom.co»)
текстом не считается — слова внутри адреса не текст ссылки.
"""

from __future__ import annotations

import re
from enum import StrEnum
from itertools import pairwise

_WORD = re.compile(r"[a-z0-9]+")
#: Притяжательный оборот: «Solana’s roadmap», «Apple's report».
POSSESSIVE = re.compile(r"\w['’]s\b", re.IGNORECASE)
#: Анкор — сам адрес: «http://…», «www.…», «tradingview.com». Слова внутри
#: адреса («/Advertising/», «code.google.com») не текст ссылки.
_ADDRESS = re.compile(r"^(?:https?://|www\.)\S+$|^[a-z0-9-]+(?:\.[a-z0-9-]+)+(?:/\S*)?$", re.I)
#: Призыв к действию — **пара слов в начале** короткого анкора: «Buy now»,
#: «Claim your bonus», «Sign up». Глагол один ничего не значит: «shop the
#: perimeter» и «subscribe to a newspaper» — советы финансового блога,
#: «Buy This, Not That» — название книги, «Visit Paris» — путешествие
#: (обходы 06.10). Одним словом зовёт только кнопка «Signup».
CALL_PHRASES: tuple[tuple[str, ...], ...] = (
    ("signup",), ("sign", "up"), ("buy", "now"), ("buy", "online"), ("shop", "now"),
    ("shop", "online"), ("order", "now"), ("order", "online"), ("claim", "your"),
    ("claim", "bonus"), ("claim", "offer"), ("claim", "now"), ("claim", "free"),
    ("register", "now"), ("register", "today"), ("register", "free"), ("subscribe", "now"),
    ("subscribe", "today"), ("get", "started"), ("get", "it"), ("get", "yours"),
    ("get", "your"), ("start", "free"), ("start", "your"), ("try", "it"), ("try", "free"),
    ("try", "for", "free"), ("book", "now"), ("book", "your"), ("apply", "now"),
    ("bet", "now"), ("play", "now"), ("play", "free"), ("join", "now"), ("join", "free"),
    ("join", "today"), ("visit", "site"), ("visit", "website"), ("visit", "store"),
    ("visit", "now"), ("use", "code"), ("download", "now"), ("install", "now"),
)  # fmt: skip
CALL_MAX_WORDS = 5
#: Слова сделки: слабый признак в любой нише — цена, скидка, обзор, акция.
#: «vs», «rental», «hire», «quote» сюда не входят: на финансах это обычная
#: речь («Contributions vs. Returns Calculator», «rental snowball»).
DEAL_WORDS: frozenset[str] = frozenset(
    {
        "cheap", "cheapest", "discount", "discounts", "coupon", "coupons", "promo",
        "promocode", "promotion", "promotions", "deal", "deals", "price", "prices", "pricing",
        "sale", "offer", "offers", "bonus", "bonuses", "trial", "review", "reviews",
        "alternative", "alternatives", "affordable",
    }
)  # fmt: skip
DEAL_MAX_WORDS = 6
#: Чья-то речь о своём: «his review», «my year-end review», «our offer» — это
#: ссылка на своё или на знакомого, а не предложение купить (финансы, 06.10).
PERSONAL: frozenset[str] = frozenset(
    {
        "i", "me", "my", "mine", "we", "us", "our", "ours", "he", "him", "his", "she",
        "her", "hers", "they", "them", "their", "theirs",
    }
)  # fmt: skip
#: Товары ниш, которые покупают ссылки везде, на любом доноре: их название
#: в анкоре — слабый признак при любой длине и в любой нише донора.
TRADE_GOODS: frozenset[str] = frozenset(
    {
        "casino", "casinos", "sportsbook", "sportsbooks", "betting", "bookmaker", "bookmakers",
        "slots", "cbd", "vape", "vapes", "payday", "viagra", "cialis", "escort", "escorts",
    }
)  # fmt: skip
TRADE_PHRASES: tuple[tuple[str, str], ...] = (("free", "bet"), ("free", "bets"), ("free", "spins"))


class AnchorKind(StrEnum):
    """Что говорит анкор: ничего, слово сделки, товар серой ниши или призыв.

    Слово сделки и товар — слабые признаки, призыв — сильный. Товар отличается
    от слова сделки тем, что под `nofollow` он — уже признак размещения:
    донор закрыл ссылку на букмекера или казино от передачи веса.
    """

    PLAIN = "plain"
    DEAL = "deal"
    TRADE = "trade"
    CALL = "call"


def tokens(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def is_address(anchor: str) -> bool:
    """Анкор — это адрес, а не текст: «http://…», «www.…», «app.dealroom.co»."""
    return bool(_ADDRESS.match(anchor.strip()))


def anchor_kind(anchor: str) -> AnchorKind:
    """Призыв, слово сделки или просто текст — по словам, не подстрокой.

    Подстрока `bet` живёт внутри `better` и `alphabet`. Призыв — глагол
    в начале короткого анкора: в длинной редакционной фразе «get» и «code»
    ничего не продают. Слово сделки — в коротком анкоре без «my/his/our»:
    «his review» — ссылка на знакомого, «Acme VPN review» — на товар.
    Анкор-адрес текстом не считается.
    """
    if is_address(anchor):
        return AnchorKind.PLAIN
    words = tokens(anchor)
    if not words:
        return AnchorKind.PLAIN
    if len(words) <= CALL_MAX_WORDS and _is_call(words):
        return AnchorKind.CALL
    if _names_trade(words):
        return AnchorKind.TRADE
    personal = bool(set(words) & PERSONAL)
    if len(words) <= DEAL_MAX_WORDS and not personal and set(words) & DEAL_WORDS:
        return AnchorKind.DEAL
    return AnchorKind.PLAIN


def _is_call(words: list[str]) -> bool:
    return any(tuple(words[: len(phrase)]) == phrase for phrase in CALL_PHRASES)


def _names_trade(words: list[str]) -> bool:
    return bool(set(words) & TRADE_GOODS) or any(pair in TRADE_PHRASES for pair in pairwise(words))


def is_commercial_anchor(anchor: str) -> bool:
    """Анкор зовёт к действию или называет сделку (`anchor_kind`)."""
    return anchor_kind(anchor) is not AnchorKind.PLAIN
