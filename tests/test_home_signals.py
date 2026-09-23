"""Главная как вторая сторона судьи: что считается признаком магазина.

Ловушка здесь одна и дорогая: признак, который есть и у изданий.
Замер 23.09 на 68 главных — раздел `/shop` у половины изданий, `/checkout`
у тех, кто продаёт подписку. Признаком магазина они стали бы ложным
обвинением каждому второму донору.
"""

from __future__ import annotations

import asyncio

import pytest
from backend.features.contacts.pages import FetchedPage, PageFetcher
from backend.features.core.domain import PageKind
from backend.features.donors.home_signals import check_home, read_home
from backend.features.donors.site_index import index_homes
from backend.features.serp.protocol import SerpResult


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


@pytest.mark.asyncio
@pytest.mark.parametrize("title", ["Just a moment...", "DDoS-Guard", "Human Verification"])
async def test_protection_stub_is_a_closed_home(
    monkeypatch: pytest.MonkeyPatch, title: str
) -> None:
    """Заглушка защиты с кодом 200 — это закрытая главная, а не её текст:
    23.09 kingston.com и bitpapa.com отдали такие вместо сайта."""

    async def stub(self: PageFetcher, host: str) -> FetchedPage:
        return FetchedPage(
            url=f"https://{host}/", kind=PageKind.HOME, html=page("", "").replace("Главная", title)
        )

    monkeypatch.setattr(PageFetcher, "home", stub)
    signals = await check_home(None, "closed.test")  # type: ignore[arg-type]
    assert not signals.reached


def test_index_home_takes_root_and_page_titles() -> None:

    class Provider:
        spent = 0.0

        async def search(
            self, keywords: list[str], country: str, **_: object
        ) -> dict[str, list[SerpResult]]:
            self.spent += 0.01
            return {
                "site:shop.test": [
                    SerpResult(1, "https://shop.test/cctv", "CCTV, harga murah"),
                    SerpResult(2, "https://shop.test/", "Shop Test", "Toko online"),
                    SerpResult(3, "https://shop.test/hp", "Beli Handphone"),
                ]
            }

    homes, cost = asyncio.run(index_homes(Provider(), ["shop.test", "none.test"]))  # type: ignore[arg-type]
    home = homes["shop.test"]
    assert home.title == "Shop Test", "корень, а не первая позиция"
    assert home.nav == ("CCTV, harga murah", "Beli Handphone")
    assert home.via == "index"
    assert "none.test" not in homes
    assert cost == pytest.approx(0.01)


@pytest.mark.parametrize("label", ["Giỏ hàng", "Keranjang (2)", "Cart", "Warenkorb 0"])
def test_cart_word_on_a_button_is_a_shop_mark(label: str) -> None:
    """Корзина, нарисованная скриптом: ссылки нет, есть кнопка со словом."""
    assert "cart:слово" in read_home(page(f"<button>{label}</button>")).shop


def test_cart_word_inside_a_sentence_is_not_a_mark() -> None:
    """«Корзина потребителя подорожала» — статья, а не магазин."""
    html = page('<a href="/news/1">Корзина потребителя подорожала на 5%</a>')
    assert read_home(html).shop == ()
