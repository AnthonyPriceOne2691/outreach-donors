"""Правила обхода: robots.txt, признаки антибота, здоровье, ограничитель.

Каждый случай здесь — либо строка требования Этапа 2, либо ошибка,
которую легко сделать и невозможно заметить по зелёному прогону:
запрет, адресованный нам лично; «не прочитали» вместо «разрешено»;
404, посчитанный отказом; пауза, отмеренная от начала запроса.
"""

from __future__ import annotations

import asyncio
import gzip
import time
from typing import Any

import httpx
import pytest
from backend.features.crawl import robots as robots_module
from backend.features.crawl.fetch import (
    FetchOutcome,
    PageCascade,
    classify,
    headers_for,
    looks_like_antibot,
)
from backend.features.crawl.health import CrawlHealth, HealthVerdict
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.robots import RobotsStatus, parse_robots
from backend.features.crawl.sitemap import SitemapReader

AGENT = "ParsingPricesBot"


def _response(status: int, body: str = "", headers: dict[str, str] | None = None) -> httpx.Response:
    request = httpx.Request("GET", "https://donor.test/page")
    return httpx.Response(status, text=body, headers=headers or {}, request=request)


class TestRobots:
    def test_ban_addressed_to_us_beats_the_common_group(self) -> None:
        """Сайт вписал нас поимённо — значит, общая группа больше не про нас.

        Иначе наш user-agent украшение: сайт нас запретил, а мы ходим
        по правилам для всех остальных.
        """
        rules = parse_robots(
            "User-agent: *\nDisallow:\n\nUser-agent: ParsingPricesBot\nDisallow: /\n",
            AGENT,
        )

        assert rules.allows("https://donor.test/article") is False

    def test_user_agent_after_rules_opens_a_new_group(self) -> None:
        """Границу групп задаёт порядок строк. Собрав все имена в одну
        группу, мы применили бы к себе чужой запрет."""
        rules = parse_robots(
            "User-agent: EvilBot\nDisallow: /\n\nUser-agent: *\nDisallow: /admin\n",
            AGENT,
        )

        assert rules.allows("https://donor.test/article") is True
        assert rules.allows("https://donor.test/admin/panel") is False

    def test_longest_pattern_wins_and_allow_breaks_the_tie(self) -> None:
        rules = parse_robots(
            "User-agent: *\nDisallow: /blog\nAllow: /blog/public\n",
            AGENT,
        )

        assert rules.allows("https://donor.test/blog/secret") is False
        assert rules.allows("https://donor.test/blog/public/post") is True

    def test_equal_length_allow_wins(self) -> None:
        rules = parse_robots("User-agent: *\nDisallow: /p\nAllow: /p\n", AGENT)

        assert rules.allows("https://donor.test/p") is True

    def test_empty_disallow_means_everything_is_allowed(self) -> None:
        """`Disallow:` без значения — это «запретов нет». Прочитав его как
        «запрещено всё», мы потеряли бы доноров на ровном месте."""
        rules = parse_robots("User-agent: *\nDisallow:\n", AGENT)

        assert rules.allows("https://donor.test/anything") is True

    def test_wildcards_and_end_anchor(self) -> None:
        rules = parse_robots(
            "User-agent: *\nDisallow: /*.php$\nDisallow: /tag/*/feed\n",
            AGENT,
        )

        assert rules.allows("https://donor.test/index.php") is False
        assert rules.allows("https://donor.test/index.php?id=1") is True
        assert rules.allows("https://donor.test/tag/seo/feed") is False

    def test_sitemap_is_collected_outside_groups(self) -> None:
        """`Sitemap` — поле файла, а не группы: сайты пишут его где угодно."""
        rules = parse_robots(
            "Sitemap: https://donor.test/a.xml\nUser-agent: *\n"
            "Disallow: /x\nSitemap: https://donor.test/b.xml\n",
            AGENT,
        )

        assert rules.sitemaps == ["https://donor.test/a.xml", "https://donor.test/b.xml"]

    def test_crawl_delay_is_read(self) -> None:
        rules = parse_robots("User-agent: *\nCrawl-delay: 4.5\nDisallow:\n", AGENT)

        assert rules.crawl_delay == 4.5

    def test_broken_crawl_delay_is_loud_and_ignored(self, caplog: pytest.LogCaptureFixture) -> None:
        """Не число — предупреждение, а не тихий ноль: тихий ноль означал бы
        темп без паузы там, где сайт о паузе просил."""
        with caplog.at_level("WARNING"):
            rules = parse_robots("User-agent: *\nCrawl-delay: медленно\n", AGENT)

        assert rules.crawl_delay is None
        assert "Crawl-delay" in caplog.text

    def test_unreadable_robots_forbids_everything(self) -> None:
        """Молчание сайта — не согласие. Это единственный безопасный ответ
        на «мы не смогли прочитать правила»."""
        rules = robots_module.unreadable()

        assert rules.readable is False
        assert rules.allows("https://donor.test/") is False

    def test_absent_robots_allows_everything(self) -> None:
        rules = robots_module.absent()

        assert rules.status is RobotsStatus.ABSENT
        assert rules.allows("https://donor.test/anything") is True

    def test_rules_of_another_agent_do_not_apply(self) -> None:
        rules = parse_robots("User-agent: GPTBot\nDisallow: /\n", AGENT)

        assert rules.allows("https://donor.test/article") is True


class TestAntibotSignals:
    def test_status_codes_are_sorted_by_meaning(self) -> None:
        """404 и 403 — разные исходы: первое обычная жизнь сайта, второе
        повод к следующему уровню каскада и к тревоге."""
        html = "<html><body>ok</body></html>"
        headers = {"content-type": "text/html"}

        assert classify(_response(403, headers=headers), None)[0] is FetchOutcome.BLOCKED
        assert classify(_response(429, headers=headers), None)[0] is FetchOutcome.BLOCKED
        assert classify(_response(404, headers=headers), None)[0] is FetchOutcome.MISSING
        assert classify(_response(503, headers=headers), None)[0] is FetchOutcome.ERROR
        assert classify(_response(200, html, headers), html)[0] is FetchOutcome.OK

    def test_two_hundred_with_a_challenge_is_a_refusal(self) -> None:
        """Код 200 и «Just a moment…» — это отказ. Посчитав его успехом,
        мы получили бы прогон, где все доноры обошлись без единой ссылки."""
        html = "<html><head><title>Just a moment...</title></head></html>"

        outcome, reason = classify(_response(200, html, {"content-type": "text/html"}), html)

        assert outcome is FetchOutcome.BLOCKED
        assert reason is not None

    def test_non_html_is_not_a_page(self) -> None:
        body = "{}"
        outcome, _ = classify(_response(200, body, {"content-type": "application/json"}), body)

        assert outcome is FetchOutcome.MISSING

    def test_challenge_words_deep_in_an_article_are_not_a_challenge(self) -> None:
        """Заставка короткая и стоит первой; статья может цитировать что
        угодно, и обыск всей страницы дал бы ложные отказы."""
        article = "<html><body>" + ("текст " * 3000) + "just a moment</body></html>"

        assert looks_like_antibot(article) is False


class TestHealth:
    def test_missing_pages_are_not_failures(self) -> None:
        """Сайт, честно сказавший «нет такой страницы», здоров. Иначе обход
        замедлялся бы там, где просто старый список адресов."""
        health = CrawlHealth(window=100, min_requests=1)
        for _ in range(50):
            health.record(FetchOutcome.MISSING)

        assert health.failure_share == 0.0
        assert health.verdict is HealthVerdict.OK

    def test_small_samples_are_not_measured_by_share(self) -> None:
        """Три отказа из трёх — это три отказа, а не сто процентов."""
        health = CrawlHealth(window=100, min_requests=20)
        for _ in range(3):
            health.record(FetchOutcome.BLOCKED)

        assert health.failure_share == 1.0
        assert health.verdict is HealthVerdict.OK

    def test_window_forgets_the_healthy_start(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Защита, включившаяся на середине, обязана быть видна: по среднему
        за всё время прогон выглядит здоровым до самого конца."""
        monkeypatch.setattr("backend.config.crawl.STOP_SHARE", 0.30)
        health = CrawlHealth(window=10, min_requests=5)
        for _ in range(20):
            health.record(FetchOutcome.OK)
        for _ in range(10):
            health.record(FetchOutcome.BLOCKED)

        assert health.verdict is HealthVerdict.STOP
        assert health.totals[FetchOutcome.OK] == 20

    def test_slow_down_sits_between_the_thresholds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("backend.config.crawl.SLOW_DOWN_SHARE", 0.10)
        monkeypatch.setattr("backend.config.crawl.STOP_SHARE", 0.30)
        health = CrawlHealth(window=10, min_requests=5)
        for _ in range(8):
            health.record(FetchOutcome.OK)
        health.record(FetchOutcome.BLOCKED)
        health.record(FetchOutcome.ERROR)

        assert health.verdict is HealthVerdict.SLOW_DOWN

    def test_blocked_share_counts_all_requests(self) -> None:
        """То самое число замера: от него зависит смета Этапа 2."""
        health = CrawlHealth(window=10, min_requests=1)
        for _ in range(3):
            health.record(FetchOutcome.BLOCKED)
        for _ in range(7):
            health.record(FetchOutcome.OK)

        assert health.blocked_share == pytest.approx(0.3)
        assert health.report()["blocked"] == 3


@pytest.mark.asyncio
class TestDomainLimiter:
    async def test_one_operation_per_host_at_a_time(self) -> None:
        """Двадцать корутин на одном сайте — это не обход, а небольшая атака."""
        limiter = DomainLimiter(delay_sec=0.0, max_delay_sec=0.0)
        inside = 0
        peak = 0

        async def work() -> None:
            nonlocal inside, peak
            async with limiter.slot("donor.test"):
                inside += 1
                peak = max(peak, inside)
                await asyncio.sleep(0.01)
                inside -= 1

        await asyncio.gather(*(work() for _ in range(5)))

        assert peak == 1

    async def test_pause_is_measured_from_the_end_of_the_previous_call(self) -> None:
        """От начала — медленный сайт получал бы удвоенный темп: пауза
        целиком укладывалась бы внутрь его же ответа."""
        limiter = DomainLimiter(delay_sec=0.05, max_delay_sec=1.0)

        async with limiter.slot("donor.test"):
            await asyncio.sleep(0.06)  # сайт отвечал дольше паузы

        started = time.monotonic()
        async with limiter.slot("donor.test"):
            pass

        assert time.monotonic() - started >= 0.04

    async def test_site_delay_wins_but_not_above_the_ceiling(self) -> None:
        limiter = DomainLimiter(delay_sec=1.0, max_delay_sec=10.0)

        assert limiter.set_delay("slow.test", 4.0) == 4.0
        assert limiter.set_delay("fast.test", 0.0) == 1.0
        assert limiter.set_delay("crazy.test", 600.0) == 10.0

    async def test_slow_down_halves_the_pace(self) -> None:
        limiter = DomainLimiter(delay_sec=1.0, max_delay_sec=10.0)
        limiter.set_delay("donor.test", 2.0)

        assert limiter.slow_down("donor.test") == 4.0


def _client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _urlset(*urls: str) -> str:
    body = "".join(f"<url><loc>{url}</loc></url>" for url in urls)
    return f"<?xml version='1.0'?><urlset>{body}</urlset>"


def _index(*urls: str) -> str:
    body = "".join(f"<sitemap><loc>{url}</loc></sitemap>" for url in urls)
    return f"<?xml version='1.0'?><sitemapindex>{body}</sitemapindex>"


@pytest.mark.asyncio
class TestSitemap:
    @staticmethod
    def _reader(handler: Any, **kwargs: int) -> tuple[SitemapReader, httpx.AsyncClient]:
        client = _client(handler)
        limiter = DomainLimiter(delay_sec=0.0, max_delay_sec=0.0)
        return SitemapReader(client, limiter, **kwargs), client

    async def test_index_leads_to_its_files(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/sitemap.xml":
                return httpx.Response(200, text=_index("https://donor.test/posts.xml"))
            return httpx.Response(200, text=_urlset("https://donor.test/a", "https://donor.test/b"))

        reader, client = self._reader(handler)
        async with client:
            scan = await reader.scan("donor.test", "https://donor.test/", [])

        assert scan.urls == ["https://donor.test/a", "https://donor.test/b"]
        assert scan.found is True
        assert scan.complete is True

    async def test_unread_map_is_not_an_empty_map(self) -> None:
        """«Не нашли» и «не дочитали» — разные ответы. Второй обязан быть
        `None`: иначе обход объявит себя полным, обойдя четверть сайта."""

        def handler(_request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("оборвалось")

        reader, client = self._reader(handler)
        async with client:
            scan = await reader.scan("donor.test", "https://donor.test/", [])

        assert scan.found is None
        assert scan.complete is False

    async def test_absent_map_is_a_plain_no(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(404)

        reader, client = self._reader(handler)
        async with client:
            scan = await reader.scan("donor.test", "https://donor.test/", [])

        assert scan.found is False

    async def test_cap_on_urls_marks_the_list_incomplete(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, text=_urlset(*[f"https://donor.test/{n}" for n in range(50)])
            )

        reader, client = self._reader(handler, max_urls=10)
        async with client:
            scan = await reader.scan("donor.test", "https://donor.test/", [])

        assert len(scan.urls) == 10
        assert scan.truncated is True
        assert scan.complete is False

    async def test_cap_on_files_stops_the_descent(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            number = request.url.path.strip("/").removesuffix(".xml")
            return httpx.Response(200, text=_index(f"https://donor.test/{number}x.xml"))

        reader, client = self._reader(handler, max_files=3)
        async with client:
            scan = await reader.scan("donor.test", "https://donor.test/", [])

        assert scan.files_read == 3
        assert scan.truncated is True

    async def test_dead_children_of_an_index_still_count(self) -> None:
        """Потолок на файлы обязан считать попытки, а не удачи.

        Найдено живым прогоном: индекс настоящего сайта перечислял
        полсотни дочерних карт, и все отвечали 404. Потолок считал
        прочитанные файлы, то есть не считал ни одной, — и обход
        двадцать минут ходил по несуществующим адресам, не открыв
        ни одной страницы.
        """
        children = [f"https://donor.test/dead{n}.xml" for n in range(50)]
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(str(request.url))
            if request.url.path == "/sitemap.xml":
                return httpx.Response(200, text=_index(*children))
            return httpx.Response(404)

        reader, client = self._reader(handler, max_files=5)
        async with client:
            scan = await reader.scan("donor.test", "https://donor.test/", [])

        assert len(asked) <= 5
        assert scan.truncated is True

    async def test_reading_maps_stops_at_the_deadline(self) -> None:
        """Срок на донора обязан покрывать и чтение карт.

        Найдено живым прогоном: сайт просил паузу в десять секунд
        и объявлял девятнадцать карт — три минуты до первой открытой
        страницы, и потолок времени не срабатывал ни разу, потому что
        считался только по обходу страниц.
        """
        asked: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            asked.append(str(request.url))
            return httpx.Response(200, text=_urlset("https://donor.test/a"))

        declared = [f"https://donor.test/map{n}.xml" for n in range(10)]
        reader, client = self._reader(handler)
        async with client:
            scan = await reader.scan(
                "donor.test",
                "https://donor.test/",
                declared,
                deadline=time.monotonic(),  # срок вышел ещё до начала
            )

        assert asked == []
        assert scan.truncated is True

    async def test_map_of_another_site_is_ignored(self) -> None:
        """`notdonor.test` кончается на `donor.test` — и без точной сверки
        карта чужого сайта прошла бы как своя."""
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            return httpx.Response(200, text=_urlset("https://donor.test/a"))

        reader, client = self._reader(handler)
        async with client:
            scan = await reader.scan(
                "donor.test", "https://donor.test/", ["https://notdonor.test/sitemap.xml"]
            )

        assert seen == []
        assert scan.urls == []

    async def test_packed_map_is_unpacked(self) -> None:
        packed = gzip.compress(_urlset("https://donor.test/a").encode())

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=packed, headers={"content-type": "application/gzip"})

        reader, client = self._reader(handler)
        async with client:
            scan = await reader.scan(
                "donor.test", "https://donor.test/", ["https://donor.test/sitemap.xml.gz"]
            )

        assert scan.urls == ["https://donor.test/a"]


class TestIdentity:
    """Как мы представляемся — это не косметика.

    Соблюдать запрет, адресованный нашему имени, и называться при этом
    браузером — несовместимые вещи: сайт, вписавший нас в robots.txt,
    всё равно увидит Chrome. Режим переключается, и оба режима обязаны
    быть настоящими.
    """

    def test_browser_headers_are_the_default(self) -> None:
        """Решение по итогам замера: ходим тем, кого чаще пускают.

        Замер на пяти донорах ниши: своим именем один из пяти закрывается
        целиком, браузером — ни одного. Цена решения записана в каноне:
        запрет, адресованный нашему имени, перестаёт срабатывать.
        """
        cascade = PageCascade(httpx.AsyncClient())

        assert AGENT not in cascade._headers["User-Agent"]

    def test_we_can_still_say_our_name(self) -> None:
        agent = headers_for(True)["User-Agent"]

        assert AGENT in agent
        assert parse_robots(f"User-agent: {AGENT}\nDisallow: /\n", AGENT).allows("https://x/") is (
            False
        )

    def test_browser_mode_says_nothing_about_us(self) -> None:
        assert AGENT not in headers_for(False)["User-Agent"]

    def test_both_modes_keep_the_rest_of_the_headers(self) -> None:
        """Урезанный набор заголовков стоил шести отказов вместо четырёх
        на замере контактной лестницы — имя меняем, остальное нет."""
        ours, browser = headers_for(True), headers_for(False)

        assert set(ours) == set(browser)
        assert ours["Accept-Language"] == browser["Accept-Language"]
