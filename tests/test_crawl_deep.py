"""Глубокий обход (`crawl/deep.py`): десять тысяч страниц вместо тысячи, и не больше нескольких разом.

Проверяется то, ради чего он заведён: потолки двигаются вместе, потолок живёт в строке обхода и
переживает выкатку, обход не кончается раньше времени на чужом потолке (карты, попыток, срока),
разом идёт не больше предела, а обычные доноры и обходы человека за глубокими не стоят.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx
import pytest
from backend.api.crawls import routes
from backend.config import crawl as crawl_cfg
from backend.config import storage
from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.crawl import CrawlBacklogModel, CrawlRunModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl import backlog, deep, donor_page
from backend.features.crawl.board import CrawlLine
from backend.features.crawl.launch import launch
from backend.features.crawl.repository import queue_crawl
from backend.features.crawl.sitemap import SitemapReader
from backend.features.crawl.walk import DonorCrawler
from backend.shared.crawl_marks import Held
from backend.shared.queue import CRAWL_JOB_MARGIN_SEC, crawl_job_timeout
from backend.workers import crawl_jobs
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import TEST_DSN, bearer, make_donor
from tests.migration_helpers import columns_down_and_up
from tests.test_crawl_progress import MAP_ROBOTS, _article
from tests.test_crawl_walk import HOST, FakeSite, _page, _urlset
from tests.test_send_race import committed_sessions

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

WHO = "anna@ours.example.test"
DEEP = 10_000


class _Enqueue:
    """Очередь обходов в памяти."""

    def __init__(self) -> None:
        self.queued: list[int] = []

    def __call__(self, run_id: int, job_id: str) -> None:
        self.queued.append(run_id)


def _ids(run_id: int) -> str:
    return f"crawl-{run_id}-test"


async def _priced(session: AsyncSession, host: str) -> None:
    """Принятый годный донор со свежей ценой — цель обхода."""
    domain = await make_donor(session, host)
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    donor.last_price = Decimal("100.00")
    donor.last_price_at = datetime.now(UTC) - timedelta(days=1)


async def _waiting(factory: async_sessionmaker[AsyncSession]) -> list[tuple[str, int | None]]:
    async with factory() as session:
        rows = await session.execute(
            select(CrawlBacklogModel.host, CrawlBacklogModel.max_pages).order_by(
                CrawlBacklogModel.id
            )
        )
        return [(host, pages) for host, pages in rows.tuples().all()]


async def _runs(factory: async_sessionmaker[AsyncSession]) -> dict[str, int | None]:
    async with factory() as session:
        rows = await session.execute(select(CrawlRunModel.host, CrawlRunModel.max_pages))
        return dict(rows.tuples().all())


# --- потолки ----------------------------------------------------------------------------------


def test_a_usual_crawl_keeps_the_settings_it_always_had() -> None:
    found = deep.ceilings_of(None)

    assert (found.pages, found.attempts) == (
        crawl_cfg.MAX_PAGES_PER_DONOR,
        crawl_cfg.MAX_ATTEMPTS_PER_DONOR,
    )
    assert found.seconds == crawl_cfg.MAX_SECONDS_SLOW
    assert (found.sitemap_files, found.sitemap_urls) == (
        crawl_cfg.MAX_SITEMAP_FILES,
        crawl_cfg.MAX_SITEMAP_URLS,
    )


def test_every_ceiling_of_a_deep_crawl_leaves_room_for_its_pages() -> None:
    """Кнопка обещает десять тысяч: чужой потолок не должен кончиться раньше страниц."""
    found = deep.ceilings_of(crawl_cfg.MAX_PAGES_DEEP)

    assert found.pages == DEEP
    assert found.attempts >= found.pages * 2  # то же правило, что у обычного: вдвое больше
    assert found.sitemap_urls >= found.attempts  # адресов карты хватает на все попытки
    assert found.sitemap_files > crawl_cfg.MAX_SITEMAP_FILES
    assert found.seconds > crawl_cfg.MAX_SECONDS_SLOW
    # 1,4 с на страницу с боевого обхода: десять тысяч обязаны влезать в срок.
    assert found.pages * 1.4 < found.seconds


def test_a_ceiling_raised_above_the_setting_keeps_two_attempts_a_page() -> None:
    assert deep.ceilings_of(30_000).attempts == 60_000


def test_a_deep_crawl_sends_batches_rarely_because_its_checkpoint_is_long() -> None:
    """Чекпоинт пишется целиком: при тысяче страниц редкие пачки не нужны, при десяти — нужны."""
    usual, long = deep.ceilings_of(None), deep.ceilings_of(DEEP)

    assert long.batch_pages > usual.batch_pages
    assert long.batch_sec > usual.batch_sec


def test_the_queue_timeout_covers_the_longest_crawl() -> None:
    """Таймаут очереди ниже срока убил бы глубокий обход посреди страницы."""
    assert crawl_job_timeout() >= crawl_cfg.MAX_SECONDS_DEEP + CRAWL_JOB_MARGIN_SEC


async def test_the_crawler_passes_its_map_ceilings_to_the_map(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[int | None, int | None]] = []
    built = SitemapReader.__init__

    def spy(self: SitemapReader, *args: Any, **kwargs: Any) -> None:
        seen.append((kwargs.get("max_files"), kwargs.get("max_urls")))
        built(self, *args, **kwargs)

    monkeypatch.setattr(SitemapReader, "__init__", spy)
    monkeypatch.setattr(crawl_cfg, "DELAY_SEC", 0.0)
    site = FakeSite({"/": _page("https://home-adv.example/")})
    async with httpx.AsyncClient(transport=httpx.MockTransport(site.handler)) as client:
        await DonorCrawler(client, sitemap_ceilings=(7, 70)).crawl(HOST)

    assert seen == [(7, 70)]


# --- задача обхода ----------------------------------------------------------------------------


@pytest.fixture
def _wiring(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(crawl_cfg, "DELAY_SEC", 0.0)
    monkeypatch.setattr(crawl_cfg, "BROWSER_ENABLED", False)
    monkeypatch.setattr(crawl_jobs, "_judge_later", lambda _run: None)
    crawl_jobs._STOP.clear()
    yield
    crawl_jobs._STOP.clear()


def _site(monkeypatch: pytest.MonkeyPatch, count: int) -> FakeSite:
    pages = {f"/p{n}": _article(f"https://adv{n}.example/offer") for n in range(count)}
    pages["/"] = _page("https://home-adv.example/")
    site = FakeSite(
        pages, robots=MAP_ROBOTS, sitemap=_urlset(*[f"https://{HOST}/p{n}" for n in range(count)])
    )

    def client(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(site.handler))

    monkeypatch.setattr(crawl_jobs, "guarded_client", client)
    return site


async def _queued(factory: async_sessionmaker[AsyncSession], max_pages: int | None) -> int:
    async with factory() as session:
        run = await queue_crawl(session, HOST, by="op@t.test", max_pages=max_pages)
        run.job_id = "first"
        await session.commit()
        return run.id


async def _row(factory: async_sessionmaker[AsyncSession], run_id: int) -> CrawlRunModel:
    async with factory() as session:
        run = await session.get(CrawlRunModel, run_id)
        assert run is not None
        return run


@pytest.mark.usefixtures("_wiring")
async def test_a_deep_crawl_goes_past_the_usual_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawl_cfg, "MAX_PAGES_PER_DONOR", 3)
    _site(monkeypatch, 8)
    async with committed_sessions() as factory:
        run_id = await _queued(factory, 6)

        done = await crawl_jobs._crawl(run_id)
        run = await _row(factory, run_id)

    assert done["pages"] == 6
    assert (run.status, run.stop_reason) == (CrawlStatus.DONE, StopReason.MAX_PAGES)


@pytest.mark.usefixtures("_wiring")
async def test_a_usual_crawl_stays_under_the_usual_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawl_cfg, "MAX_PAGES_PER_DONOR", 3)
    _site(monkeypatch, 8)
    async with committed_sessions() as factory:
        run_id = await _queued(factory, None)

        done = await crawl_jobs._crawl(run_id)

    assert done["pages"] == 3


@pytest.mark.usefixtures("_wiring")
async def test_a_resumed_deep_crawl_keeps_its_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    """Выкатка посреди глубокого обхода: потолок в строке, продолжение не становится обычным."""
    monkeypatch.setattr(crawl_cfg, "MAX_PAGES_PER_DONOR", 3)
    monkeypatch.setattr(crawl_jobs, "enqueue_crawl", lambda run_id, job_id=None: job_id or "x")
    _site(monkeypatch, 8)
    async with committed_sessions() as factory:
        run_id = await _queued(factory, 6)
        crawl_jobs.ask_to_stop()

        first = await crawl_jobs._crawl(run_id)
        crawl_jobs._STOP.clear()
        second = await crawl_jobs._crawl(run_id)
        run = await _row(factory, run_id)

    assert first["interrupted"] is True
    assert second["pages"] == 6
    assert run.max_pages == 6


@pytest.mark.usefixtures("_wiring")
async def test_a_hung_crawl_ends_by_its_own_limit_and_not_by_the_longest_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Таймаут очереди вырос до суток ради глубокого обхода; обычному свой предел держит задача."""

    async def hangs(self: DonorCrawler, host: str, progress: object = None) -> None:
        await asyncio.sleep(60)

    monkeypatch.setattr(DonorCrawler, "crawl", hangs)
    monkeypatch.setattr(crawl_jobs, "CRAWL_JOB_MARGIN_SEC", 0.05)
    monkeypatch.setattr(crawl_cfg, "MAX_SECONDS_PER_DONOR", 0.05)
    monkeypatch.setattr(crawl_cfg, "MAX_SECONDS_SLOW", 0.05)
    _site(monkeypatch, 1)
    async with committed_sessions() as factory:
        run_id = await _queued(factory, None)

        began = time.monotonic()
        with pytest.raises(TimeoutError):
            await crawl_jobs._crawl(run_id)
        spent = time.monotonic() - began

        run = await _row(factory, run_id)

    assert spent < 2.0  # предел по сроку этого обхода, а не вшитый
    assert run.status is CrawlStatus.RUNNING  # разбор мёртвых продолжит с чекпоинта
    assert run.stats is not None
    assert "будет продолжен" in run.stats["reason"]


# --- очередь загрузки: предел разом -----------------------------------------------------------


async def test_a_deep_row_remembers_its_ceiling_and_a_usual_row_becomes_deep(
    session: AsyncSession,
) -> None:
    assert await backlog.add(session, ["one.example"], by=WHO, author_id=None) == 1
    assert await backlog.add(session, ["one.example"], by=WHO, author_id=None) == 0

    assert await backlog.add(session, ["one.example"], by=WHO, author_id=None, max_pages=DEEP) == 1
    assert await backlog.add(session, ["one.example"], by=WHO, author_id=None, max_pages=DEEP) == 0

    row = await session.scalar(select(CrawlBacklogModel))
    assert row is not None
    assert row.max_pages == DEEP
    assert await deep.waiting(session) == 1


async def test_a_usual_load_does_not_take_the_ceiling_off_a_deep_row(session: AsyncSession) -> None:
    await backlog.add(session, ["one.example"], by=WHO, author_id=None, max_pages=DEEP)

    await backlog.add(session, ["one.example"], by=WHO, author_id=None)

    assert await deep.waiting(session) == 1


async def test_the_pass_gives_a_deep_crawl_its_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "deep.example")
            await backlog.add(session, ["deep.example"], by=WHO, author_id=None, max_pages=DEEP)
            await session.commit()
        async with factory() as session:
            await backlog.feed(session, workers=8, enqueue=_Enqueue(), job_id=_ids)
            await session.commit()

        assert await _runs(factory) == {"deep.example": DEEP}


async def test_no_more_deep_crawls_go_than_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawl_cfg, "DEEP_MAX_RUNNING", 2)
    hosts = [f"deep{n}.example" for n in range(4)]
    async with committed_sessions() as factory:
        async with factory() as session:
            for host in hosts:
                await _priced(session, host)
            session.add(
                CrawlRunModel(host="going.example", status=CrawlStatus.RUNNING, max_pages=DEEP)
            )
            await backlog.add(session, hosts, by=WHO, author_id=None, max_pages=DEEP)
            await session.commit()
        async with factory() as session:
            fed = await backlog.feed(session, workers=8, enqueue=_Enqueue(), job_id=_ids)
            await session.commit()

        assert list(fed.queued) == ["deep0.example"]  # один уже идёт, предел — два
        assert [host for host, _ in await _waiting(factory)] == hosts[1:]


async def test_usual_donors_do_not_wait_behind_deep_ones(monkeypatch: pytest.MonkeyPatch) -> None:
    """Предел выбран, а глубоких в очереди тысячи: обычный донор за ними не стоит. `SCAN` здесь
    меньше числа глубоких: строка обычного донора иначе не попала бы в проход вовсе."""
    monkeypatch.setattr(crawl_cfg, "DEEP_MAX_RUNNING", 1)
    monkeypatch.setattr(backlog, "SCAN", 2)
    deep_hosts = [f"deep{n}.example" for n in range(3)]
    async with committed_sessions() as factory:
        async with factory() as session:
            for host in [*deep_hosts, "usual.example"]:
                await _priced(session, host)
            session.add(
                CrawlRunModel(host="going.example", status=CrawlStatus.RUNNING, max_pages=DEEP)
            )
            await backlog.add(session, deep_hosts, by=WHO, author_id=None, max_pages=DEEP)
            await backlog.add(session, ["usual.example"], by=WHO, author_id=None)
            await session.commit()
        async with factory() as session:
            fed = await backlog.feed(session, workers=8, enqueue=_Enqueue(), job_id=_ids)
            await session.commit()

        assert list(fed.queued) == ["usual.example"]


async def test_the_pass_takes_deep_ones_only_while_room_is_left(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Предел — один, мест у обходчиков два: второе достаётся обычному, а не второму глубокому."""
    monkeypatch.setattr(crawl_cfg, "DEEP_MAX_RUNNING", 1)
    async with committed_sessions() as factory:
        async with factory() as session:
            for host in ("deep0.example", "deep1.example", "usual.example"):
                await _priced(session, host)
            await backlog.add(
                session,
                ["deep0.example", "deep1.example"],
                by=WHO,
                author_id=None,
                max_pages=DEEP,
            )
            await backlog.add(session, ["usual.example"], by=WHO, author_id=None)
            await session.commit()
        async with factory() as session:
            fed = await backlog.feed(session, workers=8, enqueue=_Enqueue(), job_id=_ids)
            await session.commit()

        assert sorted(fed.queued) == ["deep0.example", "usual.example"]
        assert await _waiting(factory) == [("deep1.example", DEEP)]


async def test_a_deep_row_the_queue_refused_comes_back_deep() -> None:
    class _Broken(_Enqueue):
        def __call__(self, run_id: int, job_id: str) -> None:
            raise ConnectionError("Redis недоступен")

    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "deep.example")
            await backlog.add(session, ["deep.example"], by=WHO, author_id=None, max_pages=DEEP)
            await session.commit()
        async with factory() as session:
            await backlog.feed(session, workers=8, enqueue=_Broken(), job_id=_ids)
            await session.commit()

        assert await _waiting(factory) == [("deep.example", DEEP)]


# --- строка обхода и сроки --------------------------------------------------------------------


async def test_launch_writes_the_ceiling_and_the_line_shows_it(session: AsyncSession) -> None:
    await launch(session, ["deep.example"], by=WHO, job_id=_ids, enqueue=_Enqueue(), max_pages=DEEP)

    run = await session.scalar(select(CrawlRunModel))
    assert run is not None
    assert CrawlLine.of(run).max_pages == DEEP


async def _finished(
    session: AsyncSession, host: str, seconds: float, *, max_pages: int | None = None
) -> None:
    session.add(
        CrawlRunModel(
            host=host,
            status=CrawlStatus.DONE,
            outcome=CrawlOutcome.OK,
            pages_opened=900,
            max_pages=max_pages,
            stats={"elapsed_sec": seconds},
        )
    )
    await session.flush()


async def test_a_deep_crawl_is_judged_by_its_own_measure_when_it_has_one(
    session: AsyncSession,
) -> None:
    await _finished(session, "usual.example", 1_000.0)
    for number, seconds in enumerate((8_000.0, 9_000.0, 10_000.0)):
        await _finished(session, f"deep{number}.example", seconds, max_pages=DEEP)

    assert await deep.typical_deep_seconds(session) == 9_000.0


async def test_without_a_measure_a_deep_crawl_is_the_usual_one_stretched(
    session: AsyncSession,
) -> None:
    await _finished(session, "usual.example", 1_000.0)

    assert await deep.typical_deep_seconds(session) == 10_000.0


async def test_the_stretched_estimate_stays_within_the_ceiling(session: AsyncSession) -> None:
    await _finished(session, "slow.example", 10_000.0)

    assert await deep.typical_deep_seconds(session) == crawl_cfg.MAX_SECONDS_DEEP


async def test_deep_crawls_do_not_spoil_the_usual_measure(session: AsyncSession) -> None:
    await _finished(session, "usual.example", 1_000.0)
    await _finished(session, "deep.example", 50_000.0, max_pages=DEEP)

    assert await donor_page.typical_seconds(session) == 1_000.0


async def test_the_term_of_the_backlog_counts_the_few_deep_slots(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сорок глубоких при четырёх разом идут десять сроков, сколько бы обходчиков ни было."""
    monkeypatch.setattr(crawl_cfg, "DEEP_MAX_RUNNING", 4)
    await _finished(session, "usual.example", 1_000.0)

    assert await backlog.eta_sec(session, 40, 16, deep_rows=40) == 100_000.0


async def test_the_term_of_the_backlog_with_both_kinds_is_the_longer_one(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crawl_cfg, "DEEP_MAX_RUNNING", 4)
    session.add(
        CrawlRunModel(
            host="usual.example",
            status=CrawlStatus.DONE,
            outcome=CrawlOutcome.OK,
            pages_opened=900,
            stats={"elapsed_sec": 1_000.0},
        )
    )
    await session.flush()

    # 1 глубокий: 10 000 с на четырёх; 1 200 обычных: 1 200 × 1 000 / 12 = 100 000 с.
    assert await backlog.eta_sec(session, 1_201, 16, deep_rows=1) == 100_000.0


async def test_the_busy_are_named_before_the_row_is_lost(session: AsyncSession) -> None:
    session.add(CrawlRunModel(host="going.example", status=CrawlStatus.RUNNING))
    session.add(CrawlRunModel(host="waits.example", status=CrawlStatus.QUEUED))
    session.add(
        CrawlRunModel(host="done.example", status=CrawlStatus.DONE, outcome=CrawlOutcome.OK)
    )
    await session.flush()

    assert await deep.busy_hosts(
        session, ["going.example", "waits.example", "done.example", "new.example"]
    ) == ["going.example", "waits.example"]
    assert await deep.busy_hosts(session, []) == []


# --- маршруты ---------------------------------------------------------------------------------


@pytest.fixture
def _routes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "workers_alive", lambda **_: 8)
    monkeypatch.setattr(routes, "job_positions", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(routes, "crawlers_held", lambda **_: Held(0, None))


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.mark.usefixtures("_routes")
async def test_the_button_of_one_donor_waits_in_the_backlog_and_says_why(
    client: AsyncClient, session: AsyncSession, operator: str
) -> None:
    await _priced(session, "deep.example")
    await session.commit()

    answer = await client.post(
        "/api/crawls/deep", json={"hosts": ["www.Deep.example"]}, headers=bearer(operator)
    )

    assert answer.status_code == 200, answer.text
    body = answer.json()
    assert (body["asked"], body["backlog"], body["busy"], body["refused"]) == (1, 1, [], {})
    assert body["rules"]["max_pages"] == crawl_cfg.MAX_PAGES_DEEP
    assert body["rules"]["max_running"] == crawl_cfg.DEEP_MAX_RUNNING
    assert body["rules"]["waiting"] == 1
    row = await session.scalar(select(CrawlBacklogModel))
    assert row is not None
    assert (row.host, row.max_pages) == ("deep.example", crawl_cfg.MAX_PAGES_DEEP)


@pytest.mark.usefixtures("_routes")
async def test_who_may_not_be_crawled_is_refused_in_words(
    client: AsyncClient, session: AsyncSession, operator: str
) -> None:
    await make_donor(session, "unpriced.example")
    await _priced(session, "going.example")
    session.add(CrawlRunModel(host="going.example", status=CrawlStatus.RUNNING))
    await session.commit()

    answer = await client.post(
        "/api/crawls/deep",
        json={"hosts": ["unpriced.example", "going.example"]},
        headers=bearer(operator),
    )

    body = answer.json()
    assert body["backlog"] == 0
    assert body["busy"] == ["going.example"]
    assert "цены нет" in body["refused"]["unpriced.example"]
    assert await session.scalar(select(CrawlBacklogModel)) is None


@pytest.mark.usefixtures("_routes")
async def test_the_viewer_may_not_start_a_deep_crawl(
    client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    await make_user("смотрит@site.com", permissions={"run": False})
    token = await sign_in("смотрит@site.com")

    answer = await client.post(
        "/api/crawls/deep", json={"hosts": ["deep.example"]}, headers=bearer(token)
    )

    assert answer.status_code == 403


@pytest.mark.usefixtures("_routes")
async def test_all_by_filter_goes_to_the_backlog_however_few_they_are(
    client: AsyncClient, session: AsyncSession, operator: str
) -> None:
    await _priced(session, "deep.example")
    await session.commit()

    answer = await client.post(
        "/api/crawls/donors/crawl",
        json={"expected": 5, "deep": True},
        headers=bearer(operator),
    )

    body = answer.json()
    assert (body["asked"], body["deferred"], body["backlog"]) == (1, True, 1)
    assert await session.scalar(select(CrawlRunModel)) is None  # выдача держит предел
    row = await session.scalar(select(CrawlBacklogModel))
    assert row is not None
    assert row.max_pages == crawl_cfg.MAX_PAGES_DEEP


@pytest.mark.usefixtures("_routes")
async def test_the_tab_gets_the_numbers_of_the_confirmation(
    client: AsyncClient, session: AsyncSession, operator: str
) -> None:
    session.add(CrawlRunModel(host="going.example", status=CrawlStatus.RUNNING, max_pages=DEEP))
    await backlog.add(session, ["waits.example"], by=WHO, author_id=None, max_pages=DEEP)
    await session.commit()

    answer = await client.get("/api/crawls/donors", headers=bearer(operator))

    rules = answer.json()["deep"]
    assert (rules["going"], rules["waiting"]) == (1, 1)
    assert rules["max_seconds"] == crawl_cfg.MAX_SECONDS_DEEP
    assert rules["typical_sec"] > 0


@pytest.mark.usefixtures("_routes")
async def test_a_deep_crawl_shows_its_own_ceiling_and_is_marked(
    client: AsyncClient, session: AsyncSession, operator: str
) -> None:
    session.add(CrawlRunModel(host="deep.example", status=CrawlStatus.RUNNING, max_pages=DEEP))
    session.add(CrawlRunModel(host="usual.example", status=CrawlStatus.RUNNING))
    await session.commit()

    answer = await client.get("/api/crawls", headers=bearer(operator))

    rows = {row["host"]: row for row in answer.json()["rows"]}
    assert (rows["deep.example"]["max_pages"], rows["deep.example"]["deep"]) == (DEEP, True)
    assert rows["usual.example"]["max_pages"] == crawl_cfg.MAX_PAGES_PER_DONOR
    assert rows["usual.example"]["deep"] is False


# --- ревизия ----------------------------------------------------------------------------------


def _both_tables(connection: Connection) -> tuple[set[str], set[str]]:
    down, up = columns_down_and_up(
        connection, "7a1e6d40c2f9_crawl_deep_pages.py", "crawl_runs", {"max_pages"}
    )
    return down, up


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    assert await connection.run_sync(_both_tables) == (set(), {"max_pages"})


def _on_backlog(connection: Connection) -> tuple[set[str], set[str]]:
    return columns_down_and_up(
        connection, "7a1e6d40c2f9_crawl_deep_pages.py", "crawl_backlog", {"max_pages"}
    )


async def test_the_backlog_gets_the_column_too(session: AsyncSession) -> None:
    connection = await session.connection()

    assert await connection.run_sync(_on_backlog) == (set(), {"max_pages"})
