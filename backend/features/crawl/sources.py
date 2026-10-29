"""Откуда обход донора берёт адреса — карта сайта, лента, ссылки главной — и очередь из них.

Вынесено из обхода (`walk.py`) 29.10.2026, когда к карте из robots.txt добавились карта,
названная главной, и ленты RSS и Atom: порядок «сначала карта, потом ссылки» (`frontier.py`)
прежний, а поиск источников — отдельная от обхода страниц забота.

**Карта** — объявленная в robots.txt, а нет — названная главной и по типичным адресам
(`sitemap.py`). **Лента** — только когда карта не дала ни одного адреса и только у первой
попытки: у продолжения очередь ссылок уже в чекпоинте (`feeds.py`). Её адреса — первыми
среди ссылок: это последние статьи сайта, а обход по ссылкам до статей доходит последним.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import httpx

from backend.features.crawl.feeds import FeedReader, declared_feeds
from backend.features.crawl.fetch import FetchResult
from backend.features.crawl.frontier import BOTH, LINKS, Frontier, crawlable, same_site_links
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.progress import Checkpoint
from backend.features.crawl.robots import RobotsRules
from backend.features.crawl.sitemap import SitemapReader, SitemapScan, declared_in_html


@dataclass(frozen=True, slots=True)
class Sources:
    """Что нашлось до обхода страниц: карты сайта и адреса ленты (пусто — ленту не искали
    или её нет)."""

    scan: SitemapScan
    fresh: list[str]


async def read_sources(
    client: httpx.AsyncClient,
    limiter: DomainLimiter,
    headers: Mapping[str, str],
    *,
    host: str,
    home: FetchResult,
    rules: RobotsRules,
    point: Checkpoint,
    deadline: float,
) -> Sources:
    """Карты, а если они не дали адресов у первой попытки, — лента. Запросы — под слотом
    ограничителя сайта и тем же именем, что у страниц (`headers`); `deadline` — общий срок
    на донора."""
    html = home.html or ""
    sitemaps = SitemapReader(client, limiter, headers=headers)
    hinted = declared_in_html(html, home.url, host)
    scan = await sitemaps.scan(host, home.url, rules.sitemaps, deadline=deadline, hinted=hinted)
    if point.pages or scan.urls:
        return Sources(scan, [])
    feeds = FeedReader(client, limiter, headers=headers)
    fresh = await feeds.read(
        host, home.url, declared_feeds(html, home.url, host), deadline=deadline
    )
    return Sources(scan, fresh)


def start_frontier(
    home: FetchResult, host: str, found: Sources, point: Checkpoint
) -> tuple[Frontier, list[str]]:
    """Очередь — сначала карта, потом ссылки (`frontier.py`) — и уже открытые страницы.

    Адреса карты проходят те же правила, что ссылки: свой сайт, страница, а не файл.
    Ссылки копятся с главной и с каждой открытой страницы; продолжение берёт их из
    чекпоинта — собрать заново значило бы обойти сайт второй раз, — а карту читает снова.
    Адреса ленты — первыми среди ссылок.
    """
    # Главная открыта в любом случае — её качала проверка «сайт
    # открывается». Она же и первая страница отчёта, откуда бы
    # ни взялась очередь: иначе статей окажется больше, чем страниц.
    opened = list(point.pages) or [home.url]
    mapped = [] if point.source == LINKS else [u for u in found.scan.urls if crawlable(u, host)]
    linked = point.queue if point.pages else [
        *(url for url in found.fresh if crawlable(url, host)),
        *same_site_links(home.html or "", home.url, host),
    ]  # fmt: skip
    frontier = Frontier.start(
        mapped,
        linked,
        opened,
        has_map=bool(mapped) or point.source not in (LINKS, "none"),
        took_links=point.source == BOTH,
        counts=(point.from_map, point.from_links),
    )
    return frontier, opened
