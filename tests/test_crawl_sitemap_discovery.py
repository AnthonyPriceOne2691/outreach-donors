"""Где искать карту, если robots.txt о ней молчит (`crawl/sitemap.py`, 29.10.2026), без сети.

«Может, она есть, а мы не там смотрим»: ссылки главной на карту, больше типичных адресов,
текстовая карта — и ответ 200 со страницей «не найдено» больше не считается найденной картой.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.sitemap import (
    COMMON_PATHS,
    MAX_HTML_DECLARED,
    SitemapReader,
    declared_in_html,
    looks_like_map,
)
from backend.features.crawl.walk import DonorCrawler
from tests.test_crawl_walk import HOST, FakeSite, _page, _urlset

BASE = f"https://{HOST}/"
NOT_FOUND = "<html><body><h1>Страница не найдена</h1><a href='/a'>на главную</a></body></html>"


def _reader(handler: Any) -> tuple[SitemapReader, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return SitemapReader(client, DomainLimiter(delay_sec=0.0, max_delay_sec=0.0)), client


def _soft_404_except(path: str, body: str) -> tuple[Any, list[str]]:
    """Сайт отвечает 200 и страницей «не найдено» на любой адрес, кроме `path`."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == path:
            return httpx.Response(200, text=body)
        return httpx.Response(200, text=NOT_FOUND, headers={"content-type": "text/html"})

    return handler, asked


async def test_a_200_not_found_page_is_not_a_map() -> None:
    """Раньше первая догадка со страницей «не найдено» кончала поиск: «карта есть, адресов
    нет». Теперь поиск идёт дальше — до настоящей карты."""
    handler, asked = _soft_404_except("/sitemap1.xml", _urlset(f"{BASE}a", f"{BASE}b"))
    reader, client = _reader(handler)
    async with client:
        scan = await reader.scan(HOST, BASE, [])

    assert scan.urls == [f"{BASE}a", f"{BASE}b"]
    assert scan.found is True
    assert asked == list(COMMON_PATHS[: COMMON_PATHS.index("/sitemap1.xml") + 1])


async def test_only_not_found_pages_mean_no_map() -> None:
    handler, asked = _soft_404_except("/nowhere", "")
    reader, client = _reader(handler)
    async with client:
        scan = await reader.scan(HOST, BASE, [])

    assert scan.found is False
    assert scan.urls == []
    assert asked == list(COMMON_PATHS)


async def test_a_text_map_lists_a_page_per_line() -> None:
    """Текстовый формат поисковиков: адрес на строку; чужие адреса не наши."""
    text = f"{BASE}a\n\n{BASE}b\nhttps://elsewhere.test/c\n"
    handler, _ = _soft_404_except("/sitemap.txt", text)
    reader, client = _reader(handler)
    async with client:
        scan = await reader.scan(HOST, BASE, [])

    assert scan.urls == [f"{BASE}a", f"{BASE}b"]
    assert scan.found is True


async def test_maps_named_by_the_home_page_come_first_and_end_the_guessing() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/maps/main.xml":
            return httpx.Response(200, text=_urlset(f"{BASE}a"))
        return httpx.Response(404)

    reader, client = _reader(handler)
    async with client:
        scan = await reader.scan(HOST, BASE, [], hinted=[f"{BASE}maps/main.xml"])

    assert scan.urls == [f"{BASE}a"]
    assert asked == ["/maps/main.xml"]


async def test_a_dead_home_page_hint_falls_back_to_the_guesses() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=_urlset(f"{BASE}a"))
        return httpx.Response(404)

    reader, client = _reader(handler)
    async with client:
        scan = await reader.scan(HOST, BASE, [], hinted=[f"{BASE}gone.xml"])

    assert scan.urls == [f"{BASE}a"]
    assert asked == ["/gone.xml", "/sitemap.xml"]


async def test_maps_declared_in_robots_are_read_as_they_are() -> None:
    """Объявленную сайтом карту не судим: «карта без адресов» — знание, как и раньше."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=NOT_FOUND)

    reader, client = _reader(handler)
    async with client:
        scan = await reader.scan(HOST, BASE, [f"{BASE}declared.xml"])

    assert scan.files_read == 1
    assert scan.found is True


def test_the_home_page_names_its_maps() -> None:
    html = (
        "<html><head><link rel='sitemap' type='application/xml' href='/maps/index.xml'></head>"
        "<body><footer><a href='/sitemap.xml'>Sitemap</a> <a href='/sitemap.html'>Карта сайта</a>"
        "<a href='https://elsewhere.test/sitemap.xml'>чужая</a> <a href='/news-sitemap.xml.gz'>"
        "новости</a> <a href='/about'>о нас</a></footer></body></html>"
    )

    assert declared_in_html(html, BASE, HOST) == [
        f"{BASE}maps/index.xml",
        f"{BASE}sitemap.xml",
        f"{BASE}news-sitemap.xml.gz",
    ]


def test_the_home_page_hints_are_capped() -> None:
    links = "".join(f"<a href='/sitemap-{n}.xml'>карта</a>" for n in range(MAX_HTML_DECLARED + 3))

    assert len(declared_in_html(f"<html><body>{links}</body></html>", BASE, HOST)) == (
        MAX_HTML_DECLARED
    )


@pytest.mark.parametrize(
    ("text", "said"),
    [
        (_urlset(f"{BASE}a"), True),
        ("<?xml version='1.0'?><sitemapindex></sitemapindex>", True),
        (f"{BASE}a\n{BASE}b\n", True),
        (NOT_FOUND, False),
        ("", False),
    ],
)
def test_what_counts_as_a_map(text: str, said: bool) -> None:
    assert looks_like_map(text) is said


async def test_the_crawl_finds_the_map_the_home_page_names() -> None:
    """Обход целиком: robots.txt молчит о карте, главная называет её — обход идёт по карте."""
    site = FakeSite(
        {
            "/": "<html><head><link rel='sitemap' href='/custom-map.xml'></head><body></body></html>",
            "/a": _page(),
        },
        sitemap=_urlset(f"{BASE}a"),
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
    crawler = DonorCrawler(client, limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0))
    async with client:
        report = await crawler.crawl(HOST)
    await crawler.aclose()

    assert site.asked.index("/custom-map.xml") < site.asked.index("/a")
    assert report.source == "sitemap"
    assert report.sitemap_found is True
    assert "/sitemap.xml" not in site.asked


def _prefixed_urlset(*entries: tuple[str, str]) -> str:
    """Карта, где у каждого элемента префикс пространства имён — так её пишет, например,
    стандартная библиотека Python."""
    body = "".join(
        f"<ns0:url><ns0:loc>{BASE}{path}</ns0:loc><ns0:lastmod>{day}</ns0:lastmod></ns0:url>"
        for path, day in entries
    )
    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    return f"<?xml version='1.0'?><ns0:urlset xmlns:ns0='{ns}'>{body}</ns0:urlset>"


async def test_a_map_with_a_namespace_prefix_is_a_map() -> None:
    """Угаданная карта с префиксом — карта: и адреса, и даты (свежие первыми) читаются."""
    body = _prefixed_urlset(("old", "2026-01-01"), ("new", "2026-10-01"))
    handler, _ = _soft_404_except("/sitemap.xml", body)
    reader, client = _reader(handler)
    async with client:
        scan = await reader.scan(HOST, BASE, [])

    assert scan.found is True
    assert scan.urls == [f"{BASE}new", f"{BASE}old"]
    assert scan.days == {f"{BASE}new": "2026-10-01", f"{BASE}old": "2026-01-01"}


async def test_an_index_with_a_namespace_prefix_leads_to_its_maps() -> None:
    """Индекс `<sitemap:sitemapindex>` — индекс: дочерние карты читаются, а не идут в обход."""
    ns = "http://www.sitemaps.org/schemas/sitemap/0.9"
    index = (
        f"<?xml version='1.0'?><sitemap:sitemapindex xmlns:sitemap='{ns}'><sitemap:sitemap>"
        f"<sitemap:loc>{BASE}posts.xml</sitemap:loc></sitemap:sitemap></sitemap:sitemapindex>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text=index)
        if request.url.path == "/posts.xml":
            return httpx.Response(200, text=_prefixed_urlset(("a", "2026-10-01")))
        return httpx.Response(200, text=NOT_FOUND)

    reader, client = _reader(handler)
    async with client:
        scan = await reader.scan(HOST, BASE, [])

    assert scan.urls == [f"{BASE}a"]
    assert scan.files_read == 2


@pytest.mark.parametrize(
    "text",
    [
        _prefixed_urlset(("a", "2026-10-01")),
        "<x:sitemapindex xmlns:x='urn:x'><x:sitemap><x:loc>https://donor.test/m.xml</x:loc>"
        "</x:sitemap></x:sitemapindex>",
    ],
)
def test_a_prefixed_map_counts_as_a_map(text: str) -> None:
    assert looks_like_map(text) is True
