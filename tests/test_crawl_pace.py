"""Срок обхода донора — по паузе сайта (`crawl/pace.py`), без сети.

Сайт с долгой паузой кончался по сроку, не набрав и половины потолка: час на всех доноров
одинаковый. Срок — столько, чтобы при паузе сайта успеть потолок страниц, но не меньше часа
и не больше потолка медленных; продолжение берёт тот же срок.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from types import SimpleNamespace

import httpx
import pytest
from backend.api.crawls import routes
from backend.config import crawl as crawl_cfg
from backend.features.core.domain import StopReason
from backend.features.core.models.access import UserModel
from backend.features.crawl import walk
from backend.features.crawl.limiter import DomainLimiter
from backend.features.crawl.pace import (
    REPLAN_MARGIN,
    REPLAN_MIN_PAGES,
    ResponseClock,
    replan,
    time_budget,
)
from backend.features.crawl.progress import Batch, Checkpoint, Progress
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.walk import DonorCrawler
from tests.test_crawl_walk import HOST, FakeSite, _page

HOUR, SLOW = 3_600.0, 10_800.0


@pytest.mark.parametrize(
    ("pause", "response", "said"),
    [
        # Пауза 6 с: тысяча страниц и двадцать файлов карты — больше часа, но не выше потолка.
        (6.0, 0.05, 1_020 * 6.05),
        # Самая долгая пауза и медленный сайт — потолок медленных.
        (10.0, 2.0, SLOW),
        # Обычный темп укладывается в час — срок общий.
        (1.0, 0.4, HOUR),
    ],
)
def test_the_budget_follows_the_pause_within_bounds(
    pause: float, response: float, said: float
) -> None:
    budget = time_budget(pause=pause, response=response, pages=1_000, base=HOUR, ceiling=SLOW)

    assert budget == pytest.approx(said)
    assert HOUR <= budget <= SLOW


def test_a_ceiling_below_the_base_does_not_cut_the_base() -> None:
    assert time_budget(pause=6.0, response=0.0, pages=1_000, base=HOUR, ceiling=600.0) == HOUR


def test_the_clock_averages_responses_only() -> None:
    clock = ResponseClock()
    assert clock.average == 0.0

    clock.record(0.2)
    clock.record(0.4)

    assert clock.average == pytest.approx(0.3)


class _PausedLimiter(DomainLimiter):
    """Говорит, что пауза сайта 6 с, а ждать не ждёт: тест — про срок, а не про сон."""

    def __init__(self) -> None:
        super().__init__(delay_sec=0.0, max_delay_sec=0.0)

    def set_delay(self, host: str, requested: float | None) -> float:
        super().set_delay(host, 0.0)
        return 6.0 if requested == 6.0 else 0.0


async def _crawl(
    site: FakeSite, progress: Progress | None = None, **caps: float
) -> tuple[CrawlReport, DonorCrawler]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
    crawler = DonorCrawler(
        client,
        limiter=_PausedLimiter(),
        max_seconds=caps.get("max_seconds"),
        max_seconds_slow=caps.get("max_seconds_slow"),
    )
    async with client:
        try:
            return await crawler.crawl(HOST, progress), crawler
        finally:
            await crawler.aclose()


def _paused_site() -> FakeSite:
    return FakeSite(
        {"/": _page("/a"), "/a": _page()}, robots="User-agent: *\nCrawl-delay: 6\nDisallow:\n"
    )


async def test_a_site_asking_for_6_seconds_gets_more_than_an_hour() -> None:
    report, crawler = await _crawl(_paused_site())

    expected = round(1_020 * (6.0 + crawler._clock.average))
    assert report.time_budget_sec == expected
    assert HOUR < expected <= SLOW
    assert report.as_dict()["time_budget_sec"] == expected


async def test_the_pause_extends_the_base_instead_of_stopping_at_it() -> None:
    """Общий срок ноль остановил бы обход сразу; пауза сайта даёт ему срок — до потолка."""
    report, _ = await _crawl(_paused_site(), max_seconds=0.0, max_seconds_slow=HOUR)

    assert report.time_budget_sec == HOUR
    assert report.stop_reason is StopReason.EXHAUSTED


async def test_a_resumed_crawl_keeps_its_budget() -> None:
    """Продолжение не пересчитывает срок: тот, что посчитала первая попытка, а время — с
    прошлых попыток."""
    point = Checkpoint(
        source="links",
        pages=[f"https://{HOST}/"],
        queue=[f"https://{HOST}/a"],
        elapsed_sec=100.0,
        time_budget_sec=50.0,
    )
    site = _paused_site()

    report, _ = await _crawl(site, Progress(flush=_ignore, resume=point))

    assert report.time_budget_sec == 50.0
    assert report.stop_reason is StopReason.TIMEOUT
    assert "/a" not in site.opened


async def _ignore(_batch: object) -> None:
    return None


async def test_the_panel_hears_the_slow_ceiling_from_the_server(
    client: httpx.AsyncClient,
    make_user: Callable[..., Awaitable[UserModel]],
    sign_in: Callable[..., Awaitable[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """«i» панели называет потолок медленных числом сервера, а не копией в экране."""

    async def none() -> None:
        return None

    monkeypatch.setattr(routes, "_workers", none)
    monkeypatch.setattr(crawl_cfg, "MAX_SECONDS_SLOW", 7_200.0)
    await make_user("смотрит@site.com", permissions={"run": False})
    token = await sign_in("смотрит@site.com")

    body = (
        await client.get("/api/crawls/targets", headers={"Authorization": f"Bearer {token}"})
    ).json()

    assert body["max_seconds_slow"] == 7_200.0


# ---------------- срок вышел — по фактическому темпу (`pace.replan`, 29.10.2026) ----------------


@pytest.mark.parametrize(
    ("elapsed", "opened", "said"),
    [
        # Тест-группа: 976 страниц за 88 мин — до тысячи ещё ~2 мин, с запасом.
        (5_280.0, 976, 5_280.0 * 1_000 / 976 * REPLAN_MARGIN),
        # Медленный сайт: темп не успевает и к потолку медленных — продление до потолка.
        (3_600.0, 300, SLOW),
    ],
)
def test_the_budget_follows_the_real_pace(elapsed: float, opened: int, said: float) -> None:
    budget = replan(budget=elapsed, elapsed=elapsed, opened=opened, pages=1_000, ceiling=SLOW)

    assert budget == pytest.approx(said)


@pytest.mark.parametrize(
    ("budget", "opened"),
    [
        # Темп по нескольким страницам не понять — продолжение, у которого срок вышел ещё
        # в прошлой попытке, или сайт, отдающий страницу раз в минуты.
        (50.0, REPLAN_MIN_PAGES - 1),
        # Потолок уже набран — продлевать незачем.
        (50.0, 1_000),
        # Обещанное больше нового расчёта — срок не короче обещанного.
        (9_000.0, 900),
    ],
)
def test_the_budget_is_never_cut(budget: float, opened: int) -> None:
    assert replan(budget=budget, elapsed=100.0, opened=opened, pages=1_000, ceiling=SLOW) == budget


class _Clock:
    """Часы обхода, которые идут только по ответам сайта: тест — про срок, а не про сон."""

    def __init__(self) -> None:
        self.now = time.monotonic()

    def monotonic(self) -> float:
        return self.now


class _SlowingSite(FakeSite):
    """Первые ответы — robots, главная, поиск карты и ленты — быстрые, страницы медленнее:
    как у сайта тест-группы, срок по первым ответам ему мал."""

    def __init__(self, clock: _Clock, pages: int) -> None:
        links = [f"/p{number}" for number in range(pages)]
        super().__init__({"/": _page(*links), **{link: _page() for link in links}}, robots=None)
        self.clock = clock

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.clock.now += 2.0 if request.url.path.startswith("/p") else 0.5
        return super().handler(request)


async def _slowing_crawl(
    monkeypatch: pytest.MonkeyPatch, *, ceiling: float, progress: Progress | None = None
) -> CrawlReport:
    clock = _Clock()
    monkeypatch.setattr(walk, "time", SimpleNamespace(monotonic=clock.monotonic))
    site = _SlowingSite(clock, pages=80)
    client = httpx.AsyncClient(transport=httpx.MockTransport(site.handler))
    crawler = DonorCrawler(
        client,
        limiter=DomainLimiter(delay_sec=0.0, max_delay_sec=0.0),
        max_pages=60,
        max_seconds=60.0,
        max_seconds_slow=ceiling,
    )
    async with client:
        try:
            return await crawler.crawl(HOST, progress)
        finally:
            await crawler.aclose()


async def test_a_site_slower_than_its_first_answers_gets_the_time_to_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Срок по первым ответам — минута, страницы идут по 2 с: на минуте открыто около
    тридцати из шестидесяти. Раньше — «частично» по сроку; теперь срок продлён по темпу,
    и обход доходит до потолка страниц."""
    report = await _slowing_crawl(monkeypatch, ceiling=1_000.0)

    assert report.stop_reason is StopReason.MAX_PAGES
    assert len(report.pages) == 60
    assert report.time_budget_sec is not None
    assert 60.0 < report.time_budget_sec <= 1_000.0
    assert report.as_dict()["time_budget_sec"] == report.time_budget_sec


async def test_the_extension_stops_at_the_slow_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Продление — не дальше потолка медленных: на нём держится и таймаут задачи очереди."""
    report = await _slowing_crawl(monkeypatch, ceiling=90.0)

    assert report.stop_reason is StopReason.TIMEOUT
    assert report.time_budget_sec == 90.0
    assert 30 < len(report.pages) < 60


async def test_the_checkpoint_carries_the_extended_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """Продолжение после выкатки берёт продлённый срок, а не первый: он в чекпоинте."""
    sent: list[Checkpoint] = []

    async def keep(batch: Batch) -> None:
        sent.append(batch.checkpoint)

    report = await _slowing_crawl(monkeypatch, ceiling=1_000.0, progress=Progress(flush=keep))

    assert sent[-1].time_budget_sec == report.time_budget_sec
    assert report.time_budget_sec is not None
    assert report.time_budget_sec > 60.0
