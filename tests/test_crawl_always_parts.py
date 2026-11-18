"""Части обхода 24/7 в задаче обходчика (`crawl/always_part.py`): цепочка, ночь, пауза, конец сайта.

Задачи открывают свою базу — настоящие фиксации и чистка после (`committed_sessions`), как у задач
обхода. Сайт поддельный (`FakeSite`), порядок обхода настоящий. Потолки уменьшены: первая часть — две
страницы, «первые 10 000» — четыре. Проверяется то, ради чего части так устроены: открытое прежними
частями второй раз не открывается; ночь и пауза кончают часть штатно, а не продолжением, и следующая
часть идёт с того же места; выкатка посреди части продолжает её, а строка части не пишет страниц
цепочки; сайт кончился — это видно; чекпоинт цепочки хранится сжатым и ограниченным.
"""

from __future__ import annotations

import asyncio
import zlib
from collections.abc import Iterator
from datetime import datetime
from typing import Any

import httpx
import pytest
from backend.config import crawl as crawl_cfg
from backend.config import crawl_always as always_cfg
from backend.config import storage
from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlDepthModel, CrawlRunModel
from backend.features.crawl import always, always_depth, always_part, always_switch
from backend.features.crawl.always import ALWAYS_BY
from backend.features.crawl.always_part import CHAIN_FROM, CHAIN_TO, Part
from backend.features.crawl.progress import Checkpoint
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.repository import queue_crawl
from backend.workers import crawl_jobs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import TEST_DSN
from tests.test_crawl_jobs import _Queue
from tests.test_crawl_progress import MAP_ROBOTS, _article
from tests.test_crawl_walk import HOST, FakeSite, _page, _urlset
from tests.test_send_race import committed_sessions

#: Страниц у поддельного сайта, кроме главной.
SIZE = 10


@pytest.fixture(autouse=True)
def _wiring(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(crawl_cfg, "DELAY_SEC", 0.0)
    monkeypatch.setattr(crawl_cfg, "BROWSER_ENABLED", False)
    monkeypatch.setattr(crawl_cfg, "MAX_PAGES_PER_DONOR", 2)
    monkeypatch.setattr(crawl_cfg, "MAX_PAGES_DEEP", 4)
    monkeypatch.setattr(crawl_cfg, "NIGHT_ROUND_ENABLED", False)
    monkeypatch.setattr(always_cfg, "ENABLED", True)
    monkeypatch.setattr(crawl_jobs, "quick_queue", _Queue)
    crawl_jobs._STOP.clear()
    yield
    crawl_jobs._STOP.clear()


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> FakeSite:
    """Сайт на `SIZE` статей с картой, у каждой — своя внешняя ссылка."""
    pages = {f"/p{n}": _article(f"https://adv{n}.example/offer") for n in range(SIZE)}
    pages["/"] = _page("https://home-adv.example/")
    found = FakeSite(
        pages, robots=MAP_ROBOTS, sitemap=_urlset(*[f"https://{HOST}/p{n}" for n in range(SIZE)])
    )

    def client(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(found.handler))

    monkeypatch.setattr(crawl_jobs, "guarded_client", client)
    return found


#: Адреса карты, уводящие навсегда на уже открытую страницу (как у донора 17–18.11.2026).
REDIRECTS = {"/r0": "/p0", "/r1": "/p0"}


@pytest.fixture
def redirecting(monkeypatch: pytest.MonkeyPatch) -> FakeSite:
    """Сайт на три статьи, в карте которого между ними — два адреса, уводящие на первую."""
    pages = {f"/p{n}": _article(f"https://adv{n}.example/offer") for n in range(3)}
    pages["/"] = _page("https://home-adv.example/")
    order = ("/p0", "/p1", "/r0", "/r1", "/p2")
    found = FakeSite(
        pages,
        robots=MAP_ROBOTS,
        sitemap=_urlset(*[f"https://{HOST}{path}" for path in order]),
        redirects=REDIRECTS,
    )

    def client(**_: Any) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(found.handler))

    monkeypatch.setattr(crawl_jobs, "guarded_client", client)
    return found


@pytest.fixture
def continued(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Продолжения, которые задача ставит себе сама (выкатка)."""
    calls: list[int] = []

    def enqueue(run_id: int, job_id: str | None = None) -> str:
        calls.append(run_id)
        return job_id or "x"

    monkeypatch.setattr(crawl_jobs, "enqueue_crawl", enqueue)
    return calls


async def _switch(factory: async_sessionmaker[AsyncSession], *, on: bool) -> None:
    """Пуск или пауза обхода 24/7 — строкой истории, как с экрана."""
    async with factory() as session:
        await always_switch.set_switch(session, by=None, enabled=on)
        await session.commit()


async def _part(
    factory: async_sessionmaker[AsyncSession], number: int, max_pages: int | None
) -> dict[str, Any]:
    """Часть цепочки — строкой, как её ставит обход 24/7, и задачей обходчика."""
    async with factory() as session:
        run = await queue_crawl(session, HOST, by=ALWAYS_BY, max_pages=max_pages, part=number)
        run.job_id = f"part-{number}"
        await session.commit()
        run_id = run.id
    done = await crawl_jobs._crawl(run_id)
    return {**done, "crawl": run_id}


async def _run(factory: async_sessionmaker[AsyncSession], run_id: int) -> CrawlRunModel:
    async with factory() as session:
        run = await session.get(CrawlRunModel, run_id)
        assert run is not None
        return run


async def _depth(factory: async_sessionmaker[AsyncSession]) -> CrawlDepthModel:
    async with factory() as session:
        row = await always_depth.row_of(session, HOST)
        assert row is not None
        return row


def _chain(row: CrawlDepthModel) -> list[str]:
    point = always_depth.unpack(row.checkpoint)
    assert point is not None
    return [page.removeprefix(f"https://{HOST}") for page in point.pages]


def _opened(site: FakeSite) -> list[str]:
    return [path for path in site.opened if path != "/"]


async def test_parts_continue_the_chain_without_reopening_pages(site: FakeSite) -> None:
    async with committed_sessions() as factory:
        await _switch(factory, on=True)
        first = await _part(factory, 1, None)
        after_first = await _depth(factory)
        second = await _part(factory, 2, 4)
        third = await _part(factory, 3, 8)
        last = await _part(factory, 4, 12)

        runs = [await _run(factory, done["crawl"]) for done in (first, second, third, last)]
        depth = await _depth(factory)
        judged = await crawl_jobs._judge(last["crawl"])
        async with factory() as session:
            roots = set((await session.scalars(select(CandidateModel.target_root))).all())

    assert _chain(after_first) == ["/", "/p0"], "первая часть — обычный потолок"
    assert [run.pages_opened for run in runs] == [2, 4, 8, 11], "страницы — всей цепочки"
    assert [(run.stats or {})[CHAIN_FROM] for run in runs] == [0, 2, 4, 8]
    assert [(run.stats or {})[CHAIN_TO] for run in runs] == [2, 4, 8, 11], "разных — после части"
    assert [(run.stats or {})["links_found"] for run in runs] == [2, 2, 4, 3], "ссылки — свои"
    assert [run.stop_reason for run in runs] == [StopReason.MAX_PAGES] * 3 + [StopReason.EXHAUSTED]
    assert all(run.checkpoint is None for run in runs)
    assert _opened(site) == [f"/p{n}" for n in range(SIZE)], "ни одна страница не открыта дважды"
    assert (depth.pages, depth.parts, depth.last_run_id) == (11, 4, last["crawl"])
    assert depth.first_done_at is not None, "первые «10 000» пройдены"
    assert depth.exhausted_at is not None, "карта дочитана, адреса кончились — сайт пройден"
    assert judged["crawl"] == last["crawl"]
    assert {f"adv{n}.example" for n in range(SIZE)} <= roots, "кандидаты — всей цепочки"


async def test_a_part_stuck_on_redirects_passes_the_first_step_and_the_next_goes_deeper(
    redirecting: FakeSite,
) -> None:
    """17–18.11.2026: адрес карты, уводящий на открытую страницу, открывается и считается, а цепочку
    не растит. Часть «до 10 000» (здесь — до четырёх) упиралась в потолок, цепочка стояла ниже, и
    следующая часть получала тот же потолок — по кругу, часть в минуту. Упёрлась — первые пройдены,
    и следующая часть идёт с потолком «ещё 10 000» до конца сайта."""
    async with committed_sessions() as factory:
        await _switch(factory, on=True)
        first = await _part(factory, 1, None)
        after_first = await _depth(factory)
        second = await _part(factory, 2, always_depth.first_step())
        async with factory() as session:
            stuck = (await always_depth.facts(session, {HOST}))[HOST]
            switch = await always_switch.state(session)
        stage = always._stage(HOST, stuck, False, always._Known(crawled={HOST}), switch)
        ceiling = always._ceiling(stage, stuck.pages)
        third = await _part(factory, 3, ceiling)
        runs = [await _run(factory, done["crawl"]) for done in (first, second, third)]
        depth = await _depth(factory)

    assert after_first.first_done_at is None, "первая часть — обычный потолок, не первые 10 000"
    assert runs[1].stop_reason is StopReason.MAX_PAGES
    assert runs[1].pages_opened == 4, "открытое — с уводом на уже открытую страницу"
    assert stuck.pages == 3, "увод на открытую страницу посчитан, а в цепочку не лёг"
    assert stuck.first_done_at is not None, "упёрлась в потолок первых — первые пройдены"
    assert stage is always.Stage.DEEPER
    assert ceiling == 3 + always_depth.first_step(), "дальше — ещё «10 000» к пройденному"
    assert [((run.stats or {})[CHAIN_FROM], (run.stats or {})[CHAIN_TO]) for run in runs] == [
        (0, 2),
        (2, 3),
        (3, 4),
    ]
    assert runs[2].stop_reason is StopReason.EXHAUSTED
    assert depth.pages == 4
    assert depth.exhausted_at is not None, "дошла до конца сайта"


async def test_the_night_ends_a_part_and_the_next_one_goes_on_from_there(
    site: FakeSite, monkeypatch: pytest.MonkeyPatch, continued: list[int]
) -> None:
    async with committed_sessions() as factory:
        await _switch(factory, on=True)
        await _part(factory, 1, None)

        def night_after_one_page(_moment: datetime) -> bool:
            return "/p1" in site.asked

        monkeypatch.setattr(always_part, "night_hours", night_after_one_page)
        stopped = await _part(factory, 2, 4)
        paused = await _run(factory, stopped["crawl"])
        mid = await _depth(factory)
        monkeypatch.setattr(always_part, "night_hours", lambda _moment: False)
        await _part(factory, 3, 4)
        depth = await _depth(factory)

    assert (stopped["outcome"], "interrupted" in stopped) == ("partial", False)
    assert continued == [], "ночь кончает часть, а не ставит ей продолжение"
    assert (paused.status, paused.stop_reason) == (CrawlStatus.DONE, StopReason.PAUSED)
    assert paused.outcome is CrawlOutcome.PARTIAL
    assert _chain(mid) == ["/", "/p0", "/p1"]
    assert _chain(depth) == ["/", "/p0", "/p1", "/p2"]
    assert _opened(site) == ["/p0", "/p1", "/p2"]


async def test_a_pause_ends_the_part_at_the_next_page(site: FakeSite) -> None:
    async with committed_sessions() as factory:
        await _switch(factory, on=True)
        await _part(factory, 1, None)
        await _switch(factory, on=False)
        paused = await _part(factory, 2, 4)
        row = await _run(factory, paused["crawl"])
        depth = await _depth(factory)

    assert paused["outcome"] == "partial"
    assert row.stop_reason is StopReason.PAUSED
    assert _chain(depth) == ["/", "/p0"], "на паузе новых страниц нет — цепочка как была"
    assert depth.parts == 2
    assert _opened(site) == ["/p0"]


async def test_the_switch_is_read_while_the_part_goes() -> None:
    part = Part(number=1, base=None)
    async with committed_sessions() as factory:
        watcher = asyncio.create_task(always_part.watch(factory, part, every=0.01))
        try:
            await _switch(factory, on=True)
            await asyncio.sleep(0.1)
            on = part.paused()
            await _switch(factory, on=False)
            await asyncio.sleep(0.1)
        finally:
            watcher.cancel()

    assert (on, part.paused()) == (False, True)


async def test_a_deploy_mid_part_continues_it_and_the_part_row_keeps_only_its_pages(
    site: FakeSite, monkeypatch: pytest.MonkeyPatch, continued: list[int]
) -> None:
    async with committed_sessions() as factory:
        await _switch(factory, on=True)
        await _part(factory, 1, None)
        monkeypatch.setattr(crawl_jobs, "stop_requested", lambda: "/p1" in site.asked)
        first = await _part(factory, 2, 4)
        halted = await _run(factory, first["crawl"])
        monkeypatch.setattr(crawl_jobs, "stop_requested", lambda: False)
        done = await crawl_jobs._crawl(first["crawl"])
        finished = await _run(factory, first["crawl"])
        depth = await _depth(factory)

    assert first["interrupted"] is True
    assert continued == [first["crawl"]], "выкатка — продолжение той же части"
    assert halted.status is CrawlStatus.QUEUED
    assert halted.pages_opened == 3, "страницы — всей цепочки"
    assert halted.checkpoint is not None
    assert halted.checkpoint["pages"] == [f"https://{HOST}/p1"], "в строке части — только её"
    assert done["outcome"] == "ok"
    assert finished.pages_opened == 4
    assert _chain(depth) == ["/", "/p0", "/p1", "/p2"]
    assert _opened(site) == ["/p0", "/p1", "/p2"], "продолжение не открыло открытое"


# --- чекпоинт цепочки ------------------------------------------------------------------------------


def test_the_chain_checkpoint_is_compressed_bounded_and_survives_damage() -> None:
    pages = [f"https://{HOST}/2026/11/article-{n}/" for n in range(10_000)]
    queue = [f"https://{HOST}/tag/{n}/" for n in range(always_depth.QUEUE_KEEP + 500)]
    point = Checkpoint("sitemap+links", pages=pages, queue=queue, attempts=12_000, articles=9_000)

    packed = always_depth.pack(point)
    back = always_depth.unpack(packed)

    assert back is not None
    assert back.pages == pages
    assert len(back.queue) == always_depth.QUEUE_KEEP
    assert (back.attempts, back.articles) == (12_000, 9_000)
    assert len(packed) < 400_000, f"10 000 страниц и очередь — {len(packed)} байт"
    assert always_depth.unpack(b"not zlib") is None
    assert always_depth.unpack(zlib.compress(b"[1, 2]")) is None
    assert always_depth.unpack(None) is None


def test_a_part_starts_its_own_clock_but_keeps_the_chain() -> None:
    base = Checkpoint(
        "sitemap+links",
        pages=["a", "b"],
        queue=["c"],
        attempts=7,
        elapsed_sec=3_600.0,
        time_budget_sec=9_000.0,
        slowed=True,
    )

    seeded = always_depth.seed(base)
    stored = always_depth.stored(Checkpoint("links", pages=["a", "b", "x"], queue=["y"]), 2)
    merged = always_depth.merged(base, stored)

    assert (seeded.pages, seeded.queue, seeded.attempts) == (["a", "b"], ["c"], 7)
    assert (seeded.elapsed_sec, seeded.time_budget_sec, seeded.slowed) == (0.0, 0.0, False)
    assert stored["pages"] == ["x"]
    assert merged is not None
    assert (merged.pages, merged.queue, merged.source) == (["a", "b", "x"], ["y"], "links")
    assert always_depth.merged(None, None) is None
    assert always_depth.merged(base, None) is base


@pytest.mark.parametrize(
    ("found", "complete", "stop", "exhausted"),
    [
        (True, True, StopReason.EXHAUSTED, True),
        (False, False, StopReason.EXHAUSTED, True),
        (True, False, StopReason.EXHAUSTED, False),
        (None, False, StopReason.EXHAUSTED, False),
        (True, True, StopReason.MAX_PAGES, False),
    ],
    ids=["карта дочитана", "карты нет", "карта оборвана", "карта не прочитана", "потолок"],
)
def test_a_site_is_done_only_when_its_map_was_read_to_the_end(
    found: bool | None, complete: bool, stop: StopReason, exhausted: bool
) -> None:
    report = CrawlReport(
        host=HOST,
        outcome=CrawlOutcome.OK,
        stop_reason=stop,
        sitemap_found=found,
        sitemap_complete=complete,
    )

    assert always_depth.exhausted(report) is exhausted


def test_the_sitemap_ceiling_grows_with_the_part_target(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawl_cfg, "MAX_PAGES_DEEP", 10_000)

    deep = always_part.ceilings(10_000)
    third = always_part.ceilings(30_000)
    huge = always_part.ceilings(10_000_000)
    first = always_part.ceilings(None)

    assert (deep.sitemap_urls, deep.sitemap_files) == (
        crawl_cfg.MAX_SITEMAP_URLS_DEEP,
        crawl_cfg.MAX_SITEMAP_FILES_DEEP,
    )
    assert (third.pages, third.sitemap_urls) == (30_000, 3 * crawl_cfg.MAX_SITEMAP_URLS_DEEP)
    assert huge.sitemap_urls == always_part.MAP_SCALE_MAX * crawl_cfg.MAX_SITEMAP_URLS_DEEP
    assert first.pages == crawl_cfg.MAX_PAGES_PER_DONOR, "первая часть — обычный потолок"
