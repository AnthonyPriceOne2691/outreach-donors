"""Просьба кончить обход доходит и до чтения карты сайта (18.11.2026).

Обход спрашивал «не пора ли остановиться» только перед страницами, а до страниц он читает
карты — до 200 файлов, по одному на паузу сайта. На проде часть обхода 24/7 читала индекс
в сотни карт с паузой сайта 10 с и не видела ни «Пауза», ни остановки выкатки. Здесь —
индекс на тридцать карт и просьба после второго файла: чтение встаёт перед следующим, а обход —
на первой своей проверке, как от просьбы на странице.
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from backend.features.core.domain import StopReason
from backend.features.crawl.feeds import FEED_PATHS
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.progress import Batch, CrawlInterruptedError, Progress
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.sitemap import SitemapReader, cut_short
from backend.features.crawl.walk import DonorCrawler

HOST = "donor.test"
HOME = f"https://{HOST}/"
MAPS = 30
TEXT = {"content-type": "text/plain"}
XML = {"content-type": "text/xml"}
HTML = {"content-type": "text/html; charset=utf-8"}


class IndexSite:
    """Донор, чей robots.txt называет индекс на `MAPS` карт; в каждой карте — одна страница."""

    def __init__(self, *, home_links: bool = True) -> None:
        self.home_links = home_links
        self.maps_read = 0
        self.asked: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.asked.append(path)
        if path == "/robots.txt":
            robots = f"User-agent: *\nDisallow:\nSitemap: https://{HOST}/index.xml\n"
            return httpx.Response(200, text=robots, headers=TEXT)
        if path == "/index.xml":
            self.maps_read += 1
            kids = "".join(
                f"<sitemap><loc>https://{HOST}/m{n}.xml</loc></sitemap>" for n in range(MAPS)
            )
            return httpx.Response(200, text=f"<sitemapindex>{kids}</sitemapindex>", headers=XML)
        if path.startswith("/m") and path.endswith(".xml"):
            self.maps_read += 1
            page = f"https://{HOST}/p{path[2:-4]}"
            return httpx.Response(
                200, text=f"<urlset><url><loc>{page}</loc></url></urlset>", headers=XML
            )
        if path == "/":
            links = '<a href="/about">о сайте</a>' if self.home_links else ""
            return httpx.Response(200, text=f"<html><body>{links}</body></html>", headers=HTML)
        if path == "/about" or path.startswith("/p"):
            return httpx.Response(200, text="<html><body><p>текст</p></body></html>", headers=HTML)
        return httpx.Response(404, headers=HTML)

    @property
    def pages_asked(self) -> list[str]:
        """Страницы, а не служебное: robots, карты и ленты."""
        return [p for p in self.asked if p != "/robots.txt" and not p.endswith(".xml")]


async def _sink(_batch: Batch) -> None:
    return None


async def _crawl(site: IndexSite, progress: Progress) -> CrawlReport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
    crawler = DonorCrawler(
        client,
        limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0),
        sitemap_ceilings=(200, 50_000),
    )
    async with client:
        try:
            return await crawler.crawl(HOST, progress)
        finally:
            await crawler.aclose()


async def _scan(site: IndexSite, **kwargs: Any) -> Any:
    async with httpx.AsyncClient(transport=httpx.MockTransport(site.handler)) as client:
        limiter = DomainLimiter(delay_sec=0.0, max_delay_sec=0.0)
        reader = SitemapReader(client, limiter, max_files=200, max_urls=50_000)
        return await reader.scan(HOST, HOME, [f"https://{HOST}/index.xml"], **kwargs)


def test_the_reason_to_stop_reading_is_named() -> None:
    assert cut_short(None, None) is None
    assert cut_short(time.monotonic() + 60, lambda: False) is None
    assert cut_short(time.monotonic() - 1, lambda: False) == "срок на донора вышел"
    assert cut_short(None, lambda: True) == "обход просят кончить"


@pytest.mark.parametrize(("ask_after", "read"), [(None, MAPS + 1), (3, 3)])
async def test_the_map_stops_before_the_next_file(ask_after: int | None, read: int) -> None:
    """Без просьбы — индекс и все тридцать карт; с просьбой после трёх файлов — ни одного сверх."""
    site = IndexSite()
    halt = None if ask_after is None else (lambda: site.maps_read >= ask_after)

    scan = await _scan(site, halt=halt)

    assert site.maps_read == read
    assert scan.files_read == read
    assert len(scan.urls) == read - 1  # индекс страниц не даёт, каждая карта — одну
    assert scan.complete is (ask_after is None)


async def test_a_pause_on_the_map_ends_the_part_paused() -> None:
    """Пауза 24/7 (или ночь) пришла, пока часть читает карту: часть кончается штатно, «на паузе»,
    не дочитав карту и не открыв ни одной страницы, кроме главной."""
    site = IndexSite()

    report = await _crawl(site, Progress(flush=_sink, pause=lambda: site.maps_read >= 2))

    assert report.stop_reason is StopReason.PAUSED
    assert site.maps_read == 2
    assert site.pages_asked == ["/"]
    assert report.sitemap_complete is False


async def test_a_stop_on_the_map_leaves_a_checkpoint() -> None:
    """Выкатка пришла, пока обход читает карту: он не ждёт, пока докер его убьёт, а встаёт с
    чекпоинтом — продолжение откроет главную как открытую и карту прочтёт заново."""
    site = IndexSite()

    with pytest.raises(CrawlInterruptedError) as stopped:
        await _crawl(site, Progress(flush=_sink, stop=lambda: site.maps_read >= 2))

    assert stopped.value.checkpoint.pages == [HOME]
    assert site.maps_read == 2
    assert site.pages_asked == ["/"]


async def test_a_crawl_stopped_before_the_map_reads_it_when_it_continues() -> None:
    """Остановку попросили до первого файла карты: адресов карты нет, но чекпоинт не называет
    источник «только ссылки» — продолжение карту читает и обходит сайт целиком. Иначе цепочка
    обхода 24/7 после одной паузы в неудачную минуту забыла бы карту навсегда."""
    first = IndexSite()
    with pytest.raises(CrawlInterruptedError) as stopped:
        await _crawl(first, Progress(flush=_sink, stop=lambda: True))
    point = stopped.value.checkpoint

    assert first.maps_read == 0
    assert point.source != "links"

    second = IndexSite()
    report = await _crawl(second, Progress(flush=_sink, resume=point))

    assert second.maps_read == MAPS + 1
    assert report.stop_reason is StopReason.EXHAUSTED
    assert len(report.pages) == MAPS + 2  # главная, «о сайте» и страница каждой карты


async def test_a_pause_with_nothing_to_open_is_not_an_exhausted_site() -> None:
    """Карту прервали на индексе, а с главной ссылок нет — очередь пуста. Это пауза, а не
    «адреса кончились»: у части 24/7 исход «кончились» пометил бы сайт обойдённым до конца.
    И ленту такой обход не читает — он всё равно встаёт."""
    site = IndexSite(home_links=False)

    report = await _crawl(site, Progress(flush=_sink, pause=lambda: site.maps_read >= 1))

    assert report.stop_reason is StopReason.PAUSED
    assert site.maps_read == 1
    assert not set(FEED_PATHS) & set(site.asked)


async def test_without_a_request_the_same_site_is_crawled_to_the_end() -> None:
    """Обратная сторона: без просьбы тот же сайт дочитан и обойдён до конца."""
    site = IndexSite(home_links=False)

    report = await _crawl(site, Progress(flush=_sink))

    assert report.stop_reason is StopReason.EXHAUSTED
    assert site.maps_read == MAPS + 1
    assert len(site.pages_asked) == MAPS + 1  # главная и страница каждой карты
