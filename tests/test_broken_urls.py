"""Битый адрес из внешнего мира — мусор, а не падение.

Находка «Продаж» (03.10.2026): `normalize_host("[")` бросал «Invalid IPv6
URL», хотя обещал пустую строку, — а с ним и разбор выдачи в планировании.
Тот же `ValueError` у `urljoin` и `urlparse` из-за одной ссылки ронял разбор
целой страницы: сбор ссылок рекламодателей, очередь обхода, поиск раздела
контактов, карту сайта, редирект в следе обхода.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.ladder import ContactLadder
from backend.features.contacts.sweep_trace import SiteTrace
from backend.features.crawl.links import harvest
from backend.features.crawl.sitemap import _same_site
from backend.features.crawl.walk import same_site_links
from backend.features.donors.author_door import author_door
from backend.features.donors.backfill import _site_search
from backend.features.donors.site_index import index_homes
from backend.features.serp.protocol import SerpResult
from backend.shared.net.url_parts import join_url, parse_url, split_url

#: Как такие адреса выглядят в живом HTML.
BROKEN = [
    "http://[broken",  # «[» без пары — начало адреса IPv6
    "https://exa[mple.com/x",
    "https://example.com]/",
    "https://example.com／contact",  # полноширинная косая черта: NFKC меняет хост
]


class TestParsers:
    @pytest.mark.parametrize("url", BROKEN)
    def test_broken_address_is_none(self, url: str) -> None:
        assert split_url(url) is None
        assert parse_url(url) is None
        assert join_url("https://site.com/post/", url) is None

    def test_good_address_is_parsed_as_before(self) -> None:
        parts = split_url("https://Site.com/a?b=1")
        assert parts is not None
        assert parts.hostname == "site.com"
        parsed = parse_url("https://site.com/a;x?b=1")
        assert parsed is not None
        assert (parsed.path, parsed.params) == ("/a", "x")
        assert join_url("https://site.com/a/b/", "../c") == "https://site.com/a/c"


class TestOnePageOneBrokenLink:
    """Одна битая ссылка не стоит остальных ссылок страницы."""

    def test_advertiser_links_survive(self) -> None:
        links = "".join(f'<a href="{url}">x</a> ' for url in BROKEN)
        html = (
            "<html><body><article><p>Text "
            f'{links}<a href="https://advertiser.net/offer">offer</a>'
            "</p></article></body></html>"
        )
        found = harvest(html, "https://site.com/post/", "site.com")
        assert [link.url for link in found] == ["https://advertiser.net/offer"]

    def test_crawl_queue_survives(self) -> None:
        links = "".join(f'<a href="{url}">x</a>' for url in BROKEN)
        html = f'<html><body>{links}<a href="/about/">about</a></body></html>'
        assert same_site_links(html, "https://site.com/", "site.com") == ["https://site.com/about/"]

    @pytest.mark.parametrize("url", BROKEN)
    def test_sitemap_entry_is_not_our_site(self, url: str) -> None:
        assert not _same_site(url, "site.com")

    def test_redirect_to_a_broken_address_is_not_a_new_host(self) -> None:
        trace = SiteTrace(hosts={"site.com"})
        request = httpx.Request("GET", "https://site.com/")
        trace.received(httpx.Response(301, headers={"location": BROKEN[0]}, request=request))
        assert trace.hosts == {"site.com"}

    def test_author_door_reads_the_title_past_a_broken_url(self) -> None:
        assert author_door(BROKEN[0], None) is None
        assert author_door(BROKEN[0], "Write for Us") is not None


@pytest.fixture
def _mx_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


@pytest.mark.usefixtures("_mx_is_fine")
async def test_contact_link_next_to_a_broken_one_is_followed() -> None:
    """Анкор «Contact» у битой ссылки берёт её в обход по тексту — дальше
    она отпадает при сборке адреса, а соседняя рабочая ведёт к ящику."""
    pages = {
        "/": f'<html><body><a href="{BROKEN[0]}">Contact</a> <a href="/reach/">Contact us</a>'
        "</body></html>",
        "/reach/": '<html><body><a href="mailto:editor@site.com">mail</a></body></html>',
    }

    def site(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path) if request.url.host == "site.com" else None
        if body is None:
            return httpx.Response(404, text="нет", request=request)
        return httpx.Response(200, html=body, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
        result = await ContactLadder(http).find("site.com")
    assert result.contact is not None
    assert result.contact.email == "editor@site.com"


def test_search_result_with_a_broken_url_is_skipped() -> None:
    class Provider:
        spent = 0.0

        async def search(
            self, keywords: list[str], country: str, **_: object
        ) -> dict[str, list[SerpResult]]:
            return {
                "site:site.com": [
                    SerpResult(1, "https://site.com]/guide", "Broken"),
                    SerpResult(2, "https://site.com/guide/x", "A guide to x"),
                ]
            }

    found, _ = asyncio.run(_site_search(Provider(), ["site.com"]))  # type: ignore[arg-type]
    assert found["site.com"].url == "https://site.com/guide/x"


def test_index_home_past_a_broken_url() -> None:
    """Битый адрес в выдаче корнем сайта не считается и разбор не роняет."""

    class Provider:
        spent = 0.0

        async def search(
            self, keywords: list[str], country: str, **_: object
        ) -> dict[str, list[SerpResult]]:
            return {
                "site:shop.test": [
                    SerpResult(1, "https://shop.test]/x", "Broken"),
                    SerpResult(2, "https://shop.test/", "Shop Test"),
                ]
            }

    homes, _ = asyncio.run(index_homes(Provider(), ["shop.test"]))  # type: ignore[arg-type]
    assert homes["shop.test"].title == "Shop Test"
