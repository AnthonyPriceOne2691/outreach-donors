"""Что видит судья рекламодателей: страница донора и главная домена — строками с нашими подписями.

**Контекст дополучается при суде, а не хранится при обходе** (проект автоматизации спорных,
02.11.2026). В `outlinks` есть адрес, анкор и пометки ссылки, но нет текста вокруг неё: хранить его
у сотен тысяч ссылок ради малой доли спорных — сотни мегабайт рядом с правилом «сырой HTML не
храним», а у собранных спорных текста нет всё равно. Поэтому судья открывает лучшую страницу
кандидата сам — только обычным запросом каскада (`fetch.PageCascade` без журнала расхода, браузера,
прокси и сервиса рендеринга), — и главную домена общим разбором главной (`donors/home_signals`):
два запроса на строку, денег ноль.

**Строка — кусок текста со своей подписью** (`Абзац со ссылкой: …`). Подписи — наши слова, а не
сайта: цитировать их нельзя, и сверка цитаты снимает их со своей стороны (`shared/quote.unlabeled`
с нашими подписями `LABELS`, а не чужими). Цитата ищется внутри одной строки своей стороны: слова о
ссылке — в строках статьи, слова о домене — в строках главной.

**Судить нечем — не суд.** Страница не открылась, статьи на ней нет или ссылки на домен на ней
больше нет — `blind`; вместо страницы заглушка защиты или отказ доступа — `denied`. Ни вердикта, ни
кэша, ни токенов: такой случай решает человек. Главная не открылась — судья видит одну статью, и
факты о домене остаются недоказанными: совет по ним таблица правил не ставит.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import httpx
from selectolax.parser import HTMLParser, Node

from backend.features.crawl.article import extract_article
from backend.features.crawl.fetch import FetchOutcome, PageCascade
from backend.features.crawl.proxies import ProxyPool
from backend.features.donors.home_signals import HomeSignals, check_home, looks_denied
from backend.shared.quote import quote_holds

logger = logging.getLogger(__name__)

#: Подписи строк статьи донора.
ARTICLE_TITLE = "Заголовок статьи"
ARTICLE_START = "Начало статьи"
LINK_PARAGRAPH = "Абзац со ссылкой"
ARTICLE_END = "Конец статьи"
#: Подписи строк главной домена.
HOME_TITLE = "Заголовок главной"
HOME_DESCRIPTION = "Описание главной"
HOME_MENU = "Меню главной"
HOME_TEXT = "Текст главной"
ARTICLE_LABELS = (ARTICLE_TITLE, ARTICLE_START, LINK_PARAGRAPH, ARTICLE_END)
HOME_LABELS = (HOME_TITLE, HOME_DESCRIPTION, HOME_MENU, HOME_TEXT)
#: Все наши подписи — их снимает сверка цитаты (`shared/quote.unlabeled`).
LABELS = (*ARTICLE_LABELS, *HOME_LABELS)

#: Потолки строк, знаков. Абзац со ссылкой — главное: в нём роль ссылки. Начало и конец статьи —
#: где сайты ставят раскрытие рекламы («Реклама», «Anzeige», «Sponsored» — на любом языке).
TITLE_CHARS = 200
PARAGRAPH_CHARS = 700
EDGE_CHARS = 300
HOME_DESCRIPTION_CHARS = 300
HOME_MENU_CHARS = 300
HOME_TEXT_CHARS = 700
#: Потолок стороны целиком: статья и главная — до 1500 знаков каждая.
SIDE_CHARS = 1500
#: Блок, текст которого — «абзац со ссылкой»: ближайший к ссылке предок из этих.
BLOCK_TAGS = frozenset(
    {"p", "li", "td", "th", "dd", "dt", "blockquote", "figcaption", "h1", "h2", "h3", "h4", "h5",
     "h6", "div", "section", "article"}
)  # fmt: skip
#: Язык страницы — первый подтег `<html lang>`: «de-AT» — «de».
_LANGUAGE = re.compile(r"[a-z]{2,3}")

Line = tuple[str, str]


@dataclass(frozen=True, slots=True)
class JudgeText:
    """Текст для судьи — по строке на кусок: (подпись, слова сайта)."""

    lines: tuple[Line, ...] = ()

    @property
    def empty(self) -> bool:
        return not self.lines

    def side(self, labels: Sequence[str]) -> tuple[str, ...]:
        """Слова строк с этими подписями — по строке."""
        return tuple(words for label, words in self.lines if label in labels)

    @property
    def article(self) -> tuple[str, ...]:
        return self.side(ARTICLE_LABELS)

    @property
    def home(self) -> tuple[str, ...]:
        return self.side(HOME_LABELS)

    def for_model(self) -> str:
        """Как его видит модель — и как он хранится в строке суда (`seen`)."""
        return "\n".join(f"{label}: {words}" for label, words in self.lines)

    @classmethod
    def parse(cls, stored: str) -> JudgeText:
        """Текст, сохранённый в строке суда, — обратно в строки: по нему сверяется цитата."""
        split = (line.partition(": ") for line in stored.splitlines())
        return cls(tuple((label, words) for label, _, words in split if label in LABELS and words))


def holds(quote: str, lines: Sequence[str]) -> bool:
    """Цитата — внутри одной строки стороны (`shared/quote.quote_holds`)."""
    return any(quote_holds(quote, line) for line in lines)


@dataclass(frozen=True, slots=True)
class Seen:
    """Что судья увидит — или почему судить нечем: `blind` — читать нечего, `denied` — вместо
    страницы заглушка защиты. `language` — язык страницы по её разметке, если назван."""

    text: JudgeText = JudgeText()
    language: str | None = None
    blind: str = ""
    denied: str = ""


def _clean(text: str | None) -> str:
    return " ".join((text or "").split())


def cut(text: str | None, limit: int) -> str:
    """Не длиннее `limit` знаков: пробелы схлопнуты, срез — по границе слова (письменность без
    пробелов — по знаку)."""
    clean = _clean(text)
    if len(clean) <= limit:
        return clean
    head = clean[:limit]
    return head.rsplit(" ", 1)[0] if " " in head else head


def _around(text: str, anchor: str, limit: int) -> str:
    """Окно `limit` знаков вокруг анкора в длинном блоке: абзацем бывает весь текст статьи."""
    clean = _clean(text)
    if len(clean) <= limit:
        return clean
    at = clean.casefold().find(_clean(anchor).casefold()) if anchor.strip() else -1
    start = max(0, at - limit // 2) if at >= 0 else 0
    window = clean[start : start + limit]
    if start > 0 and " " in window:
        window = window.split(" ", 1)[1]
    return cut(window, limit)


def _root_of(host: str) -> str:
    return host.lower().removeprefix("www.")


def _to_target(href: str, page_url: str, target_root: str) -> bool:
    """Ссылка ведёт на домен кандидата или его поддомен."""
    try:
        host = urlsplit(urljoin(page_url, href)).hostname or ""
    except ValueError:
        # Битый адрес ссылки («http://[::1», чужой мусор в href) — не наш домен; страниц таких мало.
        logger.debug("судья рекламодателей: адрес ссылки не разобран", extra={"href": href[:120]})
        return False
    host = _root_of(host)
    return host == target_root or host.endswith(f".{target_root}")


def _link_node(tree: HTMLParser, page_url: str, target_root: str, anchor: str) -> Node | None:
    """Ссылка на домен кандидата: с тем же анкором, что нашёл обход, — иначе первая."""
    found = [
        node
        for node in tree.css("a[href]")
        if _to_target(node.attributes.get("href") or "", page_url, target_root)
    ]
    wanted = _clean(anchor).casefold()
    same = [node for node in found if _clean(node.text(separator=" ")).casefold() == wanted]
    chosen = same or found
    return chosen[0] if chosen else None


def _block_of(node: Node) -> Node:
    """Ближайший к ссылке блок текста; ссылка вне блоков — она сама."""
    current: Node | None = node.parent
    while current is not None and current.tag not in BLOCK_TAGS:
        current = current.parent
    return current if current is not None else node


def _title_of(tree: HTMLParser) -> str:
    for selector in ("h1", "title"):
        node = tree.css_first(selector)
        if node is not None and (words := _clean(node.text(separator=" "))):
            return words
    return ""


def _language_of(tree: HTMLParser) -> str | None:
    root = tree.css_first("html")
    lang = (root.attributes.get("lang") or "") if root is not None else ""
    head = lang.strip().lower().split("-")[0].split("_")[0]
    return head if _LANGUAGE.fullmatch(head) else None


def _budget(lines: Sequence[tuple[str, str]]) -> list[Line]:
    """Строки стороны в её потолке `SIDE_CHARS`: пустые — вон, хвост последней срезается."""
    kept: list[Line] = []
    left = SIDE_CHARS
    for label, words in lines:
        if not words or left <= 0:
            continue
        piece = cut(words, left)
        if piece:
            kept.append((label, piece))
            left -= len(piece)
    return kept


@dataclass(frozen=True, slots=True)
class Page:
    """Строки статьи — или почему их нет: `blind` и `denied` — как у `Seen`."""

    lines: tuple[Line, ...] = ()
    language: str | None = None
    blind: str = ""
    denied: str = ""


def page_lines(html: str, *, page_url: str, target_root: str, anchor: str) -> Page:
    """Страница донора → строки статьи: заголовок, начало, абзац со ссылкой, конец."""
    tree = HTMLParser(html)
    title = cut(_title_of(tree), TITLE_CHARS)
    node = _link_node(tree, page_url, target_root, anchor)
    article = extract_article(html)
    body = _clean(article.text) if article is not None else ""
    if looks_denied(f"{title}\n{cut(body, EDGE_CHARS)}"):
        return Page(denied=f"вместо страницы заглушка защиты: {title[:40]!r}")
    if node is None:
        return Page(blind="ссылки на домен на странице нет")
    paragraph = _around(_block_of(node).text(separator=" "), anchor, PARAGRAPH_CHARS)
    start = cut(body, EDGE_CHARS)
    end = body[-EDGE_CHARS:].split(" ", 1)[-1] if len(body) > 2 * EDGE_CHARS else ""
    lines = _budget(
        [(ARTICLE_TITLE, title), (LINK_PARAGRAPH, paragraph), (ARTICLE_START, start),
         (ARTICLE_END, end)]
    )  # fmt: skip
    order = {label: n for n, label in enumerate(ARTICLE_LABELS)}
    return Page(tuple(sorted(lines, key=lambda line: order[line[0]])), _language_of(tree))


def home_lines(home: HomeSignals | None) -> list[Line]:
    """Главная домена → строки главной; не открылась — строк нет."""
    if home is None or not home.reached:
        return []
    return _budget(
        [
            (HOME_TITLE, cut(home.title, TITLE_CHARS)),
            (HOME_DESCRIPTION, cut(home.description, HOME_DESCRIPTION_CHARS)),
            (HOME_MENU, cut(" · ".join(home.nav), HOME_MENU_CHARS)),
            (HOME_TEXT, cut(home.text, HOME_TEXT_CHARS)),
        ]
    )


async def _page(http: httpx.AsyncClient, page_url: str) -> tuple[str | None, Page | None]:
    """Страница донора только обычным запросом каскада: без журнала расхода платные уровни не
    зовутся (`fetch.PageCascade._allowed`), прокси и сервиса рендеринга у него нет вовсе, браузера —
    тоже: страница, закрытая от обычного запроса, — `denied`, её решает человек."""
    cascade = PageCascade(
        http, browser_enabled=False, proxies=ProxyPool(()), render=None, spending=None
    )
    try:
        result = await cascade.get(page_url)
    finally:
        await cascade.aclose()
    if result.outcome is FetchOutcome.BLOCKED:
        return None, Page(denied=f"страница закрылась от нас: {result.reason or 'отказ'}")
    if result.outcome is not FetchOutcome.OK or not result.html:
        return None, Page(blind=f"страница не открылась: {result.reason or result.outcome.value}")
    return result.html, None


async def gather(http: httpx.AsyncClient, *, page_url: str, target_root: str, anchor: str) -> Seen:
    """Страница донора и главная домена — текстом судьи; судить нечем — почему."""
    html, failed = await _page(http, page_url)
    if failed is not None or html is None:
        page = failed or Page(blind="страница не открылась")
        return Seen(blind=page.blind, denied=page.denied)
    page = page_lines(html, page_url=page_url, target_root=target_root, anchor=anchor)
    if page.blind or page.denied:
        return Seen(language=page.language, blind=page.blind, denied=page.denied)
    home = await check_home(http, target_root)
    if not home.reached:
        logger.info(
            "судья рекламодателей: главная не открылась — судья видит одну статью",
            extra={"host": target_root, "error": home.error},
        )
    return Seen(JudgeText((*page.lines, *home_lines(home))), page.language)


def fingerprint(text: str) -> str:
    """Отпечаток того, что видел судья: правка статьи или главной — новый вопрос модели."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
