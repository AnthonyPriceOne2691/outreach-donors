"""Долгий обход: пачки по ходу, остановка на границе страницы, продолжение.

Сайт поддельный (`FakeSite` из тестов обхода), порядок настоящий. Главное,
что здесь проверяется, по зелёному обходу не видно: что пачки вместе
дают ровно то, что нашёл обход, что остановка не теряет ни одной ссылки
и что продолжение не открывает открытое и не считает его второй раз.
"""

from __future__ import annotations

import logging

import httpx
import pytest
from backend.features.core.domain import CrawlOutcome, StopReason
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.progress import Batch, Checkpoint, CrawlInterruptedError, Progress
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.walk import DonorCrawler
from tests.test_crawl_walk import HOST, FakeSite, _page, _urlset

MAP_ROBOTS = f"User-agent: *\nDisallow:\nSitemap: https://{HOST}/sitemap.xml\n"


def _article(*links: str) -> str:
    """Страница, которую обход признаёт статьёй: счётчик статей — тоже чекпоинт."""
    body = "Текст статьи про кредиты и вклады. " * 20
    anchors = "".join(f'<a href="{href}">ссылка</a>' for href in links)
    return f"<html><body><article><p>{body}</p><p>{anchors}</p></article></body></html>"


def _site(count: int = 6) -> FakeSite:
    """Донор с картой на `count` статей, у каждой — своя внешняя ссылка."""
    pages = {f"/p{n}": _article(f"https://adv{n}.example/offer") for n in range(count)}
    pages["/"] = _page("https://home-adv.example/")
    return FakeSite(
        pages,
        robots=MAP_ROBOTS,
        sitemap=_urlset(*[f"https://{HOST}/p{n}" for n in range(count)]),
    )


class _Sink:
    """Куда уходят пачки — с памятью, чтобы сверить их с отчётом."""

    def __init__(self) -> None:
        self.batches: list[Batch] = []

    async def __call__(self, batch: Batch) -> None:
        self.batches.append(batch)

    @property
    def links(self) -> list[str]:
        return [link.url for batch in self.batches for link in batch.links]


async def _crawl(site: FakeSite, progress: Progress | None, **kwargs: object) -> CrawlReport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
    crawler = DonorCrawler(
        client,
        limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0),
        **kwargs,  # type: ignore[arg-type]
    )
    async with client:
        try:
            return await crawler.crawl(HOST, progress)
        finally:
            await crawler.aclose()


class TestBatches:
    async def test_batches_add_up_to_what_the_crawl_found(self) -> None:
        sink = _Sink()

        report = await _crawl(_site(), Progress(flush=sink, every_pages=2))

        assert len(sink.batches) >= 3
        assert sorted(sink.links) == sorted(link.url for link in report.links)
        assert len(sink.links) == len(set(sink.links)), "ссылка ушла в две пачки"
        assert sink.batches[-1].checkpoint.pages == report.pages
        assert sink.batches[-1].checkpoint.articles == report.articles

    async def test_without_a_remote_everything_stays_for_the_end(self) -> None:
        """Консольный замер пульта не передаёт — и обход идёт как прежде."""
        report = await _crawl(_site(), None)

        assert report.outcome is CrawlOutcome.OK
        assert len(report.links) == 7

    async def test_slow_pages_are_batched_by_time(self) -> None:
        sink = _Sink()

        await _crawl(_site(3), Progress(flush=sink, every_pages=1_000, every_sec=0.0))

        assert len(sink.batches) >= 3


class TestStop:
    async def test_stop_writes_the_batch_and_leaves_a_checkpoint(self) -> None:
        sink = _Sink()
        site = _site()
        opened = len(site.opened)
        asked = iter([False, False, True])

        with pytest.raises(CrawlInterruptedError) as stopped:
            await _crawl(site, Progress(flush=sink, every_pages=100, stop=lambda: next(asked)))

        point = stopped.value.checkpoint
        assert point.source == "sitemap"
        # Главная и две статьи открыты — и ровно их ссылки записаны.
        assert len(point.pages) == 3
        assert len(site.opened) - opened == 3
        assert sorted(sink.links) == [
            "https://adv0.example/offer",
            "https://adv1.example/offer",
            "https://home-adv.example/",
        ]
        assert sink.batches[-1].checkpoint == point
        assert "продолжится" in str(stopped.value)


class TestResume:
    async def test_resume_skips_what_was_opened_and_keeps_counting(self) -> None:
        first = _Sink()
        asked = iter([False, False, True])
        with pytest.raises(CrawlInterruptedError) as stopped:
            await _crawl(_site(), Progress(flush=first, every_pages=100, stop=lambda: next(asked)))
        point = stopped.value.checkpoint

        site = _site()
        second = _Sink()
        report = await _crawl(site, Progress(flush=second, resume=point))

        # Вторая попытка не открывает ни главную для ссылок, ни открытые статьи.
        assert "/p0" not in site.opened
        assert "/p1" not in site.opened
        assert set(first.links).isdisjoint(second.links)
        assert len(first.links) + len(second.links) == 7
        assert len(report.pages) == 7
        # Статьи — шесть (главная не статья): две из первой попытки плюс четыре.
        assert point.articles == 2
        assert report.articles == 6
        assert report.outcome is CrawlOutcome.OK
        assert report.stop_reason is StopReason.EXHAUSTED

    async def test_time_cap_counts_the_earlier_attempts(self) -> None:
        point = Checkpoint(
            source="links",
            pages=[f"https://{HOST}/"],
            queue=[f"https://{HOST}/p0"],
            elapsed_sec=10_000.0,
        )
        site = _site()

        report = await _crawl(site, Progress(flush=_Sink(), resume=point))

        assert report.stop_reason is StopReason.TIMEOUT
        assert report.outcome is CrawlOutcome.PARTIAL
        assert "/p0" not in site.opened

    async def test_site_gone_on_resume_keeps_what_was_crawled(self) -> None:
        """Сайт лёг между попытками: обход не «не начат», а частичный —
        открытое до того в базе и в отчёте."""
        point = Checkpoint(source="sitemap", pages=[f"https://{HOST}/", f"https://{HOST}/p0"])
        site = FakeSite({}, robots=MAP_ROBOTS, status=503)

        report = await _crawl(site, Progress(flush=_Sink(), resume=point))

        assert report.outcome is CrawlOutcome.PARTIAL
        assert report.stop_reason is StopReason.NO_START
        assert len(report.pages) == 2

    async def test_unreadable_robots_on_resume_keeps_what_was_crawled(self) -> None:
        point = Checkpoint(source="sitemap", pages=[f"https://{HOST}/"])
        site = FakeSite({"/": _page()}, robots="__error__")

        report = await _crawl(site, Progress(flush=_Sink(), resume=point))

        assert report.outcome is CrawlOutcome.PARTIAL
        assert report.stop_reason is StopReason.ROBOTS

    async def test_link_walk_resumes_from_its_saved_queue(self) -> None:
        """Без карты очередь собирается по ссылкам — её не собрать заново,
        не обойдя сайт второй раз, поэтому она едет в чекпоинте."""
        pages = {"/": _page("/a"), "/a": _page("/b"), "/b": _article("https://adv.example/")}
        point = Checkpoint(
            source="links",
            pages=[f"https://{HOST}/", f"https://{HOST}/a"],
            queue=[f"https://{HOST}/b"],
            articles=2,
        )
        site = FakeSite(pages)
        sink = _Sink()

        report = await _crawl(site, Progress(flush=sink, resume=point))

        assert site.opened.count("/a") == 0
        assert sink.links == ["https://adv.example/"]
        assert report.source == "links"
        assert report.articles == 3


class TestCheckpointColumn:
    def test_round_trip(self) -> None:
        point = Checkpoint(source="links", pages=["a"], queue=["b"], attempts=3, slowed=True)

        assert Checkpoint.of(point.as_dict()) == point

    def test_empty_means_start_over(self) -> None:
        assert Checkpoint.of(None) is None
        assert Checkpoint.of({}) is None

    def test_spoiled_means_start_over_and_says_so(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            assert Checkpoint.of({"pages": ["a"]}) is None

        assert "чекпоинт не прочитан" in caplog.text

    def test_unknown_keys_from_a_newer_version_are_ignored(self) -> None:
        point = Checkpoint.of({"source": "sitemap", "pages": ["a"], "later_field": 1})

        assert point == Checkpoint(source="sitemap", pages=["a"])
