"""Деньги в письме: валюта к коду, число к `Decimal`, суммы и все числа.

Отдельно от разбора, потому что здесь нет модели — только проверяемые
правила, на которых стоят проверки поверх её самооценки: названная цена
обязана встречаться в письме, а из нескольких сумм берётся наименьшая.
"""

from __future__ import annotations

import logging
import re
from decimal import Decimal, InvalidOperation
from typing import Any

logger = logging.getLogger(__name__)

TOPIC = "разбор ответа"

#: Валюта приводится к коду: «евро», «€» и «EUR» — одно и то же, а в базе
#: должно лежать одно значение, иначе фильтр по валюте не работает.
CURRENCIES: dict[str, str] = {
    "$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD", "доллар": "USD",
    "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "евро": "EUR",
    "£": "GBP", "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "фунт": "GBP",
    "₽": "RUB", "rub": "RUB", "rouble": "RUB", "roubles": "RUB", "руб": "RUB",
    "zł": "PLN", "pln": "PLN", "zloty": "PLN", "злот": "PLN",
    # Крипта — отдельные валюты, не доллар: USDT — это способ оплаты, и
    # подписать его долларом значит потерять, чем донор хочет получить деньги.
    "usdt": "USDT", "tether": "USDT", "₮": "USDT", "usdc": "USDC", "dai": "DAI",
    "busd": "BUSD", "btc": "BTC", "bitcoin": "BTC", "₿": "BTC", "eth": "ETH",
    "ether": "ETH", "ethereum": "ETH", "bnb": "BNB", "trx": "TRX", "tron": "TRX",
    "ltc": "LTC", "litecoin": "LTC", "sol": "SOL", "solana": "SOL", "ton": "TON",
    "toncoin": "TON",
}  # fmt: skip


def normalize_currency(raw: str | None) -> str | None:
    """Валюта к коду. Неизвестное возвращается как есть, в верхнем регистре:
    выбросить незнакомое значит потерять цену вместе с ним."""
    if not raw:
        return None
    key = raw.strip().lower()
    if key in CURRENCIES:
        return CURRENCIES[key]
    # Целыми словами, а не подстрокой: «usd» внутри «usdt» делал из USDT
    # доллар (эталонный прогон 23.09). Знаки валют — отдельно, они не слова.
    words = re.findall(r"[a-zа-яё]+", key)
    for word in words:
        if word in CURRENCIES:
            return CURRENCIES[word]
    # Падежи и формы: «рублей», «долларов», «евро» — по основе, но только
    # после точного совпадения, иначе «usdt» снова стал бы долларом.
    for word in words:
        for token, code in CURRENCIES.items():
            if len(token) >= 3 and token.isalpha() and word.startswith(token):
                return code
    for sign in ("₮", "₿", "$", "€", "£", "₽", "zł"):
        if sign in key:
            return CURRENCIES[sign]
    return raw.strip().upper()[:8]


def as_price(raw: Any) -> Decimal | None:
    """Число в цену. Мусор — это `None`, а не ноль: ноль означал бы
    «размещают бесплатно»."""
    if raw is None or isinstance(raw, bool):
        return None
    try:
        value = Decimal(_plain_number(str(raw)))
    except (InvalidOperation, ValueError):
        # Модель вернула на месте цены что-то, что числом не является.
        # Молча это не пропускаем: если такое стало частым, сломался
        # разбор, а не письма.
        logger.warning("%s: в поле цены не число — %r", TOPIC, raw)
        return None
    return value if value > 0 else None


def _plain_number(raw: str) -> str:
    """Число в европейской и английской записи — к виду, понятному `Decimal`.

    Раньше запятая просто выбрасывалась: «120,50 €» становилось 12050, а
    «1.200 €» — 1,2. Правило: при двух видах разделителей десятичный — тот,
    что последним; при одном — группы по три цифры это тысячи, иначе дробь.
    Первая группа не с нуля: «0.005 BTC» — дробь, а не пять.
    """
    text = re.sub(r"[\s\u00a0'’]", "", raw.strip())
    if "," in text and "." in text:
        decimal = "," if text.rfind(",") > text.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        return text.replace(thousands, "").replace(decimal, ".")
    for mark in (",", "."):
        if mark in text:
            if re.fullmatch(rf"[1-9]\d{{0,2}}(\{mark}\d{{3}})+", text):
                return text.replace(mark, "")
            return text.replace(mark, ".")
    return text


#: Число в письме целиком: цифры с разделителями разрядов и дроби внутри.
_NUMBER = re.compile(r"\d(?:[\d.,'\u2019\u00a0\u202f ]*\d)?")


def numbers_in(text: str) -> set[Decimal]:
    """Все числа письма — каждое целиком, в европейской и английской записи."""
    found: set[Decimal] = set()
    for token in _NUMBER.findall(text):
        try:
            found.add(Decimal(_plain_number(token)))
        except (InvalidOperation, ValueError):
            logger.debug("%s: не число в письме — %r", TOPIC, token)
    return found


def appears_in(value: Decimal, text: str) -> bool:
    """Встречается ли число в письме — ЦЕЛИКОМ.

    Главная проверка файла. «1,200», «1.200», «1 200» и «1200» — одно число,
    «120,50» и «120.5» — тоже, а «1250» вместо «1200» — другое.

    ⚠ Сравниваются числа, а не подстроки цифр. Подстрокой «120» находилось
    внутри «120,50»: модель урезала цену до целых, проверка это пропускала,
    и неверная цена ложилась в базу сама (эталонный прогон 23.09). До того
    бралась только целая часть, и у «0,05 BTC» это был «0» — проверка
    проходила на любом письме, где есть ноль.
    """
    return value in numbers_in(text)


#: Число рядом с валютой: знак или слово сразу до или сразу после.
_AMOUNT = re.compile(
    r"(?P<pre>[$€£₽₮₿]|\b[^\W\d_]{3,5})?[ \u00a0]?"
    r"(?P<num>\d(?:[\d.,'\u2019\u00a0\u202f ]*\d)?)"
    r"[ \u00a0]?(?P<post>[$€£₽₮₿]|zł|[^\W\d_]+)?"
)


def _is_currency(token: str | None) -> bool:
    if not token:
        return False
    word = token.lower()
    if word in CURRENCIES:
        return True
    # Падежи — только у кириллических основ («рублей», «долларов»): латинская
    # основа по префиксу сделала бы валютой «tons» и «ethical».
    return any(not key.isascii() and key.isalpha() and word.startswith(key) for key in CURRENCIES)


def amounts_in(text: str) -> set[Decimal]:
    """Денежные суммы письма — числа, у которых рядом стоит валюта.

    Отдельно от `numbers_in`: там и «TRC20», и «30 дней», а здесь нужны
    только цены — чтобы понять, из скольких модель выбирала.
    """
    found: set[Decimal] = set()
    for match in _AMOUNT.finditer(text):
        if not (_is_currency(match["pre"]) or _is_currency(match["post"])):
            continue
        try:
            found.add(Decimal(_plain_number(match["num"])))
        except (InvalidOperation, ValueError):
            logger.debug("%s: не сумма в письме — %r", TOPIC, match["num"])
    return found
