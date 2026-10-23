"""Сначала карта сайта, потом ссылки уже открытых страниц — до потолка.

Сайт с картой короче потолка кончался задолго до него: карта кончилась, а по ссылкам
обход с картой не ходил вовсе. Сайт поддельный (`FakeSite`), без
сети; проверяется порядок обхода, исход, чекпоинт и что файл из карты не запрашивается.
"""

from __future__ import annotations

import httpx
import pytest
from backend.features.core.domain import CrawlOutcome, StopReason
from backend.features.crawl.frontier import Frontier
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.progress import Batch, CrawlInterruptedError, Progress
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.walk import DonorCrawler
from tests.test_crawl_walk import HOST, FakeSite, _page, _urlset

MAP_ROBOTS = f"User-agent: *\nDisallow: /private\nSitemap: https://{HOST}/sitemap.xml\n"


def _url(path: str) -> str:
    return f"https://{HOST}{path}"


def _site() -> FakeSite:
    """Карта на две страницы; со страниц и с главной — ссылки дальше по сайту."""
    return FakeSite(
        {
            "/": _page("/home-1", "/a"),
            "/a": _page("/a-1", "/a-2", "/b", "/private/x", "/doc.pdf"),
            "/b": _page("/b-1", "/a-1"),
            "/home-1": _page("/deep"),
            "/a-1": _page(),
            "/a-2": _page(),
            "/b-1": _page(),
            "/deep": _page(),
        },
        robots=MAP_ROBOTS,
        sitemap=_urlset(_url("/a"), _url("/b")),
    )


async def _crawl(site: FakeSite, progress: Progress | None = None, **caps: int) -> CrawlReport:
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
    crawler = DonorCrawler(
        client,
        limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0),
        max_pages=caps.get("max_pages"),
    )
    async with client:
        try:
            return await crawler.crawl(HOST, progress)
        finally:
            await crawler.aclose()


async def test_the_map_runs_out_and_links_fill_up_to_the_cap() -> None:
    """Карта — первой, потом ссылки в порядке находки: с главной, потом со страниц карты."""
    site = _site()

    report = await _crawl(site, max_pages=6)

    assert site.opened == ["/", "/a", "/b", "/home-1", "/a-1", "/a-2"]
    assert report.stop_reason is StopReason.MAX_PAGES
    assert report.outcome is CrawlOutcome.OK
    assert (report.source, report.pages_from_sitemap, report.pages_from_links) == (
        "sitemap+links",
        2,
        3,
    )
    assert report.as_dict()["source"] == "sitemap+links"


async def test_exhausted_only_when_both_the_map_and_the_links_ran_out() -> None:
    site = _site()

    report = await _crawl(site)

    assert report.stop_reason is StopReason.EXHAUSTED
    assert sorted(site.opened) == ["/", "/a", "/a-1", "/a-2", "/b", "/b-1", "/deep", "/home-1"]
    # Тот же хост, robots и не файл — правила ссылок те же, что без карты; повторов нет.
    assert "/private/x" not in site.opened
    assert "/doc.pdf" not in site.opened
    assert len(site.opened) == len(set(site.opened))
    assert (report.pages_from_sitemap, report.pages_from_links) == (2, 5)


async def test_a_file_from_the_map_is_not_asked_for() -> None:
    """PDF и картинки из карты тратили попытку и паузу: адрес карты проходит тот же фильтр,
    что ссылка."""
    site = FakeSite(
        {"/": _page(), "/a": _page()},
        robots=MAP_ROBOTS,
        sitemap=_urlset(_url("/price.pdf"), _url("/a"), _url("/logo.PNG")),
    )

    report = await _crawl(site)

    assert site.opened == ["/", "/a"]
    assert report.source == "sitemap"
    assert report.health["requests"] == 2


async def test_a_mixed_crawl_resumes_from_its_saved_links() -> None:
    """Остановка в фазе ссылок: в чекпоинте — «карта, потом ссылки» и очередь ссылок;
    продолжение открытое не открывает и доходит до конца по сохранённой очереди."""
    first: list[Batch] = []

    async def keep(batch: Batch) -> None:
        first.append(batch)

    # Три страницы — /a и /b с карты, /home-1 по ссылке, — на четвёртой просят остановиться.
    asked = iter([False] * 3 + [True])
    with pytest.raises(CrawlInterruptedError) as stopped:
        await _crawl(_site(), Progress(flush=keep, every_pages=100, stop=lambda: next(asked)))
    point = stopped.value.checkpoint

    assert point.source == "sitemap+links"
    assert (point.from_map, point.from_links) == (2, 1)
    # Запрет robots.txt проверяется перед запросом, а не при постановке: /private/x — в очереди.
    assert point.queue == [_url(p) for p in ("/a-1", "/a-2", "/private/x", "/b-1", "/deep")]
    assert point.time_budget_sec > 0

    site = _site()
    report = await _crawl(site, Progress(flush=keep, resume=point))

    assert not {"/a", "/b", "/home-1"} & set(site.opened)
    assert report.stop_reason is StopReason.EXHAUSTED
    assert (report.source, report.pages_from_sitemap, report.pages_from_links) == (
        "sitemap+links",
        2,
        5,
    )
    assert len(report.pages) == 8


def test_a_link_already_queued_or_opened_is_not_queued_again() -> None:
    frontier = Frontier.start([_url("/a")], [_url("/x"), _url("/a")], [_url("/")], has_map=True)

    frontier.add([_url("/"), _url("/x"), _url("/y")])

    assert list(frontier.mapped) == [_url("/a")]
    assert list(frontier.linked) == [_url("/x"), _url("/y")]
    assert frontier.pop() == (_url("/a"), False)
    assert frontier.source == "sitemap"
    assert frontier.pop() == (_url("/x"), True)
    assert frontier.source == "sitemap+links"
