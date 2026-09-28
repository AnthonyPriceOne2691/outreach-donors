"""Деньги в письме: валюта к коду, число к `Decimal`, суммы и все числа.

Отдельно от разбора, потому что здесь нет модели — только проверяемые
правила, на которых стоят проверки поверх её самооценки: названная цена
обязана встречаться в письме, а из нескольких сумм берётся наименьшая.

**Валюты нужны все, что встречаются на рынках** (`serp/markets.py`).
Незнакомая валюта — не мелочь: число рядом с ней суммой не считается,
и тогда автоответ с ценой в рэндах уходит в тишину, а из «главная R1 200,
блог R350» модель берёт любую цену без проверки «взята не наименьшая».
До 28.09.2026 словарь знал доллар, евро, фунт, рубль, злотый и крипту,
а «R$ 500» читалось долларом США.

**Неоднозначное решается написанием, а не догадкой о стране.**

* Код, совпадающий со словом языка рынка, — валюта только заглавными
  (`CASE_SENSITIVE`): итальянское «dai 100 euro» — «от 100 евро», а не
  стейблкоин DAI; «try 2 posts» — не турецкая лира, «nok» по-норвежски —
  «наверное». Тикер и код пишут заглавными — так их и узнаём.
* Одиночная «R» — рэнд только вплотную перед числом («R500», «R 500»):
  после числа это буква, а слово «real» не берётся вовсе — английское.
* Крона и песо без страны (`UNCODED`): «kr» — датская, норвежская или
  шведская, «pesos» — мексиканский, аргентинский, чилийский, колумбийский
  или филиппинский. Суммой такое число считается, а кода не получает:
  код по стране донора был бы догадкой, записанной ценой в карточку.
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
#: Ключи — в нижнем регистре; то, что валютой бывает только заглавными, —
#: в `CASE_SENSITIVE`.
CURRENCIES: dict[str, str] = {
    # Доллар США. Голый «$» — тоже он: так пишут на главном рынке, а доллары
    # других стран носят свою приставку (ниже, и длинные знаки ищутся первыми).
    "$": "USD", "us$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD",
    "доллар": "USD", "долар": "USD", "dólar": "USD", "dolar": "USD", "dolares": "USD",
    "dolary": "USD", "dolarów": "USD", "dolarů": "USD", "dolari": "USD", "dolara": "USD",
    "dollari": "USD", "dollaro": "USD", "dollár": "USD", "dollaria": "USD",
    # Евро — и его имя на языках еврозоны из рынков.
    "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "евро": "EUR", "euroa": "EUR",
    "eurot": "EUR", "eura": "EUR", "eurų": "EUR", "eurai": "EUR", "eiro": "EUR",
    "evro": "EUR", "evra": "EUR", "evrov": "EUR", "ευρώ": "EUR",
    "£": "GBP", "gbp": "GBP", "pound": "GBP", "pounds": "GBP", "sterling": "GBP", "фунт": "GBP",
    "₽": "RUB", "rub": "RUB", "rouble": "RUB", "roubles": "RUB", "руб": "RUB",
    "zł": "PLN", "pln": "PLN", "zloty": "PLN", "zlotych": "PLN", "злот": "PLN",
    # Доллары других стран — только со своей приставкой.
    "c$": "CAD", "ca$": "CAD", "cad": "CAD", "a$": "AUD", "au$": "AUD", "aud": "AUD",
    "nz$": "NZD", "nzd": "NZD", "s$": "SGD", "sgd": "SGD", "hk$": "HKD", "hkd": "HKD",
    "mx$": "MXN", "mxn": "MXN", "ar$": "ARS", "ars": "ARS", "col$": "COP", "clp": "CLP",
    "r$": "BRL", "brl": "BRL", "reais": "BRL",
    "zar": "ZAR", "rand": "ZAR",
    "₦": "NGN", "ngn": "NGN", "naira": "NGN",
    "chf": "CHF", "sfr": "CHF", "franken": "CHF", "franc": "CHF", "francs": "CHF",
    "franchi": "CHF",
    "sek": "SEK", "kronor": "SEK", "krona": "SEK", "dkk": "DKK",
    "czk": "CZK", "kč": "CZK", "korun": "CZK", "koruna": "CZK", "koruny": "CZK",
    "huf": "HUF", "forint": "HUF",
    "lei": "RON", "leu": "RON",
    "₺": "TRY", "lira": "TRY", "liras": "TRY",
    "₹": "INR", "inr": "INR", "rs": "INR", "rupee": "INR", "rupees": "INR",
    "¥": "JPY", "￥": "JPY", "円": "JPY", "jpy": "JPY", "yen": "JPY",
    "元": "CNY", "cny": "CNY", "rmb": "CNY", "yuan": "CNY",
    "₩": "KRW", "krw": "KRW",
    "rp": "IDR", "idr": "IDR", "rupiah": "IDR",
    "₱": "PHP",
    "฿": "THB", "thb": "THB", "baht": "THB",
    "₫": "VND", "vnd": "VND", "đồng": "VND",
    "₪": "ILS", "nis": "ILS", "shekel": "ILS", "shekels": "ILS",
    "aed": "AED", "dirham": "AED", "dirhams": "AED",
    "sar": "SAR", "riyal": "SAR", "riyals": "SAR",
    "₸": "KZT", "kzt": "KZT", "tenge": "KZT", "тенге": "KZT", "теңге": "KZT",
    "₴": "UAH", "uah": "UAH", "грн": "UAH", "грив": "UAH", "hryvnia": "UAH",
    "e£": "EGP", "egp": "EGP", "ksh": "KES", "myr": "MYR", "ringgit": "MYR",
    "s/": "PEN", "s/.": "PEN", "soles": "PEN",
    "bgn": "BGN", "лв": "BGN", "лева": "BGN",
    # Крипта — отдельные валюты, не доллар: USDT — это способ оплаты, и
    # подписать его долларом значит потерять, чем донор хочет получить деньги.
    "usdt": "USDT", "tether": "USDT", "₮": "USDT", "usdc": "USDC",
    "busd": "BUSD", "btc": "BTC", "bitcoin": "BTC", "₿": "BTC", "eth": "ETH",
    "ether": "ETH", "ethereum": "ETH", "bnb": "BNB", "trx": "TRX", "tron": "TRX",
    "ltc": "LTC", "litecoin": "LTC", "solana": "SOL", "toncoin": "TON",
}  # fmt: skip

#: Валюта — только в таком написании. Каждый ключ — слово какого-то языка
#: рынков: «dai» (ит. «от»), «sol» (исп. «солнце»), «ton» (фр. «твой»),
#: «try», «cop», «pen», «Ron», «ils» (фр. «они»), «nok» (норв. «наверное»),
#: «kes» (малайск. «случай»), «rm», «tl», «ft» (футы). Тикер и код пишут
#: заглавными, «Ft» — так венгры пишут форинт. «R» — рэнд, но только перед
#: числом (`_names_currency`).
CASE_SENSITIVE: dict[str, str] = {
    "DAI": "DAI", "SOL": "SOL", "TON": "TON", "TRY": "TRY", "TL": "TRY", "COP": "COP",
    "PEN": "PEN", "RON": "RON", "PHP": "PHP", "ILS": "ILS", "NOK": "NOK", "KES": "KES",
    "RM": "MYR", "Ft": "HUF", "R": "ZAR",
}  # fmt: skip

#: Валюта без страны: сумма есть, а кода нет — «KR» и «PESO» вместо догадки.
UNCODED: dict[str, str] = {
    "kr": "KR", "kroner": "KR", "krone": "KR", "pesos": "PESO", "peso": "PESO",
}  # fmt: skip

#: При разборе ответа модели регистр не важен: валютой слово назвала она.
_FOLDED = {token.lower(): code for token, code in CASE_SENSITIVE.items()}

#: Знаки — всё, что не слово. Длинные первыми: «R$» и «HK$» — не доллар
#: США, а «$» внутри них иначе находился бы раньше.
_SIGNS = tuple(sorted((t for t in CURRENCIES if not t.isalpha()), key=len, reverse=True))

#: Составные доллары и фунты: «R$», «HK$», «E£». В ответе модели их надо
#: узнать раньше слов — иначе в «500 R$» первой найдётся буква «r» (рэнд).
_COMPOUND = tuple(sign for sign in _SIGNS if len(sign) > 1 and sign[-1] in "$£")

#: Основы с падежами — только нелатинские: «рублей», «долларов», «złotych»,
#: «гривень», «5000円です». Латинская основа по префиксу сделала бы валютой
#: «tons» и «ethical».
_STEMS = tuple(t for t in CURRENCIES if not t.isascii() and t.isalpha())

_WORDS = re.compile(r"[^\W\d_]+")


def _exact(token: str) -> str | None:
    return CURRENCIES.get(token) or UNCODED.get(token) or _FOLDED.get(token)


def _by_compound_sign(key: str, _words: list[str]) -> str | None:
    """«500 R$» — реал, хотя внутри есть и «$», и буква «r»."""
    return next((CURRENCIES[sign] for sign in _COMPOUND if sign in key), None)


def _by_word(_key: str, words: list[str]) -> str | None:
    # Целыми словами, а не подстрокой: «usd» внутри «usdt» делал из USDT
    # доллар (эталонный прогон 23.09).
    return next((code for word in words if (code := _exact(word))), None)


def _by_stem(_key: str, words: list[str]) -> str | None:
    # Падежи и формы: «рублей», «долларов» — по основе, но только после
    # точного совпадения, иначе «usdt» снова стал бы долларом.
    return next(
        (
            code
            for word in words
            for token, code in CURRENCIES.items()
            if len(token) >= 3 and token.isalpha() and word.startswith(token)
        ),
        None,
    )


def _by_sign(key: str, _words: list[str]) -> str | None:
    return next((CURRENCIES[s] for s in _SIGNS if s in key), None)


def normalize_currency(raw: str | None) -> str | None:
    """Валюта к коду. Неизвестное возвращается как есть, в верхнем регистре:
    выбросить незнакомое значит потерять цену вместе с ним."""
    if not raw:
        return None
    key = raw.strip().lower()
    words = _WORDS.findall(key)
    exact = _exact(key)
    if exact:
        return exact
    for finder in (_by_compound_sign, _by_word, _by_stem, _by_sign):
        code = finder(key, words)
        if code:
            return code
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
    text = re.sub(r"[\s '’]", "", raw.strip())
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
_NUMBER = re.compile(r"\d(?:[\d.,'’   ]*\d)?")


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


#: Между валютой и числом — не больше одного пробела, в том числе
#: неразрывного: так пишут и «500 €», и «500 Kč».
_GAP = "[   ]?"
_SIGN_PATTERN = "|".join(re.escape(sign) for sign in _SIGNS)

#: Валюта перед числом: знак, короткое слово или код («Rs.», «kr.») —
#: не приклеенные к предыдущему слову, — или одиночная «R» рэнда.
_BEFORE = re.compile(
    rf"(?P<token>(?i:{_SIGN_PATTERN})|(?<![^\W\d_])(?:[^\W\d_]{{2,5}}\.?|R)){_GAP}\Z"
)
#: Валюта после числа: знак или слово целиком («5000円です» — основа «円»).
_AFTER = re.compile(rf"{_GAP}(?P<token>(?i:{_SIGN_PATTERN})|[^\W\d_]+)")

#: Сколько знаков перед числом смотреть: длиннее валюта не пишется, а
#: окно не даёт письму в двести тысяч знаков стоить секунд.
_WINDOW = 10


def _names_currency(token: str, *, before: bool) -> bool:
    """Называет ли слово или знак валюту. `before` — стоит ли оно перед
    числом: одиночная «R» — рэнд только там, после числа это буква."""
    word = token.rstrip(".")
    if word in CASE_SENSITIVE:
        return before or word != "R"
    key = word.lower()
    return key in CURRENCIES or key in UNCODED or key.startswith(_STEMS)


def _priced(text: str, start: int, end: int) -> bool:
    """Стоит ли вплотную к числу валюта — перед ним или после.

    Обе стороны смотрятся у каждого числа отдельно, ничего не поглощая:
    при разборе одним выражением знак после первого числа съедался им,
    и во «€100 €200» второй суммы не было.
    """
    before = _BEFORE.search(text, max(0, start - _WINDOW), start)
    if before is not None and _names_currency(before["token"], before=True):
        return True
    after = _AFTER.match(text, end)
    return after is not None and _names_currency(after["token"], before=False)


def amounts_in(text: str) -> set[Decimal]:
    """Денежные суммы письма — числа, у которых рядом стоит валюта.

    Отдельно от `numbers_in`: там и «TRC20», и «30 дней», а здесь нужны
    только цены — чтобы понять, из скольких модель выбирала.
    """
    found: set[Decimal] = set()
    for number in _NUMBER.finditer(text):
        if not _priced(text, number.start(), number.end()):
            continue
        try:
            found.add(Decimal(_plain_number(number.group(0))))
        except (InvalidOperation, ValueError):
            logger.debug("%s: не сумма в письме — %r", TOPIC, number.group(0))
    return found
