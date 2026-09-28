"""Суммы и валюты рынков: число рядом с валютой — сумма, где бы ни писали.

Незнакомая валюта — не мелочь: число рядом с ней суммой не считается,
и автоответ с ценой в рэндах уходил бы в тишину, а из двух цен в кронах
модель брала бы любую без проверки «взята не наименьшая». До 28.09.2026
словарь знал шесть валют и крипту, а «R$ 500» читалось долларом США.

Второй класс здесь — неоднозначное: слово языка рынка, совпадающее с кодом
валюты («dai» по-итальянски, «try» по-английски, «nok» по-норвежски),
суммой не делает ничего.
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest
from backend.features.replies.money import amounts_in, normalize_currency
from backend.features.replies.quoting import written_by_hand
from backend.features.serp.markets import SERP_LANGUAGE

GOLDEN = Path(__file__).parent.parent / "scripts" / "data" / "reply_parse_golden.jsonl"

#: Сумма на каждом рынке выдачи — так, как её пишут там:
#: страна → текст, валюта в нём, её код.
BY_MARKET: dict[str, tuple[str, str, str]] = {
    "ae": ("Sponsored post: AED 550", "AED", "AED"),
    "ar": ("Nota patrocinada: ARS 50.000", "ARS", "ARS"),
    "at": ("Gastartikel 150 €", "€", "EUR"),
    "au": ("Sponsored post A$ 300", "A$", "AUD"),
    "be": ("Prijs: 120 euro", "euro", "EUR"),
    "bg": ("Цена: 200 лв.", "лв", "BGN"),
    "br": ("Artigo patrocinado: R$ 1.500", "R$", "BRL"),
    "ca": ("Sponsored post C$ 200", "C$", "CAD"),
    "ch": ("Gastbeitrag 250 CHF", "CHF", "CHF"),
    "cl": ("Artículo patrocinado: 90.000 CLP", "CLP", "CLP"),
    "co": ("Artículo: COP 350.000", "COP", "COP"),
    "cz": ("Článek za 5 000 Kč", "Kč", "CZK"),
    "de": ("Gastartikel 120 Euro", "Euro", "EUR"),
    "dk": ("Artikel: 1.500 DKK", "DKK", "DKK"),
    "ee": ("Artikkel 100 eurot", "eurot", "EUR"),
    "eg": ("Article: E£ 2,500", "E£", "EGP"),
    "es": ("Artículo patrocinado: 80 €", "€", "EUR"),
    "fi": ("Artikkeli 120 euroa", "euroa", "EUR"),
    "fr": ("Article sponsorisé : 150 €", "€", "EUR"),
    "gb": ("Sponsored post £250", "£", "GBP"),
    "gr": ("Άρθρο 100 ευρώ", "ευρώ", "EUR"),
    "hr": ("Članak 90 eura", "eura", "EUR"),
    "hu": ("Cikk ára 45 000 Ft", "Ft", "HUF"),
    "id": ("Artikel: Rp 750.000", "Rp", "IDR"),
    "ie": ("Sponsored post €200", "€", "EUR"),
    "il": ("Sponsored post ₪ 400", "₪", "ILS"),
    "in": ("Guest post Rs. 5,000", "Rs.", "INR"),
    "it": ("Articolo sponsorizzato 100 euro", "euro", "EUR"),
    "jp": ("記事掲載は50000円です", "円", "JPY"),
    "ke": ("Sponsored article KSh 8,000", "KSh", "KES"),
    "kz": ("Статья — 25 000 тенге", "тенге", "KZT"),
    "lt": ("Straipsnis 80 eurų", "eurų", "EUR"),
    "lv": ("Raksts 70 eiro", "eiro", "EUR"),
    "mx": ("Artículo patrocinado: 1,500 MXN", "MXN", "MXN"),
    "my": ("Artikel tajaan RM 300", "RM", "MYR"),
    "ng": ("Sponsored post ₦50,000", "₦", "NGN"),
    "nl": ("Gastblog 95 euro", "euro", "EUR"),
    "no": ("Artikkel: NOK 1 200", "NOK", "NOK"),
    "nz": ("Sponsored post NZ$ 180", "NZ$", "NZD"),
    "pe": ("Artículo patrocinado: S/ 300", "S/", "PEN"),
    "ph": ("Sponsored post ₱2,500", "₱", "PHP"),
    "pl": ("Artykuł sponsorowany 400 zł", "zł", "PLN"),
    "pt": ("Artigo patrocinado 90 euros", "euros", "EUR"),
    "ro": ("Articol sponsorizat 300 lei", "lei", "RON"),
    "sa": ("Sponsored article SAR 600", "SAR", "SAR"),
    "se": ("Artikel: 1 500 kronor", "kronor", "SEK"),
    "sg": ("Sponsored post S$ 250", "S$", "SGD"),
    "si": ("Članek 80 evrov", "evrov", "EUR"),
    "sk": ("Článok 90 EUR", "EUR", "EUR"),
    "th": ("Sponsored post ฿5,000", "฿", "THB"),
    "tr": ("Tanıtım yazısı 2.500 TL", "TL", "TRY"),
    "ua": ("Стаття — 3 000 грн", "грн", "UAH"),
    "us": ("Sponsored post $150", "$", "USD"),
    "vn": ("Bài PR 2.000.000₫", "₫", "VND"),
    "za": ("Sponsored post R1 500", "R", "ZAR"),
}


def test_every_market_has_its_money() -> None:
    """Рынок без записи здесь — рынок, где цена донора суммой не считается."""
    assert set(BY_MARKET) == set(SERP_LANGUAGE)


@pytest.mark.parametrize(("text", "token", "code"), BY_MARKET.values(), ids=BY_MARKET.keys())
def test_market_price_is_a_sum_with_its_own_code(text: str, token: str, code: str) -> None:
    assert len(amounts_in(text)) == 1
    assert normalize_currency(token) == code


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        ("R$", "BRL"), ("r$", "BRL"), ("500 R$", "BRL"), ("US$", "USD"), ("C$", "CAD"),
        ("HK$", "HKD"), ("S/", "PEN"), ("S/.", "PEN"), ("soles", "PEN"), ("Kč", "CZK"),
        ("Ft", "HUF"), ("lei", "RON"), ("₺", "TRY"), ("TL", "TRY"), ("₹", "INR"),
        ("Rs.", "INR"), ("¥", "JPY"), ("円", "JPY"), ("元", "CNY"), ("RMB", "CNY"),
        ("₩", "KRW"), ("Rp", "IDR"), ("₱", "PHP"), ("฿", "THB"), ("₫", "VND"),
        ("₪", "ILS"), ("₸", "KZT"), ("₴", "UAH"), ("гривень", "UAH"), ("R", "ZAR"),
        ("rand", "ZAR"), ("₦", "NGN"), ("naira", "NGN"), ("Franken", "CHF"),
        ("kronor", "SEK"), ("dólares", "USD"), ("reais", "BRL"), ("euroa", "EUR"),
        ("MXN", "MXN"), ("DAI", "DAI"), ("TON", "TON"),
    ],
)  # fmt: skip
def test_currency_as_the_model_names_it(raw: str, code: str) -> None:
    """Разбор ответа модели: она называет валюту «как написано», а в базе
    лежит одно значение — иначе фильтр по валюте не работает."""
    assert normalize_currency(raw) == code


@pytest.mark.parametrize("raw", ["kr", "Kr.", "kroner", "krone"])
def test_crown_without_a_country_stays_a_crown(raw: str) -> None:
    """«kr» — датская, норвежская или шведская крона. Код по стране донора
    был бы догадкой, записанной в карточку ценой."""
    assert normalize_currency(raw) == "KR"
    assert amounts_in(f"Artikel: 1 500 {raw}") == {Decimal("1500")}


@pytest.mark.parametrize("raw", ["pesos", "peso"])
def test_peso_without_a_country_stays_a_peso(raw: str) -> None:
    """Песо — мексиканский, аргентинский, чилийский, колумбийский или
    филиппинский: без страны кода нет, а сумма есть."""
    assert normalize_currency(raw) == "PESO"
    assert amounts_in(f"El artículo cuesta 1.500 {raw}") == {Decimal("1500")}


def test_real_sign_is_not_a_us_dollar() -> None:
    """«R$» — бразильский реал: прочитанный долларом США, он лёг бы в карточку
    ценой в пять раз выше названной."""
    assert normalize_currency("R$") == "BRL"
    assert amounts_in("Artigo patrocinado: R$ 1.500") == {Decimal("1500")}


# --- неоднозначное -----------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "want"),
    [
        # Рэнд — одиночная «R» вплотную перед числом, и только перед ним.
        ("Sponsored post R500", {"500"}),
        ("Sponsored post R 500", {"500"}),
        ("Row 500 R", set()),
        # «real» — английское слово, валютой не считается ни перед, ни после.
        ("real 500 visitors", set()),
        ("we get 500 real visitors", set()),
        # Итальянское «dai» — предлог: «от 100 евро», «от 3 до 5 дней».
        ("Prezzi dai 100 euro in su", {"100"}),
        ("Pubblichiamo dai 3 ai 5 giorni lavorativi", set()),
        ("Dai 3 ai 5 giorni lavorativi", set()),
        ("Pagamento: 100 DAI", {"100"}),
        # Коды-слова — валюта только заглавными.
        ("You can try 2 posts first", set()),
        ("Fiyat: TRY 2.500", {"2500"}),
        ("The banner is 5 ft wide", set()),
        ("Vi har nok 3 ledige plasser", set()),
        ("Ils ont 3 sites", set()),
        ("Terdapat 5 kes baru", set()),
        ("Al sol 5 días", set()),
        ("We have 5 tons of ethical content", set()),
        ("Pay 5 SOL on Solana", {"5"}),
        ("Размещение — 25 TON", {"25"}),
        ("Price: PHP 1,500", {"1500"}),
        ("our stack is php 8", set()),
    ],
)
def test_word_of_a_market_language_is_not_a_currency(text: str, want: set[str]) -> None:
    assert amounts_in(text) == {Decimal(value) for value in want}


def test_sign_of_the_next_sum_is_not_eaten_by_the_previous() -> None:
    """Раньше одно выражение разбирало «число и валюта после него», и знак
    второй суммы уходил первой: во «€100 €200» вторая цена пропадала,
    а с ней — проверка «несколько цен, взята не наименьшая»."""
    assert amounts_in("Homepage €100 €200 blog") == {Decimal("100"), Decimal("200")}
    assert amounts_in("100 EUR 200 EUR") == {Decimal("100"), Decimal("200")}


# --- эталон разбора ------------------------------------------------------------


def _golden() -> list[dict[str, object]]:
    return [json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line]


def test_every_golden_price_written_in_digits_is_a_sum() -> None:
    """Эталон — ворота разбора цены: цена, названная цифрами и с валютой,
    обязана считаться суммой, иначе проверка «взята не наименьшая» её
    не видит. Цены словами и без валюты — законное исключение."""
    missed = {}
    for case in _golden():
        expect = case["expect"]
        assert isinstance(expect, dict)
        text = written_by_hand(str(case["text"]))
        prices = [expect[key] for key in ("price_white", "price_grey") if expect[key] is not None]
        if not expect["currency"] or not any(char.isdigit() for char in text):
            continue
        found = amounts_in(text)
        lost = [price for price in prices if Decimal(str(price)) not in found]
        if lost:
            missed[case["id"]] = lost
    assert not missed
