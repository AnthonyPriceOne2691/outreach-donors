"""Ленты RSS и Atom: свежие статьи донора, когда карты сайта нет (29.10.2026).

Без карты обход начинается со ссылок главной и идёт в ширину — по навигации, рубрикам и
тегам, — и до статей доходит последним. А размещения покупают сейчас: лента сайта
перечисляет как раз последние статьи, от новой к старой, одним запросом. Поэтому, когда
карта не нашлась, адреса ленты встают в очередь ссылок первыми, перед ссылками главной
(`walk.DonorCrawler`), — дальше обход идёт по ссылкам, как без неё.

**Лента — не карта.** В ней десяток-другой последних статей, а не сайт: в отчёте она —
числом адресов (`CrawlReport.feed_items`), а не «карта найдена», и «в карте сайта больше
адресов нет» по ней не скажешь.

**Где искать.** Сначала — что объявляет главная (`<link rel="alternate"
type="application/rss+xml">` или `application/atom+xml`): это уже скачанная страница, и
поиск ничего не стоит. Не объявила — типичные адреса движков (`FEED_PATHS`: `/feed` у
WordPress, `/rss.xml` у Drupal и генераторов статики, `/atom.xml` у Blogger и Jekyll). До
первой ленты с записями: остальные адреса того же сайта дадут те же статьи.

**XML — выражением, а не разбором**, как у карты (`sitemap.py`): чужой XML — вход от
постороннего, и разборщик стандартной библиотеки раскрывал бы его сущности.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.sitemap import NS_PREFIX, same_site
from backend.shared.net.url_parts import join_url

logger = logging.getLogger(__name__)

#: Где лента лежит у распространённых движков, если главная о ней молчит.
FEED_PATHS: tuple[str, ...] = ("/feed", "/rss", "/rss.xml", "/atom.xml", "/feed.xml")

#: Сколько объявленных главной лент пробуем: больше — это ленты рубрик с теми же статьями.
MAX_DECLARED = 3

#: Сколько байт ленты читаем: лента — это последние статьи, а не архив.
MAX_FEED_BYTES = 2 * 1024 * 1024

_TYPES = ("application/rss+xml", "application/atom+xml")
#: Префикс пространства имён — тот же, что у карты (`sitemap.NS_PREFIX`): `<atom:feed>`,
#: `<rdf:RDF>`.
_IS_FEED = re.compile(rf"<{NS_PREFIX}(?:rss|feed|RDF)\b", re.IGNORECASE)
_ITEM = re.compile(
    rf"<{NS_PREFIX}(item|entry)\b[^>]*>(.*?)</{NS_PREFIX}\1>", re.IGNORECASE | re.DOTALL
)
#: Адрес записи RSS — текст `<link>`; Atom — `href` у `<link>` без `rel` или с `alternate`.
_RSS_LINK = re.compile(rf"<{NS_PREFIX}link>\s*(?:<!\[CDATA\[)?\s*([^<\]\s]+)", re.IGNORECASE)
_ATOM_LINK = re.compile(rf"<{NS_PREFIX}link\b([^>]*)/?>", re.IGNORECASE)
_ATTR = re.compile(r"""(\w+)\s*=\s*["']([^"']*)["']""")


def declared_feeds(html: str, base: str, host: str) -> list[str]:
    """Ленты, которые объявляет главная, — свои, без повторов, не больше `MAX_DECLARED`."""
    out: list[str] = []
    for node in HTMLParser(html).css("link[href]"):
        rel = (node.attributes.get("rel") or "").lower().split()
        if (
            "alternate" not in rel
            or (node.attributes.get("type") or "").strip().lower() not in _TYPES
        ):
            continue
        url = join_url(base, (node.attributes.get("href") or "").strip())
        if url is not None and url not in out and same_site(url, host):
            out.append(url)
    return out[:MAX_DECLARED]


def feed_urls(text: str, host: str) -> list[str]:
    """Адреса записей ленты — в её порядке (ленты идут от новой к старой), свои, без повторов."""
    out: list[str] = []
    for match in _ITEM.finditer(text):
        url = _entry_url(match.group(2))
        if url is not None and url not in out and same_site(url, host):
            out.append(url)
    return out


def _entry_url(entry: str) -> str | None:
    """Адрес одной записи: RSS — `<link>текст</link>`, Atom — ссылка-«альтернатива»."""
    rss = _RSS_LINK.search(entry)
    if rss is not None:
        return rss.group(1).strip()
    for tag in _ATOM_LINK.finditer(entry):
        attrs: dict[str, str] = {name.lower(): value for name, value in _ATTR.findall(tag.group(1))}
        if attrs.get("href") and attrs.get("rel", "alternate").lower() == "alternate":
            return attrs["href"].strip()
    return None


class FeedReader:
    """Читает ленты одного донора до первой с записями — под слотом ограничителя сайта."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        limiter: DomainLimiter,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._client = client
        self._limiter = limiter
        # Заголовки страниц обхода (`PageCascade.headers`): ленту просит то же имя.
        self._headers = dict(headers or {})

    async def read(
        self, host: str, base: str, declared: list[str], *, deadline: float | None = None
    ) -> list[str]:
        """Адреса статей первой ленты с записями: объявленные главной, а если их нет —
        типичные адреса. `deadline` — общий срок на донора: лента — тоже запрос."""
        queue = list(declared) or [urljoin(base, path) for path in FEED_PATHS]
        for url in queue:
            if deadline is not None and time.monotonic() >= deadline:
                logger.info("лента: срок на донора вышел")
                break
            text = await self._get(host, url)
            if text is None or not _IS_FEED.search(text):
                continue
            found = feed_urls(text, host)
            if found:
                logger.info("лента %s: %s свежих адресов — в начало очереди", url, len(found))
                return found
        return []

    async def _get(self, host: str, url: str) -> str | None:
        """Одна лента. Отказ, обрыв и не найдено — «ленты здесь нет», обход идёт дальше."""
        async with self._limiter.slot(host):
            try:
                response = await self._client.get(url, headers=self._headers, follow_redirects=True)
            except httpx.HTTPError as exc:
                logger.info("лента: %s не открылась (%r)", url, exc)
                return None
        if response.status_code >= 400:
            return None
        return response.content[:MAX_FEED_BYTES].decode(response.encoding or "utf-8", "replace")
