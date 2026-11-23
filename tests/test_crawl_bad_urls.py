"""Плохой адрес на странице донора — пропуск одной страницы, а не смерть обхода.

21–22.11.2026 обход на бою закрывался мёртвым из-за одной ссылки: невидимый знак в адресе
(`httpx.InvalidURL`: «Invalid non-printable ASCII character in URL») и шаг редиректа на чужое
имя, которое распознаётся во внутренний адрес (`UnsafeUrlError`). Ни то ни другое не
`httpx.HTTPError`: исключение пролетало мимо обработчиков, задача падала, продолжения с чекпоинта
падали на той же ссылке, и разбор мёртвых закрывал обход.

Сайты — синтетические (`.example.test`), сеть — подделка `httpx.MockTransport`, распознавание
имён — подмена: в сеть тесты не ходят. Защита исходящих — настоящая (`GuardedTransport`).
"""

from __future__ import annotations

import logging

import httpx
import pytest
from backend.features.core.domain import CrawlOutcome, StopReason
from backend.features.crawl.fetch import CascadeLevel, FetchOutcome, FetchResult, PageCascade
from backend.features.crawl.frontier import same_site_links
from backend.features.crawl.health import CrawlHealth
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.progress import Checkpoint
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.sitemap import SitemapScan
from backend.features.crawl.sources import Sources, start_frontier
from backend.features.crawl.walk import DonorCrawler
from backend.shared.net import url_guard
from backend.shared.net.url_guard import GuardedTransport
from backend.shared.net.url_parts import clean_url

HOST = "donor.example.test"
HOME = f"https://{HOST}/"
#: Чужое имя, которое распознаётся во внутренний адрес: на него уводит редирект донора.
INSIDE = "blog.partner.example.test"
INNER = "10.0.0.7"
HTML = {"content-type": "text/html; charset=utf-8"}


def page(*links: str, head: str = "") -> str:
    body = "".join(f'<a href="{link}">ссылка</a>' for link in links)
    return f"<html><head>{head}</head><body><article>{body}</article></body></html>"


def urlset(*urls: str) -> str:
    entries = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return f"<?xml version='1.0'?><urlset>{entries}</urlset>"


@pytest.fixture(autouse=True)
def _names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Имя `INSIDE` ведёт во внутреннюю сеть, остальные не распознаются — для защиты это
    «безопасно» (`url_guard._resolved_addresses`), и сети не нужно."""
    monkeypatch.setattr(
        url_guard, "_resolved_addresses", lambda host: [INNER] if host == INSIDE else []
    )


class Site:
    """Донор по путям: ответы, редиректы (путь → полный адрес) и журнал запросов как есть,
    с процентами: так видно, что ушло на провод."""

    def __init__(
        self,
        pages: dict[str, str],
        *,
        files: dict[str, str] | None = None,
        moved: dict[str, str] | None = None,
        robots: str = "User-agent: *\nDisallow:\n",
    ) -> None:
        self.pages = pages
        self.files = files or {}
        self.moved = moved or {}
        self.robots = robots
        self.asked: list[str] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        self.asked.append(f"{request.url.host}{path}")
        if path in self.moved:
            return httpx.Response(302, headers={"location": self.moved[path]})
        if path == "/robots.txt":
            return httpx.Response(200, text=self.robots, headers={"content-type": "text/plain"})
        if path in self.files:
            return httpx.Response(200, text=self.files[path], headers={"content-type": "text/xml"})
        if path in self.pages:
            return httpx.Response(200, text=self.pages[path], headers=HTML)
        return httpx.Response(404, text="нет такой страницы", headers=HTML)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=GuardedTransport(httpx.MockTransport(self.handle)))

    @property
    def inside_asked(self) -> list[str]:
        return [asked for asked in self.asked if asked.startswith(INSIDE)]


async def crawl(site: Site) -> CrawlReport:
    async with site.client() as client:
        crawler = DonorCrawler(client, limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0))
        try:
            return await crawler.crawl(HOST)
        finally:
            await crawler.aclose()


class TestCleanUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "https://example.test/",
            "https://example.test/a/b?x=1&y=%20#top",
            "https://example.test:8443/путь?q=значение",
            "http://www.example.test/a%02b",
        ],
    )
    def test_an_ordinary_address_stays_as_it_was(self, url: str) -> None:
        assert clean_url(url) == url

    def test_edges_are_trimmed_and_line_breaks_dropped(self) -> None:
        """Края и переводы строки — как у браузера: их срезают и выбрасывают молча."""
        assert clean_url(" \x01https://example.test/a\x1f ") == "https://example.test/a"
        assert clean_url("https://example.test/a\tb\nc\r") == "https://example.test/abc"

    def test_a_control_character_inside_is_encoded_like_a_browser_does(self) -> None:
        cleaned = clean_url("https://example.test/a\x02b?q=\x7f")

        assert cleaned == "https://example.test/a%02b?q=%7F"
        assert str(httpx.URL(cleaned)) == cleaned, "клиент обязан открыть очищенный адрес"

    @pytest.mark.parametrize(
        "url",
        [
            "https://\x02www.example.test/x",  # управляющий знак в имени хоста
            "https://www\u200b.example.test/",  # невидимый знак в имени: IDNA не пропустит
            "https://example.test:port/",  # порт не числом
            "http://[broken/",  # «[» без пары
        ],
    )
    def test_what_a_browser_cannot_read_either_is_none(self, url: str) -> None:
        assert clean_url(url) is None


class TestQueue:
    def test_a_link_with_an_invisible_character_is_cleaned_not_fatal(self) -> None:
        html = page("/news/a\x02b", "/trail\x02", f"https://\x02www.{HOST}/x", "/plain?p=2")

        links = same_site_links(html, HOME, HOST)

        assert links == [f"{HOME}news/a%02b", f"{HOME}trail", f"{HOME}plain?p=2"]

    def test_ordinary_links_do_not_change(self) -> None:
        html = page("/a", "b/c?x=1", f"https://blog.{HOST}/статья", "/a#comments")

        links = same_site_links(html, f"{HOME}dir/", HOST)

        assert links == [f"{HOME}a", f"{HOME}dir/b/c?x=1", f"https://blog.{HOST}/статья"]

    def test_map_feed_and_checkpoint_follow_the_same_rules(self) -> None:
        """Карта и лента дают адреса мимо разбора HTML, а чекпоинт старой попытки мог
        записать адрес до этой правки: правила у всех одни (`frontier.own_page`)."""
        home = FetchResult(url=HOME, outcome=FetchOutcome.OK, html=page("/from-home"))
        found = Sources(
            SitemapScan(urls=[f"{HOME}map\x02a", f"https://\x02{HOST}/map-b"], found=True),
            [f"{HOME}feed\x01item"],
        )

        first, _ = start_frontier(home, HOST, found, Checkpoint("none"))
        resumed, _ = start_frontier(
            home,
            HOST,
            found,
            Checkpoint("links", pages=[HOME], queue=[f"{HOME}queued\x02", "https://\x02x/"]),
        )

        assert list(first.mapped) == [f"{HOME}map%02a"]
        assert list(first.linked) == [f"{HOME}feed%01item", f"{HOME}from-home"]
        assert list(resumed.linked) == [f"{HOME}queued"]


class TestCascade:
    async def test_an_address_the_client_rejects_is_skipped_with_a_reason(self) -> None:
        """Пропуск, а не исключение, — и выше по каскаду адрес не идёт: браузер включён,
        но его даже не спросили."""
        site = Site({})
        async with site.client() as client:
            cascade = PageCascade(client, browser_enabled=True)
            result = await cascade.get(f"{HOME}a\x02b")

        assert result.outcome is FetchOutcome.SKIPPED
        assert (result.reason or "").startswith("адрес не разбирается: Invalid non-printable")
        assert site.asked == []
        assert CascadeLevel.BROWSER not in cascade.degradation.asked

    async def test_a_redirect_into_the_inner_network_is_skipped_not_followed(self) -> None:
        site = Site({}, moved={"/go": f"https://{INSIDE}/deal"})
        async with site.client() as client:
            result = await PageCascade(client).get(f"{HOME}go")

        assert result.outcome is FetchOutcome.SKIPPED
        assert "адрес не прошёл защиту исходящих" in (result.reason or "")
        assert INNER in (result.reason or "")
        assert site.asked == [f"{HOST}/go"], "по чужому имени во внутреннюю сеть ходить нельзя"

    async def test_only_the_first_skip_is_told_out_loud(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Строкой — первый пропуск, дальше счётом: у сайта таких адресов бывают сотни."""
        caplog.set_level(logging.DEBUG, logger="backend.features.crawl.fetch")
        async with Site({}).client() as client:
            cascade = PageCascade(client)
            for path in ("a\x02", "b\x03", "c\x04"):
                await cascade.get(f"{HOME}{path}x")

        skips = [record for record in caplog.records if "пропущен" in record.getMessage()]
        assert [record.levelno for record in skips] == [logging.INFO, logging.DEBUG, logging.DEBUG]
        assert cascade.skipped == 3


class TestHealth:
    def test_a_skipped_address_is_neither_a_request_nor_a_failure(self) -> None:
        health = CrawlHealth(window=10, min_requests=1)
        for outcome in (FetchOutcome.OK, *[FetchOutcome.SKIPPED] * 5, FetchOutcome.ERROR):
            health.record(outcome)

        assert health.failure_share == 0.5
        assert health.requests == 2
        assert health.report()["skipped"] == 5


class TestCrawl:
    async def test_the_crawl_goes_on_past_bad_links(self) -> None:
        """Тот самый случай с боя: на главной — ссылка с невидимым знаком и ссылка, уводящая
        редиректом на чужое имя во внутренней сети. Обход доходит до конца."""
        site = Site(
            {"/": page("/a", "/bad\x02page", "/go", "/b"), "/a": page(), "/b": page()},
            moved={"/go": f"https://{INSIDE}/deal"},
        )

        report = await crawl(site)

        assert report.stop_reason is StopReason.EXHAUSTED
        assert report.outcome is CrawlOutcome.OK
        assert sorted(report.pages) == [HOME, f"{HOME}a", f"{HOME}b"]
        assert f"{HOST}/bad%02page" in site.asked, "очищенная ссылка открывается как в браузере"
        assert report.health["skipped"] == 1
        assert site.inside_asked == []

    async def test_bad_map_entries_and_a_map_leading_inside_do_not_stop_the_crawl(self) -> None:
        site = Site(
            {"/": page(), "/a": page(), "/b": page()},
            files={"/sitemap.xml": urlset(f"{HOME}a\x02", f"{HOME}b")},
            moved={"/moved.xml": f"https://{INSIDE}/sitemap.xml"},
            robots=f"User-agent: *\nDisallow:\nSitemap: {HOME}sitemap.xml\nSitemap: {HOME}moved.xml\n",
        )

        report = await crawl(site)

        assert sorted(report.pages) == [HOME, f"{HOME}a", f"{HOME}b"]
        assert report.sitemap_found is None, "карта, которую не открыть, — «не дочитана»"
        assert site.inside_asked == []

    async def test_a_feed_leading_inside_is_no_feed(self) -> None:
        feed = '<link rel="alternate" type="application/rss+xml" href="/feed-moved">'
        site = Site(
            {"/": page("/a", head=feed), "/a": page()},
            moved={"/feed-moved": f"https://{INSIDE}/feed"},
        )

        report = await crawl(site)

        assert sorted(report.pages) == [HOME, f"{HOME}a"]
        assert report.feed_items == 0
        assert site.inside_asked == []

    async def test_robots_leading_inside_ends_the_crawl_without_a_fall(self) -> None:
        """robots.txt, уводящий во внутреннюю сеть, — «не прочитан»: обход не идёт, как при
        молчащем сайте, но и не падает задачей."""
        site = Site({"/": page("/a")}, moved={"/robots.txt": f"https://{INSIDE}/robots.txt"})

        report = await crawl(site)

        assert report.stop_reason is StopReason.ROBOTS
        assert report.pages == []
        assert site.inside_asked == []
