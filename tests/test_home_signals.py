"""Главная как вторая сторона судьи: что считается признаком магазина.

Ловушка здесь одна и дорогая: признак, который есть и у изданий.
Замер 23.09 на 68 главных — раздел `/shop` у половины изданий, `/checkout`
у тех, кто продаёт подписку. Признаком магазина они стали бы ложным
обвинением каждому второму донору.
"""

from __future__ import annotations

import pytest
from backend.features.donors.home_signals import read_home


def page(body: str, head: str = "") -> str:
    return f"<html><head><title>Главная</title>{head}</head><body>{body}</body></html>"


@pytest.mark.parametrize(
    ("html", "mark"),
    [
        (page('<a href="/warenkorb">Warenkorb</a>'), "cart:/warenkorb"),
        (page('<a href="https://x.test/cart?ref=1">Cart</a>'), "cart:/cart"),
        (page('<a href="/panier/">Panier</a>'), "cart:/panier"),
        (
            page(
                "",
                '<script type="application/ld+json">{"@type": "Product", "sku": "A1"}</script>',
            ),
            "schema:Product",
        ),
        (
            page(
                "",
                '<script type="application/ld+json">'
                '{"@graph": [{"@type": ["Organization", "ElectronicsStore"]}]}</script>',
            ),
            "schema:ElectronicsStore",
        ),
        (page("", '<meta name="generator" content="Shopware 6">'), "engine:shopware"),
        (page("", '<meta property="og:type" content="product">'), "og:product"),
    ],
)
def test_shop_mark_is_found(html: str, mark: str) -> None:
    assert mark in read_home(html).shop


@pytest.mark.parametrize(
    "href",
    ["/shop", "/shop/abo", "/store", "/checkout", "/produkte", "/cartoons/", "/basketball"],
)
def test_publisher_shop_section_is_not_a_mark(href: str) -> None:
    """Подписка, мерч и слова, начинающиеся с «cart», — не корзина."""
    signals = read_home(page(f'<nav><a href="{href}">Раздел</a></nav>'))
    assert signals.shop == ()
    assert not signals.is_shop


def test_broken_markup_does_not_break_parsing() -> None:
    html = page(
        '<a href="/basket">Basket</a>',
        '<script type="application/ld+json">{не json</script>',
    )
    assert read_home(html).shop == ("cart:/basket",)


def test_arbiter_sees_menu_and_marks() -> None:
    html = page(
        '<nav><a href="/kaffee">Kaffee</a><a href="/maschinen">Maschinen</a>'
        '<a href="/warenkorb">Warenkorb</a></nav>',
        '<meta name="description" content="Rösterei aus Wien">',
    )
    text = read_home(html).as_text()
    assert "Rösterei aus Wien" in text
    assert "Kaffee · Maschinen" in text
    assert "cart:/warenkorb" in text


def test_product_with_only_rating_is_a_review_not_a_shop() -> None:
    """Так размечают обзор, а не товар на продажу. Замер 23.09: сообщество
    wunschkind-community с такой разметкой арбитр отрезал как магазин."""
    html = page(
        "",
        '<script type="application/ld+json">'
        '{"@type": "Product", "name": "X", "aggregateRating": {"ratingValue": 4}}</script>',
    )
    assert read_home(html).shop == ()


@pytest.mark.parametrize(
    ("html", "mark"),
    [
        (page('<a href="/pricing">Pricing</a>'), "path:/pricing"),
        (page('<a href="https://x.test/contact-sales/">Talk to sales</a>'), "path:/contact-sales"),
        (page('<a href="/book-appointment">Book</a>'), "path:/book-appointment"),
        (
            page("", '<script type="application/ld+json">{"@type": "Dentist"}</script>'),
            "schema:Dentist",
        ),
        (
            page(
                "", '<script type="application/ld+json">{"@type": "SoftwareApplication"}</script>'
            ),
            "schema:SoftwareApplication",
        ),
    ],
)
def test_service_mark_is_found(html: str, mark: str) -> None:
    """Услуга без корзины: стоматология, агентство, программный сервис.
    23.09 модель пропустила восемь таких блогов как издания."""
    signals = read_home(html)
    assert mark in signals.service
    assert signals.sells


@pytest.mark.parametrize("href", ["/login", "/signup", "/register", "/subscribe", "/newsletter"])
def test_login_and_signup_are_not_service_marks(href: str) -> None:
    """Вход и регистрация есть у изданий — nerdwallet, investopedia, wallethub."""
    assert read_home(page(f'<a href="{href}">x</a>')).service == ()
