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

from backend.config import judge as judge_cfg
from backend.features.contacts.pages import CHALLENGE, PageFetcher

logger = logging.getLogger(__name__)


def home_timeout() -> httpx.Timeout:
    """Таймаут похода за главной: чтение — `JUDGE_HOME_TIMEOUT_SEC`, соединение — короче
    (`JUDGE_HOME_CONNECT_TIMEOUT_SEC`). Мёртвый сайт молчит на соединении, и четыре
    варианта адреса по двенадцать секунд стоили домену почти минуту (разбор 14.10.2026)."""
    return httpx.Timeout(judge_cfg.HOME_TIMEOUT_SEC, connect=judge_cfg.HOME_CONNECT_TIMEOUT_SEC)


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
#:
#: Голого «403» здесь нет (15.10.2026): вхождением оно ловило адрес, телефон и цену («ул. Абая
#: 403», «от 403 тг») — и сайт уходил человеку мимо судьи. Ошибку 403 ловят «access denied»,
#: «forbidden» и строка из одного «403» (`STUB_LINE`).
DENIAL_MARKERS: tuple[str, ...] = (
    "access denied",
    "access to this page",
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
    "human verification",
    # Заглушки, пойманные 15.10.2026 на главных сбора продаж: судья сегмента читал их как сам
    # сайт, и экспедитор с главной «BotBlocker security plugin» получил «не подходит». Фразы
    # целиком, а не слова: «checking» есть и у банка («Checking account»). Проверка «человек
    # ли вы» — на языке браузера, поэтому на нескольких языках.
    "botblocker",
    "checking your connection",
    "checking your browser",
    "checking if the site connection is secure",
    "verifying you are human",
    "ob sie ein mensch sind",
    "que vous êtes un humain",
    "que usted es un ser humano",
    "que você é humano",
    "che sei un essere umano",
    "доступ запрещ",
    "проверка браузера",
    "проверяем ваш браузер",
    "человек ли вы",
    "подтвердите, что вы человек",
    "подтвердите, что вы не робот",
    # Страница блокировки Imperva (бывший Distil), поймана 20.10.2026 на главных сбора продаж:
    # судья читал её как главную сайта. Фраза целиком: «pardon» бывает и в тексте сайта.
    "pardon our interruption",
)

#: Строка из одного слова ожидания или ошибки — заглушка, а не текст сайта: «Checking…»,
#: «Loading…», «Подождите…», «Einen Moment…», «请稍候…», «403». Сравнивается вся строка, а не
#: вхождение: «загрузка» бывает у склада, «проверка» — у сервиса проверки контрагентов, а «DDoS
#: protection» и «DDoS-Guard» — в описании любого хостинга (ревью #399): заглушка — только строка
#: из них одних или с именем защиты («DDoS protection by Cloudflare»). Подписи строк функция не знает: текст с
#: подписями («Заголовок главной: …») вызывающий проверяет по строкам — словами без подписи.
STUB_LINE = re.compile(
    r"^[\W_]*(?:"
    r"checking|loading|please wait|one moment|redirecting"
    r"|подождите|загрузка|проверка|перенаправление|один момент|секундочку"
    r"|einen moment|bitte warten|un momento|cargando|veuillez patienter|chargement|un instant"
    r"|carregando|aguarde|um momento|caricamento|attendere|un attimo|even geduld|een moment"
    r"|ładowanie|proszę czekać|chwileczkę|lütfen bekleyin|yükleniyor|күте тұрыңыз"
    r"|завантаження|зачекайте|加载中|请稍候|読み込み中|しばらくお待ちください"
    r"|ddos-guard|ddos protection(?: by (?:cloudflare|ddos-guard|qrator|stormwall|variti|imperva|sucuri))?"
    r"|(?:error |ошибка |http )?403(?: error| forbidden| ошибка)?"
    r")[\W_]*$",
    re.IGNORECASE,
)


def looks_denied(text: str) -> bool:
    """Текст похож на отказ доступа или заглушку защиты, а не на страницу сайта: маркер
    вхождением (`DENIAL_MARKERS`) — или строка целиком из слова ожидания (`STUB_LINE`)."""
    lowered = text.lower()
    if any(marker in lowered for marker in DENIAL_MARKERS):
        return True
    return any(STUB_LINE.match(line.strip()) for line in (text, *text.splitlines()))


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

#: Потолок видимого текста главной (`HomeSignals.text`) — против аномальной страницы,
#: а не ради экономии: кто читает текст, режет его своим потолком.
MAX_HOME_TEXT = 8000
#: Что не текст для глаз человека: код, стили, векторы, шаблоны, рамки — и меню
#: (меню у судьи — своя строка, `nav`).
_HIDDEN_TAGS = ["script", "style", "noscript", "template", "svg", "iframe", "nav"]
#: Элементы, с которых на экране начинается новый блок: между блоками — « · ».
_BLOCK_TAGS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "button", "caption", "dd", "div",
        "dl", "dt", "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4",
        "h5", "h6", "header", "hr", "label", "legend", "li", "main", "option", "p", "pre",
        "section", "summary", "table", "td", "th", "tr", "ul", "ol",
    }
)  # fmt: skip
#: Граница блока, пока пробелы не схлопнуты: перевод строки в исходнике — пробел, а не граница.
#: Разделитель абзацев Юникода: пробельный знак, которого в вёрстке почти не бывает.
_EDGE = " "
_SPACES = re.compile(rf"[^\S{_EDGE}]+")
_JOIN = " · "


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
    #: Видимый текст главной одной строкой (`visible_text`), до `MAX_HOME_TEXT` знаков. Его
    #: читает судья сегмента продаж. В запись на домен и арбитру доноров не идёт: `as_dict`
    #: и `as_text` — прежние.
    text: str = ""

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


def visible_text(html: str) -> str:
    """Видимый текст страницы одной строкой: тело без кода, стилей, векторов, шаблонов и меню,
    блоки через « · », пробелы схлопнуты, повтор блока подряд — один раз."""
    return _visible_of(HTMLParser(html))


def _visible_of(tree: HTMLParser) -> str:
    """`visible_text` по готовому дереву. Дерево меняется: вызывать последним."""
    tree.strip_tags(_HIDDEN_TAGS)
    root = tree.body or tree.root
    if root is None:
        return ""
    for node in [node for node in root.traverse() if node.tag in _BLOCK_TAGS]:
        node.insert_before(_EDGE)
        node.insert_after(_EDGE)
    blocks: list[str] = []
    for piece in _SPACES.sub(" ", root.text(separator=" ")).split(_EDGE):
        block = piece.strip()
        if block and (not blocks or blocks[-1] != block):
            blocks.append(block)
    return _JOIN.join(blocks)


def read_home(html: str) -> HomeSignals:
    """Разбор готового HTML. Без сети — тесты гоняют его на образцах."""
    tree = HTMLParser(html)
    title_node = tree.css_first("title")
    description = tree.css_first('meta[name="description"]')
    shop, service, nav = tuple(_shop_marks(tree)), tuple(_service_marks(tree)), _nav_texts(tree)
    return HomeSignals(
        reached=True,
        shop=shop,
        service=service,
        title=" ".join((title_node.text() if title_node else "").split())[:200],
        description=((description.attributes.get("content") if description else "") or "")[:400],
        nav=nav,
        # Последним: текст снимается с того же дерева, вычищая из него код и меню.
        text=_visible_of(tree)[:MAX_HOME_TEXT],
    )


async def check_home(client: httpx.AsyncClient, host: str) -> HomeSignals:
    """Главная домена. Не бросает: не открылась — так и записано."""
    fetcher = PageFetcher(client, max_pages=1, max_attempts=4)
    try:
        page = await fetcher.home(host)
    except (httpx.HTTPError, ValueError) as exc:
        logger.info("главная %s не открылась: %r", host, exc)
        return HomeSignals(reached=False, error=type(exc).__name__)
    if page is None and fetcher.refusal == CHALLENGE:
        # Страницу проверки «вы не робот» узнаёт сам обход (`pages.is_challenge`) и главной
        # её не отдаёт; для разбора главной это та же заглушка защиты, что и с кодом 200.
        return HomeSignals(
            reached=False, error=f"заглушка защиты: {fetcher.challenge_title[:40]!r}"
        )
    if page is None:
        return HomeSignals(reached=False, error="закрылась" if fetcher.blocked else "не ответила")
    signals = read_home(page.html)
    if looks_denied(f"{signals.title} {signals.description}"):
        # Заглушка защиты с кодом 200 — это закрытая главная, а не её текст.
        return HomeSignals(reached=False, error=f"заглушка защиты: {signals.title[:40]!r}")
    return signals
