"""Ступень 1: страницы сайта.

Порядок обхода задан ценностью адреса, а не удобством. Сначала страницы,
где сидит тот, кто называет цену за размещение, потом обычные контакты,
потом главная — и обход останавливается, как только адрес нашёлся на
странице дорогого вида.

Число страниц на домен ограничено. Без потолка сайт с бесконечной
навигацией съедает прогон: каждая страница — это запрос и секунды.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx

from backend.config import contacts as cfg
from backend.features.core.domain import PageKind

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Слаги по видам страниц. Списки не исчерпывающие и не должны быть:
# остальное добирается по ссылкам с главной, где раздел назван словами.
SLUGS: dict[PageKind, tuple[str, ...]] = {
    PageKind.MONEY: (
        "write-for-us", "write-for-me", "guest-post", "guest-posting", "submit-article",
        "advertise", "advertising", "advertise-with-us", "sponsored-post", "media-kit",
        "partnership", "work-with-us",
    ),
    PageKind.CONTACT: (
        "contact", "contacts", "contact-us", "contactus", "get-in-touch",
        "kontakt", "kontak", "contacto", "contatti", "hubungi-kami",
    ),
    PageKind.ABOUT: ("about", "about-us", "aboutus", "team", "our-team", "imprint", "impressum"),
}  # fmt: skip

# Те же слова для поиска по ссылкам главной: там раздел может лежать
# по адресу вида /p/12345, и угадать его по слагу нельзя.
LINK_MARKERS: frozenset[str] = frozenset(
    slug for slugs in SLUGS.values() for slug in slugs
) | frozenset({"write for us", "advertise", "contact", "about us", "guest post"})

# Признаки контактной формы: адреса нет, но написать можно руками.
FORM_MARKERS = ("<form", "wpcf7", "gravity_form", "contact-form", "formcraft", "hs-form")


@dataclass(frozen=True, slots=True)
class FetchedPage:
    """Скачанная страница и её вид."""

    url: str
    kind: PageKind
    html: str


def _kind_of(url: str) -> PageKind:
    """Вид страницы по её адресу. Неузнанное — главная, то есть слабый вес."""
    path = urlparse(url).path.lower()
    for kind, slugs in SLUGS.items():
        if any(slug in path for slug in slugs):
            return kind
    return PageKind.HOME


def candidate_urls(site_host: str) -> Iterator[tuple[str, PageKind]]:
    """Адреса-кандидаты по убыванию ценности: сначала деньги, потом контакты.

    Главная идёт первой физически — с неё снимаются ссылки, — но её вид
    остаётся слабым, и найденный на ней адрес уступит адресу со страницы
    «advertise», если та тоже ответит.
    """
    base = f"https://{site_host}"
    yield base + "/", PageKind.HOME
    for kind in (PageKind.MONEY, PageKind.CONTACT, PageKind.ABOUT):
        for slug in SLUGS[kind]:
            yield f"{base}/{slug}/", kind


def has_contact_form(html: str) -> bool:
    """Есть ли на странице форма. Ветка «форма без адреса» — ступень 4."""
    lowered = html.lower()
    return any(marker in lowered for marker in FORM_MARKERS)


class PageFetcher:
    """Качает страницы одного домена с потолками на число и размер.

    Ошибка сети по одной странице — не поломка домена: половина сайтов
    отдаёт 404 на половину слагов, это ожидаемо. Поломкой было бы
    промолчать о ней, поэтому каждая пропущенная страница попадает в лог
    и в счётчик.
    """

    def __init__(self, client: httpx.AsyncClient, *, max_pages: int | None = None) -> None:
        self._client = client
        self._max_pages = max_pages if max_pages is not None else cfg.MAX_PAGES_PER_DOMAIN
        self.fetched = 0
        self.failed = 0

    async def get(self, url: str, kind: PageKind) -> FetchedPage | None:
        if self.fetched >= self._max_pages:
            return None
        try:
            response = await self._client.get(url, headers=HEADERS, follow_redirects=True)
        except httpx.HTTPError as exc:
            self.failed += 1
            logger.debug("страница не открылась: %s — %r", url, exc)
            return None

        self.fetched += 1
        if response.status_code >= 400:
            logger.debug("страница ответила %s: %s", response.status_code, url)
            return None
        if "html" not in response.headers.get("content-type", "").lower():
            return None

        html = response.text[: cfg.MAX_PAGE_BYTES]
        return FetchedPage(url=str(response.url), kind=kind, html=html)

    def follow(self, page: FetchedPage, links: set[str]) -> list[tuple[str, PageKind]]:
        """Ссылки со страницы, приведённые к абсолютным, — только свой домен.

        Чужой домен здесь означает соцсеть или платформу: их контактные
        страницы к нашему донору отношения не имеют.
        """
        host = urlparse(page.url).netloc.lower()
        out: list[tuple[str, PageKind]] = []
        for href in links:
            absolute = urljoin(page.url, href)
            if urlparse(absolute).netloc.lower() != host:
                continue
            out.append((absolute, _kind_of(absolute)))
        # Дорогие виды вперёд: потолок страниц может кончиться раньше списка.
        order = {PageKind.MONEY: 0, PageKind.CONTACT: 1, PageKind.ABOUT: 2, PageKind.HOME: 3}
        return sorted(out, key=lambda item: order[item[1]])
