"""Обход донора целиком: от robots.txt до отчёта.

Сайт здесь поддельный, а порядок работы настоящий. Проверяется то, что
по зелёному прогону не видно: что запрещённый адрес не запрашивается
вовсе, что «не прочитали robots» останавливает обход, а не разрешает
его, и что несостоявшийся уровень каскада попадает в отчёт, а не только
в лог.

Второй способ проверки — живой прогон по настоящему донору
(`outreach crawl`): тесты не умеют показать долю страниц под антиботом,
а ради неё обход и написан.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from backend.features.core.domain import CrawlOutcome, StopReason
from backend.features.crawl.fetch import CascadeLevel
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.robots import RobotsStatus
from backend.features.crawl.walk import DonorCrawler, same_site_links

HOST = "donor.test"
HTML = {"content-type": "text/html; charset=utf-8"}


def _page(*links: str) -> str:
    body = "".join(f'<a href="{link}">ссылка</a>' for link in links)
    return f"<html><body><article>{body}</article></body></html>"


def _urlset(*urls: str) -> str:
    body = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return f"<?xml version='1.0'?><urlset>{body}</urlset>"


class FakeSite:
    """Поддельный донор: карта путей в ответы плюс журнал запросов.

    Журнал здесь не для отладки: половина проверок этого файла — про то,
    чего обход делать НЕ должен, а «не сходил по запрещённому адресу»
    иначе никак не увидеть.
    """

    def __init__(
        self,
        pages: dict[str, str],
        *,
        robots: str | None = "User-agent: *\nDisallow:\n",
        sitemap: str | None = None,
        status: int = 200,
    ) -> None:
        self.pages = pages
        self.robots = robots
        self.sitemap = sitemap
        self.status = status
        self.asked: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.asked.append(path)

        if path == "/robots.txt":
            return self._robots_response()
        if path.endswith(".xml"):
            return self._sitemap_response()
        if path in self.pages:
            return httpx.Response(self.status, text=self.pages[path], headers=HTML)
        return httpx.Response(404, text="нет такой страницы", headers=HTML)

    def _robots_response(self) -> httpx.Response:
        if self.robots is None:
            return httpx.Response(404)
        if self.robots == "__error__":
            return httpx.Response(500, text="упало")
        return httpx.Response(200, text=self.robots, headers={"content-type": "text/plain"})

    def _sitemap_response(self) -> httpx.Response:
        if self.sitemap is None:
            return httpx.Response(404)
        return httpx.Response(200, text=self.sitemap, headers={"content-type": "text/xml"})

    @property
    def opened(self) -> list[str]:
        """Только страницы: robots и карта — служебные запросы."""
        return [p for p in self.asked if p != "/robots.txt" and not p.endswith(".xml")]


class _SpyLimiter(DomainLimiter):
    """Ограничитель без пауз, который помнит, о чём его просили."""

    def __init__(self) -> None:
        super().__init__(delay_sec=0.0, max_delay_sec=0.0)
        self.requested: list[float | None] = []

    def set_delay(self, host: str, requested: float | None) -> float:
        self.requested.append(requested)
        return super().set_delay(host, requested)


def _crawler(site: FakeSite, **kwargs: Any) -> tuple[DonorCrawler, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
    limiter = DomainLimiter(delay_sec=0.0, max_delay_sec=0.0)
    return DonorCrawler(client, limiter=limiter, **kwargs), client


async def _crawl(site: FakeSite, **kwargs: Any) -> Any:
    crawler, client = _crawler(site, **kwargs)
    async with client:
        report = await crawler.crawl(HOST)
    await crawler.aclose()
    return report


class TestPermission:
    async def test_forbidden_donor_is_set_aside_untouched(self) -> None:
        """Запрещает — откладываем и помечаем. Ни одной страницы при этом
        не запрашивается: иначе «соблюдаем robots.txt» — это слова."""
        site = FakeSite({"/": _page()}, robots="User-agent: *\nDisallow: /\n")

        report = await _crawl(site)

        assert report.outcome is CrawlOutcome.FORBIDDEN
        assert report.stop_reason is StopReason.ROBOTS
        assert site.opened == []

    async def test_unread_robots_stops_the_crawl(self) -> None:
        """Молчание сайта — не согласие. Обход не идёт, и это видно
        в исходе: `failed`, а не `ok` с нулём страниц."""
        site = FakeSite({"/": _page()}, robots="__error__")

        report = await _crawl(site)

        assert report.outcome is CrawlOutcome.FAILED
        assert report.stop_reason is StopReason.ROBOTS
        assert report.robots_status is RobotsStatus.UNREADABLE
        assert site.opened == []

    async def test_missing_robots_means_no_restrictions(self) -> None:
        site = FakeSite({"/": _page("/a"), "/a": _page()}, robots=None)

        report = await _crawl(site)

        assert report.robots_status is RobotsStatus.ABSENT
        assert report.outcome is CrawlOutcome.OK

    async def test_forbidden_sections_are_skipped_not_fetched(self) -> None:
        """Запрет на раздел обязан работать и тогда, когда адрес пришёл
        из карты сайта: карта — это предложение сайта, а не разрешение."""
        site = FakeSite(
            {"/": _page(), "/blog/a": _page(), "/private/b": _page()},
            robots="User-agent: *\nDisallow: /private\n",
            sitemap=_urlset(f"https://{HOST}/blog/a", f"https://{HOST}/private/b"),
        )

        report = await _crawl(site)

        assert "/private/b" not in site.opened
        # Главная открыта проверкой «сайт открывается» и потому в списке;
        # из карты сайта взят только разрешённый адрес.
        assert report.pages == [f"https://{HOST}/", f"https://{HOST}/blog/a"]

    async def test_crawl_delay_reaches_the_limiter(self) -> None:
        """Пауза, о которой просит сайт, обязана дойти до ограничителя.

        Проверяется тем, что до него дошло, а не настоящим ожиданием:
        три секунды на запрос — это тест, который захочется выключить.
        """
        site = FakeSite({"/": _page()}, robots="User-agent: *\nCrawl-delay: 3\nDisallow:\n")
        limiter = _SpyLimiter()
        client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
        crawler = DonorCrawler(client, limiter=limiter)

        async with client:
            report = await crawler.crawl(HOST)

        assert report.crawl_delay == 3.0
        assert limiter.requested == [3.0]


class TestSources:
    async def test_sitemap_is_the_source_when_there_is_one(self) -> None:
        site = FakeSite(
            {"/": _page(), "/a": _page(), "/b": _page()},
            robots=f"User-agent: *\nDisallow:\nSitemap: https://{HOST}/sitemap.xml\n",
            sitemap=_urlset(f"https://{HOST}/a", f"https://{HOST}/b"),
        )

        report = await _crawl(site)

        assert report.source == "sitemap"
        assert report.sitemap_found is True
        assert sorted(report.pages) == [
            f"https://{HOST}/",
            f"https://{HOST}/a",
            f"https://{HOST}/b",
        ]

    async def test_links_are_the_fallback_when_there_is_no_map(self) -> None:
        """Карта есть не у всех, и без запасного пути каждый пятый донор
        остался бы необойдённым."""
        site = FakeSite(
            {"/": _page("/a", "/b"), "/a": _page("/c"), "/b": _page(), "/c": _page()},
            sitemap=None,
        )

        report = await _crawl(site)

        assert report.source == "links"
        assert len(report.pages) == 4

    async def test_home_page_is_fetched_once(self) -> None:
        """Главную качает проверка «сайт вообще открывается», и она же
        даёт первые ссылки. Второй запрос за той же страницей — лишний
        стук в чужую машину на каждом доноре."""
        site = FakeSite({"/": _page("/a"), "/a": _page()}, sitemap=None)

        report = await _crawl(site)

        assert site.opened.count("/") == 1
        assert len(report.pages) == 2

    async def test_unread_map_does_not_pretend_to_be_complete(self) -> None:
        """Карта, которая не дочиталась, — это `None`, а не «страниц нет»."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith(".xml"):
                raise httpx.ConnectError("оборвалось")
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, text=_page(), headers=HTML)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        crawler = DonorCrawler(client, limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0))
        async with client:
            report = await crawler.crawl(HOST)

        assert report.sitemap_found is None
        assert report.sitemap_complete is False


class TestLinks:
    def test_only_our_own_pages_get_into_the_queue(self) -> None:
        html = _page(
            "/own",
            "https://other.test/theirs",
            "https://notdonor.test/trap",
            "/file.pdf",
            "mailto:a@b.test",
            "#comments",
        )

        links = same_site_links(html, f"https://{HOST}/", HOST)

        assert links == [f"https://{HOST}/own"]

    def test_anchors_do_not_multiply_a_page(self) -> None:
        """`/post` и `/post#comments` — одна страница. Без отсечения якоря
        обход платит за неё столько раз, сколько на ней разделов."""
        html = _page("/post", "/post#comments", "/post#footer")

        assert same_site_links(html, f"https://{HOST}/", HOST) == [f"https://{HOST}/post"]

    def test_subdomains_are_ours(self) -> None:
        html = _page("https://blog.donor.test/post")

        assert same_site_links(html, f"https://{HOST}/", HOST) == ["https://blog.donor.test/post"]


class TestCaps:
    async def test_page_cap_stops_the_crawl(self) -> None:
        pages = {f"/p{n}": _page() for n in range(20)}
        pages["/"] = _page(*[f"/p{n}" for n in range(20)])
        site = FakeSite(pages)

        report = await _crawl(site, max_pages=5)

        assert len(report.pages) == 5
        assert report.stop_reason is StopReason.MAX_PAGES

    async def test_attempt_cap_counts_misses_too(self) -> None:
        """Потолок попыток считается отдельно от открытых страниц: сайт,
        отвечающий отказом на большинство адресов из карты, иначе съел бы
        бюджет, не дав ни одной страницы."""
        site = FakeSite(
            {"/": _page()},
            sitemap=_urlset(*[f"https://{HOST}/ghost{n}" for n in range(30)]),
        )

        report = await _crawl(site, max_attempts=6)

        assert report.stop_reason is StopReason.MAX_ATTEMPTS
        # Открылась только главная: остальной бюджет попыток ушёл
        # на несуществующие адреса из карты.
        assert report.pages == [f"https://{HOST}/"]

    async def test_time_cap_is_checked_before_pages(self) -> None:
        """Чекпоинт по времени, а не по числу страниц: лимит на донора
        меньше чекпоинта из требования, и тот не сработал бы ни разу."""
        site = FakeSite({"/": _page("/a"), "/a": _page()})

        report = await _crawl(site, max_seconds=0.0)

        assert report.stop_reason is StopReason.TIMEOUT

    async def test_cut_short_crawl_is_partial_not_ok(self) -> None:
        """Одна страница из тысячи — это не «обойдён».

        Найдено живым прогоном: сайт просил паузу в десять секунд, за срок
        успели одну страницу, а исход назывался `ok`. Посчитав по такому
        донору рекламодателей, мы решили бы, что видели весь сайт.
        """
        pages = {f"/p{n}": _page() for n in range(10)}
        pages["/"] = _page(*[f"/p{n}" for n in range(10)])
        site = FakeSite(pages)

        report = await _crawl(site, max_attempts=3)

        assert report.stop_reason is StopReason.MAX_ATTEMPTS
        assert report.pages
        assert report.outcome is CrawlOutcome.PARTIAL

    async def test_full_crawl_is_ok(self) -> None:
        site = FakeSite({"/": _page("/a"), "/a": _page()})

        report = await _crawl(site)

        assert report.stop_reason is StopReason.EXHAUSTED
        assert report.outcome is CrawlOutcome.OK


class TestHealth:
    async def test_closed_site_ends_as_blocked_not_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Донор, закрывшийся от нас, и донор без страниц — разные вещи.
        Первое решается деньгами, второе означает, что донор не наш."""
        monkeypatch.setattr("backend.config.crawl.STOP_SHARE", 0.30)
        monkeypatch.setattr("backend.config.crawl.SLOW_DOWN_SHARE", 0.10)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(403, text="Access denied", headers=HTML)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        crawler = DonorCrawler(client, limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0))
        async with client:
            report = await crawler.crawl(HOST)

        assert report.outcome is CrawlOutcome.BLOCKED
        assert report.health["blocked"] > 0
        assert report.health["blocked_share"] == 1.0

    async def test_challenge_page_counts_as_closed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Код 200 с заставкой — это закрытый сайт. Посчитав его открытым,
        мы получили бы донора «обойдён, ссылок нет»."""
        monkeypatch.setattr("backend.config.crawl.STOP_SHARE", 0.30)
        challenge = "<html><title>Just a moment...</title></html>"

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, text=challenge, headers=HTML)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        crawler = DonorCrawler(client, limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0))
        async with client:
            report = await crawler.crawl(HOST)

        assert report.outcome is CrawlOutcome.BLOCKED
        assert report.pages == []

    async def test_pace_halves_when_failures_pile_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.config.crawl.SLOW_DOWN_SHARE", 0.10)
        monkeypatch.setattr("backend.config.crawl.STOP_SHARE", 0.90)
        monkeypatch.setattr("backend.config.crawl.HEALTH_MIN_REQUESTS", 5)

        pages = {"/": _page(*[f"/p{n}" for n in range(10)])}
        pages.update({f"/p{n}": _page() for n in range(5)})
        site = FakeSite(pages)
        limiter = DomainLimiter(delay_sec=0.0, max_delay_sec=2.0)
        client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
        crawler = DonorCrawler(client, limiter=limiter, max_attempts=12)

        async with client:
            report = await crawler.crawl(HOST)

        # Половина адресов отвечает 404 — это не отказ; отказом здесь
        # становится их отсутствие только при 5xx, поэтому темп не падает.
        assert report.slowed_down is False


class TestReport:
    async def test_missing_cascade_level_is_written_down(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Браузер включён настройкой и не поднялся — это пометка в отчёте.
        Иначе обход зелёный и врёт, что закрытые страницы проверены."""
        monkeypatch.setattr("backend.config.crawl.BROWSER_ENABLED", True)
        monkeypatch.setattr("backend.config.crawl.STOP_SHARE", 0.90)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(403, text="нельзя", headers=HTML)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        crawler = DonorCrawler(client, limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0))
        async with client:
            report = await crawler.crawl(HOST)

        assert CascadeLevel.BROWSER.value in report.degradation
        assert "playwright" in report.degradation[CascadeLevel.BROWSER.value]

    async def test_report_carries_numbers_not_pages(self) -> None:
        """Сырой HTML не хранится: наружу уходят только адреса и числа."""
        site = FakeSite({"/": _page("/a"), "/a": _page()})

        report = await _crawl(site)
        record = report.as_dict()

        assert record["pages_opened"] == 2
        assert record["outcome"] == "ok"
        assert "html" not in record
        assert all("<html" not in str(value) for value in record.values())

    async def test_dead_site_is_failed_not_blocked(self) -> None:
        """Главная не открылась ни в одном виде — это поломка, а не отказ:
        путать их значит покупать прокси там, где донора просто нет."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            raise httpx.ConnectError("некуда идти")

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        crawler = DonorCrawler(client, limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0))
        async with client:
            report = await crawler.crawl(HOST)

        assert report.outcome is CrawlOutcome.FAILED
        assert report.stop_reason is StopReason.NO_START


class TestHarvest:
    """Ссылки снимаются по ходу обхода — и только из тела статьи."""

    @staticmethod
    def _article(*links: str) -> str:
        body = "Текст статьи про ставки и коэффициенты. " * 20
        anchors = "".join(f'<a href="{href}">оффер</a>' for href in links)
        return (
            "<html><body>"
            '<nav><a href="https://menu-sponsor.test/">меню</a></nav>'
            f"<article><p>{body}</p><p>{anchors}</p></article>"
            '<footer><a href="https://footer-sponsor.test/">подвал</a></footer>'
            "</body></html>"
        )

    async def test_links_come_from_the_body_of_every_page(self) -> None:
        site = FakeSite(
            {
                "/": self._article("https://advertiser-one.com/"),
                "/post": self._article("https://advertiser-two.com/"),
            },
            sitemap=None,
        )
        site.pages["/"] = site.pages["/"].replace(
            "<article>", '<article><a href="/post">дальше</a>', 1
        )

        report = await _crawl(site)
        roots = sorted({link.target_root for link in report.links})

        assert roots == ["advertiser-one.com", "advertiser-two.com"]
        assert report.articles == 2

    async def test_pages_without_a_body_are_counted_apart(self) -> None:
        """Страница без статьи — не страница без ссылок. Разница видна
        по счётчику статей рядом с числом открытых страниц."""
        site = FakeSite({"/": '<html><body><div class="listing">список</div></body></html>'})

        report = await _crawl(site)

        assert len(report.pages) == 1
        assert report.articles == 0
        assert report.links == []

    async def test_report_counts_advertisers_not_links(self) -> None:
        site = FakeSite(
            {"/": self._article("https://one.com/a", "https://one.com/b", "https://two.com/")}
        )

        record = (await _crawl(site)).as_dict()

        assert record["links_found"] == 3
        assert record["advertisers"] == 2
        assert record["roots_guessed"] == 0


class TestNoDoubleWork:
    async def test_home_page_listed_in_the_sitemap_is_not_fetched_again(self) -> None:
        """Карта сайта почти всегда перечисляет главную, а её уже качала
        проверка «сайт открывается». Найдено в базе: статей оказалось
        больше, чем открытых страниц."""
        site = FakeSite(
            {"/": _page(), "/a": _page()},
            sitemap=_urlset(f"https://{HOST}/", f"https://{HOST}/a"),
        )

        report = await _crawl(site)

        assert site.opened.count("/") == 1
        assert len(report.pages) == 2
