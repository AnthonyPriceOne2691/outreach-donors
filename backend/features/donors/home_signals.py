"""Главная страница как вторая сторона судьи: магазин ли это.

**Зачем, если судья уже читает выдачу.** Выдача показывает ту страницу
сайта, что пришла под наш ключ, — и у магазина со своим журналом это
статья. Замер 23.09 на кофемашинах: восемь магазинов прошли судью как
издания, потому что в выдачу попали их обзоры и инструкции. Способ
заработка — свойство сайта, а не страницы, и спрашивать о нём надо корень.

**Признаки только структурные и от языка не зависящие.** Разметка
schema.org (`Product` с ценой или артикулом, `*Store`, `OfferCatalog`), `og:type=product`,
движок магазина в `generator` и ссылка на корзину. Слова «корзина» на
разных языках — словарь рынка, а не ниши: он переносится на любую тему.

⚠ **Раздел `/shop` признаком НЕ считается.** Замер на 68 главных: он есть
у половины изданий — Stern, SZ, test.de продают подписки и мерч. `/checkout`
по той же причине: подписка оформляется через него же. Корзина — нет:
её держит тот, кто продаёт товары штуками.

Признак не выносит вердикт сам. Он либо подтверждает судью («продаёт
своё» и корзина — решено правилом), либо спорит с ним, и тогда домен
идёт к арбитру — см. `judging.py`.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
from selectolax.parser import HTMLParser

from backend.features.contacts.pages import PageFetcher

logger = logging.getLogger(__name__)

#: Признаки того, что нам отдали не страницу сайта, а отказ. Сравнение по
#: нижнему регистру, вхождением: формулировки у защит разные, а слова общие.
#:
#: ⚠ Без этой проверки судья выносит вердикт по тексту вроде «Access to this
#: page has been denied» — и иногда угадывает, что и есть худший случай:
#: замер соврал в свою пользу, а мы записали случайное попадание в точность.
#: Класс назван в каноне соседней системы (`gnc.com`), у нас пойман на живом
#: прогоне 23.09: `edmunds.com` со страницей 403 получил «площадка» (угадал),
#: `trivago.com` с той же страницей — «не площадка» (промахнулся). Ни то,
#: ни другое не знание.
DENIAL_MARKERS: tuple[str, ...] = (
    "access denied",
    "access to this page",
    "403",
    "forbidden",
    "attention required",
    "just a moment",
    "are you human",
    "verify you are human",
    "captcha",
    "bot detection",
    "request blocked",
    "unusual traffic",
    "not acceptable",
    "service unavailable",
    "site temporarily unavailable",
    # Заглушки защит, пойманные 23.09 на закрытых главных: браузер и простой
    # запрос получали их вместо сайта, и без маркера это был бы «текст».
    "ddos-guard",
    "human verification",
)


def looks_denied(text: str) -> bool:
    """Текст похож на отказ доступа, а не на страницу сайта."""
    lowered = text.lower()
    return any(marker in lowered for marker in DENIAL_MARKERS)


#: Типы schema.org, которые ставит только тот, кто продаёт у себя.
SHOP_TYPES: frozenset[str] = frozenset({"Product", "OfferCatalog", "Store", "OnlineStore"})

#: Движки магазинов в `<meta name="generator">`.
SHOP_ENGINES: tuple[str, ...] = ("shopify", "woocommerce", "magento", "bigcommerce", "prestashop",
                                 "shopware", "oxid", "gambio", "jtl-shop")  # fmt: skip

#: Корзина на языках рынков. ⚠ Без `shop`, `store`, `checkout` — см. шапку.
CART_PATH = re.compile(
    r"/(cart|basket|warenkorb|panier|carrito|carrello|koszyk|winkelwagen|kosik|kosar"
    r"|korzina|sepet|carrinho|varukorg|handlekurv|ostoskori|kurv)(?:[/?#.]|$)"
)

#: Корзина словом — у магазинов, где она нарисована скриптом, а не ссылкой.
#: Только ЦЕЛАЯ подпись кнопки или ссылки («Giỏ hàng», «Keranjang (2)»),
#: а не вхождение: «корзина потребителя» в статье — не корзина. Замер 23.09:
#: у 3 из 20 продавцов без других признаков, у 0 из 95 изданий.
CART_WORD = re.compile(
    r"^\s*(cart|basket|shopping cart|shopping bag|my cart|giỏ hàng|keranjang"
    r"|keranjang belanja|carrito|carrinho|warenkorb|panier|koszyk|корзина|cesta|sepet"
    r"|winkelwagen|varukorg|carrello)\s*(\(\d+\)|\d+)?\s*$",
    re.IGNORECASE,
)

#: Разметка услуги: так себя размечает тот, кто продаёт СВОЮ работу —
#: стоматология, агентство, программный сервис. Замер 23.09: у 5 из 15
#: компаний-услуг, у 0 из 19 изданий.
SERVICE_TYPES: frozenset[str] = frozenset({
    "LocalBusiness", "ProfessionalService", "FinancialService", "InsuranceAgency",
    "Dentist", "MedicalBusiness", "MedicalClinic", "LegalService",
    "SoftwareApplication", "WebApplication",
})  # fmt: skip

#: Путь продажи услуги. ⚠ Без `login`, `signup`, `register`: вход и
#: регистрация есть у изданий (nerdwallet, investopedia, wallethub) — замер
#: 23.09. Тарифы и демо — нет: их держит тот, кто продаёт свой сервис.
SERVICE_PATH = re.compile(
    r"/(pricing|demo|request-a-demo|book-a-demo|contact-sales|free-trial|get-started"
    r"|appointments?|book-appointment|get-a-quote|request-a-quote)(?:[/?#.]|$)"
)

#: Сколько текста главной уходит арбитру. Заголовок, описание и меню —
#: этого хватает, чтобы понять, чем сайт торгует, и не хватает, чтобы
#: утопить модель в подвале страницы.
MAX_NAV_ITEMS = 40


@dataclass(frozen=True, slots=True)
class HomeSignals:
    """Что сказала главная. `reached=False` — не открылась, судить нечем."""

    reached: bool
    shop: tuple[str, ...] = ()
    service: tuple[str, ...] = ()
    title: str = ""
    description: str = ""
    nav: tuple[str, ...] = field(default_factory=tuple)
    error: str = ""
    #: Откуда образ главной: `page` — сама страница, `index` — индекс поиска,
    #: когда сайт закрылся (`site_index.py`). В индексе нет структуры.
    via: str = "page"

    @property
    def is_shop(self) -> bool:
        return bool(self.shop)

    @property
    def sells(self) -> bool:
        """Главная структурно подтверждает продажу своего: товар или услугу."""
        return bool(self.shop or self.service)

    def as_dict(self) -> dict[str, Any]:
        """Для записи на домен: по этому видно, на чём стояло решение."""
        return {
            "reached": self.reached,
            "shop": list(self.shop),
            "service": list(self.service),
            "title": self.title[:200],
            "error": self.error,
            "via": self.via,
        }

    def as_text(self) -> str:
        """Главная глазами арбитра."""
        if self.via == "index":
            lines = [
                "(сайт закрыт от нас, это его главная и страницы в индексе поиска)",
                f"Заголовок: {self.title}",
                f"Описание: {self.description}",
            ]
            if self.nav:
                lines.append("Другие страницы: " + " · ".join(self.nav))
            return "\n".join(lines)
        lines = [f"Заголовок: {self.title}", f"Описание: {self.description}"]
        if self.nav:
            lines.append("Меню: " + " · ".join(self.nav))
        if self.shop or self.service:
            lines.append("Признаки продажи: " + ", ".join((*self.shop, *self.service)))
        return "\n".join(lines)


#: Поля товара, которые ставит продавец, а не обозреватель. `Product`
#: с одним рейтингом — разметка ОБЗОРА: замер 23.09, у сообщества
#: wunschkind-community ровно такая, и арбитр отрезал его как магазин.
SELLER_FIELDS: tuple[str, ...] = ("offers", "sku", "gtin", "gtin8", "gtin13", "gtin14", "mpn")


def _schema_items(tree: HTMLParser) -> list[dict[str, Any]]:
    """Все объекты JSON-LD, включая вложенные в `@graph` и `offers`."""
    found: list[dict[str, Any]] = []
    for node in tree.css('script[type="application/ld+json"]'):
        try:
            data = json.loads(node.text(strip=False) or "null")
        except ValueError:
            logger.debug("главная: битый JSON-LD пропущен")
            continue
        stack: list[Any] = [data]
        while stack:
            item = stack.pop()
            if isinstance(item, list):
                stack.extend(item)
            elif isinstance(item, dict):
                found.append(item)
                stack.extend(item[key] for key in ("@graph", "offers") if key in item)
    return found


def _schema_marks(tree: HTMLParser) -> set[str]:
    marks: set[str] = set()
    for item in _schema_items(tree):
        kind = item.get("@type")
        for value in kind if isinstance(kind, list) else [kind]:
            if not isinstance(value, str):
                continue
            if value == "Product" and not any(key in item for key in SELLER_FIELDS):
                continue
            # `ElectronicsStore`, `HomeGoodsStore` и прочие — подтипы `Store`.
            if value in SHOP_TYPES or value.endswith("Store"):
                marks.add(f"schema:{value}")
    return marks


def _shop_marks(tree: HTMLParser) -> list[str]:
    marks = sorted(_schema_marks(tree))
    for meta in tree.css('meta[name="generator"]'):
        engine = (meta.attributes.get("content") or "").lower()
        marks.extend(f"engine:{name}" for name in SHOP_ENGINES if name in engine)
    for meta in tree.css('meta[property="og:type"]'):
        if "product" in (meta.attributes.get("content") or "").lower():
            marks.append("og:product")
    carts = {
        match.group(1)
        for link in tree.css("a[href]")
        if (match := CART_PATH.search((link.attributes.get("href") or "").lower()))
    }
    marks.extend(f"cart:/{name}" for name in sorted(carts))
    if not carts and _has_cart_word(tree):
        marks.append("cart:слово")
    return marks


def _has_cart_word(tree: HTMLParser) -> bool:
    for node in tree.css("a, button"):
        text = " ".join((node.text(strip=True) or "").split())
        label = node.attributes.get("aria-label") or node.attributes.get("title") or ""
        if any(value and CART_WORD.match(value) for value in (text, label)):
            return True
    return False


def _types_of(item: dict[str, Any]) -> list[str]:
    kind = item.get("@type")
    values = kind if isinstance(kind, list) else [kind]
    return [value for value in values if isinstance(value, str)]


def _service_marks(tree: HTMLParser) -> list[str]:
    marks = {
        f"schema:{value}"
        for item in _schema_items(tree)
        for value in _types_of(item)
        if value in SERVICE_TYPES
    }
    marks |= {
        f"path:/{match.group(1)}"
        for link in tree.css("a[href]")
        if (match := SERVICE_PATH.search((link.attributes.get("href") or "").lower()))
    }
    return sorted(marks)


def _nav_texts(tree: HTMLParser) -> tuple[str, ...]:
    seen: list[str] = []
    for link in tree.css("nav a, header a"):
        text = " ".join(link.text(strip=True).split())
        if 2 <= len(text) <= 40 and text not in seen:
            seen.append(text)
        if len(seen) >= MAX_NAV_ITEMS:
            break
    return tuple(seen)


def read_home(html: str) -> HomeSignals:
    """Разбор готового HTML. Без сети — тесты гоняют его на образцах."""
    tree = HTMLParser(html)
    title_node = tree.css_first("title")
    description = tree.css_first('meta[name="description"]')
    return HomeSignals(
        reached=True,
        shop=tuple(_shop_marks(tree)),
        service=tuple(_service_marks(tree)),
        title=" ".join((title_node.text() if title_node else "").split())[:200],
        description=((description.attributes.get("content") if description else "") or "")[:400],
        nav=_nav_texts(tree),
    )


async def check_home(client: httpx.AsyncClient, host: str) -> HomeSignals:
    """Главная домена. Не бросает: не открылась — так и записано."""
    fetcher = PageFetcher(client, max_pages=1, max_attempts=4)
    try:
        page = await fetcher.home(host)
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("главная %s не открылась: %r", host, exc)
        return HomeSignals(reached=False, error=type(exc).__name__)
    if page is None:
        return HomeSignals(reached=False, error="закрылась" if fetcher.blocked else "не ответила")
    signals = read_home(page.html)
    if looks_denied(f"{signals.title} {signals.description}"):
        # Заглушка защиты с кодом 200 — это закрытая главная, а не её текст.
        return HomeSignals(reached=False, error=f"заглушка защиты: {signals.title[:40]!r}")
    return signals
