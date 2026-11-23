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
from backend.features.crawl.frontier import BOTH, LINKS, Frontier, own_pages, same_site_links
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.progress import Checkpoint
from backend.features.crawl.robots import RobotsRules
from backend.features.crawl.sitemap import Halt, SitemapReader, SitemapScan, declared_in_html


@dataclass(frozen=True, slots=True)
class Sources:
    """Что нашлось до обхода страниц: карты сайта и адреса ленты (пусто — ленту не искали
    или её нет)."""

    scan: SitemapScan
    fresh: list[str]


def readers(
    client: httpx.AsyncClient,
    limiter: DomainLimiter,
    headers: Mapping[str, str],
    ceilings: tuple[int, int] | None = None,
) -> tuple[SitemapReader, FeedReader]:
    """Читатели карты и ленты обхода: запросы — под слотом ограничителя сайта и тем же именем,
    что у страниц. `ceilings` — потолки карты, файлов и адресов; пусто — из настроек, свои — у
    глубокого обхода (`deep.py`)."""
    files, urls = ceilings or (None, None)
    return (
        SitemapReader(client, limiter, headers=headers, max_files=files, max_urls=urls),
        FeedReader(client, limiter, headers=headers),
    )


async def read_sources(
    sitemaps: SitemapReader,
    feeds: FeedReader,
    *,
    host: str,
    home: FetchResult,
    rules: RobotsRules,
    point: Checkpoint,
    deadline: float,
    halt: Halt | None = None,
) -> Sources:
    """Карты, а если они не дали адресов у первой попытки, — лента; `deadline` — общий срок
    на донора. `halt` — просят ли кончить обход (`sitemap.cut_short`): карта дочитывается
    не вся, а лента не читается вовсе — обход всё равно встанет на первой своей проверке."""
    html = home.html or ""
    hinted = declared_in_html(html, home.url, host)
    scan = await sitemaps.scan(
        host, home.url, rules.sitemaps, deadline=deadline, hinted=hinted, halt=halt
    )
    if point.pages or scan.urls or (halt is not None and halt()):
        return Sources(scan, [])
    fresh = await feeds.read(
        host, home.url, declared_feeds(html, home.url, host), deadline=deadline
    )
    return Sources(scan, fresh)


def start_frontier(
    home: FetchResult, host: str, found: Sources, point: Checkpoint
) -> tuple[Frontier, list[str]]:
    """Очередь — сначала карта, потом ссылки (`frontier.py`) — и уже открытые страницы.

    Адреса карты, ленты и чекпоинта проходят те же правила, что ссылки (`frontier.own_page`):
    свой сайт, страница, а не файл, адрес, который откроет клиент. Ссылки копятся с главной
    и с каждой открытой страницы; продолжение берёт их из чекпоинта — собрать заново значило
    бы обойти сайт второй раз, — а карту читает снова.
    Адреса ленты — первыми среди ссылок. Карту прервала просьба кончить (`SitemapScan.halted`)
    раньше первого адреса — очередь всё равно «с картой»: иначе чекпоинт назвал бы источник
    «только ссылки», и продолжение карту не читало бы вовсе.
    """
    # Главная открыта в любом случае — её качала проверка «сайт
    # открывается». Она же и первая страница отчёта, откуда бы
    # ни взялась очередь: иначе статей окажется больше, чем страниц.
    opened = list(point.pages) or [home.url]
    mapped = [] if point.source == LINKS else own_pages(found.scan.urls, host)
    linked = own_pages(point.queue, host) if point.pages else [
        *own_pages(found.fresh, host),
        *same_site_links(home.html or "", home.url, host),
    ]  # fmt: skip
    frontier = Frontier.start(
        mapped,
        linked,
        opened,
        has_map=_has_map(mapped, point, found.scan),
        took_links=point.source == BOTH,
        counts=(point.from_map, point.from_links),
    )
    return frontier, opened


def _has_map(mapped: list[str], point: Checkpoint, scan: SitemapScan) -> bool:
    """Идёт ли очередь «с картой»: адреса карты есть или источник решила прошлая попытка. Первая
    попытка (`"none"`), прерванная на карте просьбой кончить (`scan.halted`), источник не решила."""
    if mapped or point.source not in (LINKS, "none"):
        return True
    return point.source == "none" and scan.halted
