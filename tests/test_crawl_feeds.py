"""Ленты RSS и Atom — свежие статьи донора, когда карты нет (`crawl/feeds.py`), без сети."""

from __future__ import annotations

from typing import Any

import httpx
from backend.features.crawl.feeds import (
    FEED_PATHS,
    MAX_DECLARED,
    FeedReader,
    declared_feeds,
    feed_urls,
)
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.progress import Checkpoint, Progress
from backend.features.crawl.walk import DonorCrawler
from tests.test_crawl_walk import HOST, FakeSite, _page, _urlset

BASE = f"https://{HOST}/"


def _rss(*paths: str) -> str:
    items = "".join(f"<item><title>т</title><link>{BASE}{p}</link></item>" for p in paths)
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{items}</channel></rss>"


def _atom(*paths: str) -> str:
    entries = "".join(
        f"<entry><link rel='self' href='{BASE}feed/{p}'/>"
        f"<link rel='alternate' type='text/html' href='{BASE}{p}'/></entry>"
        for p in paths
    )
    return f"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'>{entries}</feed>"


def test_rss_items_keep_the_feed_order() -> None:
    foreign = "<item><link>https://elsewhere.test/x</link></item></channel>"
    text = _rss("new", "older", "new").replace("</channel>", foreign)

    assert feed_urls(text, HOST) == [f"{BASE}new", f"{BASE}older"]


def test_atom_entries_give_their_alternate_link() -> None:
    plain = f"<feed><entry><link href='{BASE}bare'/></entry></feed>"

    assert feed_urls(_atom("one", "two"), HOST) == [f"{BASE}one", f"{BASE}two"]
    assert feed_urls(plain, HOST) == [f"{BASE}bare"]


def test_the_home_page_declares_its_feeds() -> None:
    html = (
        "<html><head>"
        "<link rel='alternate' type='application/rss+xml' href='/feed/'>"
        "<link rel='alternate' type='application/atom+xml' href='https://donor.test/atom.xml'>"
        "<link rel='alternate' hreflang='en' href='/en/'>"
        "<link rel='alternate' type='application/rss+xml' href='https://elsewhere.test/feed'>"
        "<link rel='stylesheet' type='application/rss+xml' href='/not-a-feed'>"
        "</head></html>"
    )

    assert declared_feeds(html, BASE, HOST) == [f"{BASE}feed/", f"{BASE}atom.xml"]


def test_declared_feeds_are_capped() -> None:
    links = "".join(
        f"<link rel='alternate' type='application/rss+xml' href='/c{n}/feed'>"
        for n in range(MAX_DECLARED + 2)
    )

    assert len(declared_feeds(f"<html><head>{links}</head></html>", BASE, HOST)) == MAX_DECLARED


def _reader(handler: Any) -> tuple[FeedReader, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return FeedReader(client, DomainLimiter(delay_sec=0.0, max_delay_sec=0.0)), client


async def test_guessed_paths_until_the_first_feed_with_items() -> None:
    """Главная ленты не объявила — типичные адреса, до первой ленты с записями; страница
    со статусом 200, но не лента, и обрыв — не лента, поиск идёт дальше."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/feed":
            return httpx.Response(200, text="<html><body>не лента</body></html>")
        if request.url.path == "/rss":
            raise httpx.ConnectError("оборвалось")
        if request.url.path == "/rss.xml":
            return httpx.Response(200, text=_rss("fresh"))
        return httpx.Response(404)

    reader, client = _reader(handler)
    async with client:
        found = await reader.read(HOST, BASE, [])

    assert found == [f"{BASE}fresh"]
    assert asked == ["/feed", "/rss", "/rss.xml"]


async def test_declared_feeds_replace_the_guesses() -> None:
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(404)

    reader, client = _reader(handler)
    async with client:
        found = await reader.read(HOST, BASE, [f"{BASE}custom/feed"])

    assert found == []
    assert asked == ["/custom/feed"]


async def test_no_feed_anywhere_is_an_empty_list() -> None:
    reader, client = _reader(lambda _request: httpx.Response(404))
    async with client:
        assert await reader.read(HOST, BASE, []) == []


def _feed_site(*, sitemap: str | None = None) -> FakeSite:
    home = (
        "<html><head><link rel='alternate' type='application/rss+xml' href='/feed'></head>"
        f"<body>{_page('/rubric')}</body></html>"
    )
    return FakeSite(
        {"/": home, "/feed": _rss("new-1", "new-2"), "/new-1": _page(), "/new-2": _page(),
         "/rubric": _page()},
        sitemap=sitemap,
    )  # fmt: skip


async def _crawl(site: FakeSite, progress: Progress | None = None) -> Any:
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
    crawler = DonorCrawler(client, limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0))
    async with client:
        try:
            return await crawler.crawl(HOST, progress)
        finally:
            await crawler.aclose()


async def test_without_a_map_the_feed_articles_go_first() -> None:
    """Карты нет: статьи ленты открываются сразу после главной, до ссылок навигации."""
    site = _feed_site()

    report = await _crawl(site)

    assert site.opened[:3] == ["/", "/new-1", "/new-2"]
    assert "/rubric" in site.opened
    assert report.feed_items == 2
    assert report.as_dict()["feed_items"] == 2
    assert report.source == "links"
    assert report.sitemap_found is False


async def test_with_a_map_the_feed_is_not_asked_for() -> None:
    site = _feed_site(sitemap=_urlset(f"{BASE}new-1"))

    report = await _crawl(site)

    assert "/feed" not in site.asked
    assert report.feed_items == 0


async def test_a_resumed_crawl_does_not_read_the_feed_again() -> None:
    """У продолжения очередь ссылок — из чекпоинта: лента уже в ней."""
    site = _feed_site()
    point = Checkpoint(source="links", pages=[BASE], queue=[f"{BASE}new-2"])

    async def ignore(_batch: object) -> None:
        return None

    await _crawl(site, Progress(flush=ignore, resume=point))

    assert "/feed" not in site.asked
    assert not set(FEED_PATHS) & set(site.asked)
