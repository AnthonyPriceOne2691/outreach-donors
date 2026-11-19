"""Обход 24/7 (`crawl/always.py`): выдача частей — порядок, окна и «пройдено всё».

Проверяется то, чем автомат может навредить без человека у экрана: пойти, когда его не запускали;
обогнать обход, поставленный человеком, или ночной круг; занять всех обходчиков; ставить закрытый сайт
каждую минуту; обойти слабый или пройденный сайт; обойти доходный сайт позже бездоходного; молчать
или говорить «пройдено всё» по кругу. Очередь задач — подставная, сайты не открываются: части только
ставятся.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.config import crawl as crawl_cfg
from backend.config import crawl_always as cfg
from backend.features.core.domain import AuditAction, CrawlOutcome, CrawlStatus, StopReason
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.crawl import (
    CrawlAlwaysSignalModel,
    CrawlBacklogModel,
    CrawlDepthModel,
    CrawlNightModel,
    CrawlRunModel,
)
from backend.features.core.models.donor import DonorModel
from backend.features.crawl import (
    always,
    always_depth,
    always_switch,
    always_view,
    auto_round,
    backlog,
    judge_night,
    night,
)
from backend.features.crawl import targets as targets_module
from backend.features.crawl.always import ALWAYS_BY, DONE, Spec
from backend.features.crawl.always_part import CHAIN_FROM, CHAIN_TO
from backend.features.crawl.night import NightStatus
from backend.features.crawl.progress import Checkpoint
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_alarm_concern import _plain

#: Полдень 17 ноября по Москве.
NOON = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
#: 03:00 по Москве — посреди ночного окна.
NIGHT_TIME = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
#: 08:00 по Москве — окно по часам кончилось.
MORNING = datetime(2026, 11, 17, 5, 0, tzinfo=UTC)


class _Enqueue:
    """Очередь обходов в памяти."""

    def __init__(self) -> None:
        self.queued: list[int] = []

    def __call__(self, run_id: int, job_id: str) -> None:
        self.queued.append(run_id)


def _ids(run_id: int) -> str:
    return f"crawl-{run_id}-always"


@pytest.fixture(autouse=True)
def said(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Тревоги — в список; ночного круга нет, пока тест не включит; рубильник включён."""
    alerts: list[str] = []

    async def send(text: str) -> bool:
        alerts.append(text)
        return True

    monkeypatch.setattr(always, "send_alert", send)
    monkeypatch.setattr(crawl_cfg, "NIGHT_ROUND_ENABLED", False)
    monkeypatch.setattr(cfg, "ENABLED", True)
    monkeypatch.setattr(cfg, "SLOTS", 12)
    return alerts


async def _donor(session: AsyncSession, host: str) -> None:
    """Принятый годный донор со свежей ценой — цель обхода."""
    domain = await make_donor(session, host)
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    donor.last_price = Decimal("100.00")
    donor.last_price_at = NOON - timedelta(days=1)
    await session.flush()


#: Обход по умолчанию: законченный, «обойдён», упёрся в потолок страниц, тысяча страниц.
RUN: dict[str, Any] = {
    "status": CrawlStatus.DONE,
    "outcome": CrawlOutcome.OK,
    "stop_reason": StopReason.MAX_PAGES,
    "pages_opened": 1_000,
    "resumes": 0,
}


async def _run(
    session: AsyncSession,
    host: str,
    *,
    part: int | None = None,
    chain_from: int = 0,
    chain_to: int | None = None,
    **given: Any,
) -> CrawlRunModel:
    """Обход донора — обычный или часть цепочки (`part`) с её началом в цепочке и, если дан,
    концом; поля строки — как в `RUN`, кроме названных."""
    chain: dict[str, int] = {CHAIN_FROM: chain_from}
    if chain_to is not None:
        chain[CHAIN_TO] = chain_to
    run = CrawlRunModel(
        host=host,
        depth_part=part,
        stats=chain if part is not None else None,
        **{**RUN, **given},
    )
    session.add(run)
    await session.flush()
    return run


async def _depth(session: AsyncSession, host: str, pages: int, **more: object) -> CrawlDepthModel:
    row = CrawlDepthModel(host=host, pages=pages, **more)
    session.add(row)
    await session.flush()
    return row


async def _on(session: AsyncSession, **settings: object) -> None:
    await always_switch.set_switch(session, by=None, enabled=True, **settings)  # type: ignore[arg-type]


async def _tick(
    session: AsyncSession, now: datetime = NOON, *, workers: int = 16, stop: str | None = None
) -> list[str]:
    return await always.tick(
        session, workers=workers, enqueue=_Enqueue(), job_id=_ids, now=now, stop=stop
    )


async def _plan(session: AsyncSession) -> list[Spec]:
    return always.plan(await always.survey(session, await always_switch.state(session)))


async def _parts(session: AsyncSession) -> list[CrawlRunModel]:
    rows = await session.scalars(
        select(CrawlRunModel)
        .where(CrawlRunModel.requested_by == ALWAYS_BY)
        .order_by(CrawlRunModel.id)
    )
    return list(rows.all())


Seed = Callable[[AsyncSession], Awaitable[None]]


async def _base(session: AsyncSession) -> None:
    """Сайты во всех очередях: новый, не прошедший первые 10 000, два доходных дальше, слабый и
    пройденный до конца."""
    for host in ("new.test", "first.test", "low.test", "high.test", "weak.test", "done.test"):
        await _donor(session, host)
    for host in ("first.test", "low.test", "high.test", "weak.test", "done.test"):
        await _run(session, host)
    await _depth(session, "first.test", 3_000)
    await _depth(session, "low.test", 10_000, first_done_at=NOON, yield_pct=0.5)
    await _depth(session, "high.test", 12_000, first_done_at=NOON, yield_pct=4.0)
    await _depth(session, "weak.test", 10_000, first_done_at=NOON, yield_pct=0.05)
    await _depth(session, "done.test", 4_000, first_done_at=NOON, exhausted_at=NOON)


# --- порядок ---------------------------------------------------------------------------------------


async def test_nothing_goes_until_someone_starts_it(session: AsyncSession) -> None:
    await _base(session)

    assert await _tick(session) == []
    assert await _parts(session) == []


async def test_new_sites_first_then_ten_thousand_then_deeper_by_yield(
    session: AsyncSession,
) -> None:
    await _base(session)
    await _on(session)

    planned = await _plan(session)

    assert [(spec.host, spec.max_pages) for spec in planned] == [
        ("new.test", None),
        ("first.test", 10_000),
        ("high.test", 22_000),
        ("low.test", 20_000),
    ], "слабый и пройденный до конца не идут; дальше 10 000 — доходные первыми"
    assert {spec.part for spec in planned} == {1}


async def test_weak_sites_go_after_the_strong_when_recrawl_is_on(session: AsyncSession) -> None:
    await _base(session)
    await _on(session, recrawl_weak=True)

    planned = [spec.host for spec in await _plan(session)]

    assert planned == ["new.test", "first.test", "high.test", "low.test", "weak.test"]


async def test_a_higher_threshold_marks_more_sites_at_once(session: AsyncSession) -> None:
    await _base(session)
    await _on(session, threshold_pct=1.0)

    planned = [spec.host for spec in await _plan(session)]

    assert planned == ["new.test", "first.test", "high.test"], "порог сменили — пометки сразу"


async def test_parts_are_issued_through_the_launch_and_journaled(session: AsyncSession) -> None:
    await _base(session)
    await _run(session, "first.test", part=1, pages_opened=3_000, chain_from=1_000)
    await _on(session)

    issued = await _tick(session)

    assert issued == ["new.test", "first.test"], "не больше двух за проход"
    parts = await _parts(session)
    assert [(run.host, run.depth_part, run.max_pages, run.status) for run in parts[-2:]] == [
        ("new.test", 1, None, CrawlStatus.QUEUED),
        ("first.test", 2, 10_000, CrawlStatus.QUEUED),
    ]
    journal = await session.scalars(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
    )
    assert [entry.details for entry in journal.all()] == [
        {"этап": "2 — обход донора", "донор": "new.test", "как": "обход 24/7, часть 1"},
        {"этап": "2 — обход донора", "донор": "first.test", "как": "обход 24/7, часть 2"},
    ]


# --- окна выдачи -----------------------------------------------------------------------------------


async def test_parts_go_only_into_free_workers_and_within_the_slots(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    for host in ("a.test", "b.test", "c.test", "d.test"):
        await _donor(session, host)
    await _on(session)
    monkeypatch.setattr(cfg, "SLOTS", 3)

    assert await _tick(session, workers=1) == ["a.test"], "один свободный обходчик — одна часть"
    assert await _tick(session) == [], "ждущий в очереди обходов уходит первым"
    for run in await _parts(session):
        run.status = CrawlStatus.RUNNING
    await session.flush()
    assert await _tick(session, workers=1) == [], "свободных обходчиков нет"
    assert await _tick(session) == ["b.test", "c.test"]
    for run in await _parts(session):
        run.status = CrawlStatus.RUNNING
    await session.flush()

    assert await _tick(session) == [], "три части разом — предел"


async def test_humans_in_the_backlog_go_first(session: AsyncSession) -> None:
    await _donor(session, "a.test")
    await _on(session)
    row = CrawlBacklogModel(host="human.test", requested_by="человек@site.test")
    session.add(row)
    await session.flush()

    assert await _tick(session) == []

    row.max_pages = crawl_cfg.MAX_PAGES_DEEP
    for number in range(crawl_cfg.DEEP_MAX_RUNNING):
        await _run(session, f"deep{number}.test", status=CrawlStatus.RUNNING, outcome=None)
        last = await session.scalar(select(CrawlRunModel).order_by(CrawlRunModel.id.desc()))
        assert last is not None
        last.max_pages = crawl_cfg.MAX_PAGES_DEEP
    night_row = CrawlBacklogModel(host="nightly.test", requested_by=night.NIGHT_BY)
    session.add(night_row)
    await session.flush()

    assert await _tick(session) == ["a.test"], (
        "глубокому человека места нет до конца идущих — его ожидание обход 24/7 не держит, "
        "а строки ночного круга — не люди"
    )


@pytest.mark.parametrize(
    ("moment", "night_status", "issued"),
    [
        (NIGHT_TIME, None, False),
        (MORNING, NightStatus.RUNNING, False),
        (MORNING, NightStatus.DONE, True),
        (MORNING, NightStatus.MISSED, True),
    ],
    ids=["ночь по часам", "ночной круг ещё идёт", "круг кончился", "круг пропущен"],
)
async def test_the_night_keeps_its_window(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    moment: datetime,
    night_status: NightStatus | None,
    issued: bool,
) -> None:
    monkeypatch.setattr(crawl_cfg, "NIGHT_ROUND_ENABLED", True)
    await _donor(session, "a.test")
    await _on(session)
    if night_status is not None:
        session.add(CrawlNightModel(night=date(2026, 11, 17), status=night_status.value))
        await session.flush()

    assert await _tick(session, moment) == (["a.test"] if issued else [])


async def test_without_the_night_round_it_goes_around_the_clock(session: AsyncSession) -> None:
    await _donor(session, "a.test")
    await _on(session)

    assert await _tick(session, NIGHT_TIME) == ["a.test"]


async def test_no_units_no_switch_no_parts(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _donor(session, "a.test")
    await _on(session)

    assert await _tick(session, stop="закончились юниты Ahrefs") == []
    monkeypatch.setattr(cfg, "ENABLED", False)
    assert await _tick(session) == [], "рубильник сервиса сильнее выключателя"


async def test_busy_donors_and_catalogs_are_left_alone(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    for host in ("busy.test", "catalog.test", "free.test"):
        await _donor(session, host)
    await _run(
        session, "busy.test", status=CrawlStatus.RUNNING, outcome=None, requested_by="человек"
    )

    async def catalogs(_session: AsyncSession) -> frozenset[str]:
        return frozenset({"catalog.test"})

    monkeypatch.setattr(targets_module, "_catalogs", catalogs)
    await _on(session)

    found = await always.survey(session, await always_switch.state(session))

    assert [spec.host for spec in always.plan(found)] == ["free.test"]
    assert found.catalogs == 1
    assert {donor.host for donor in found.donors} == {"busy.test", "free.test"}


@pytest.mark.parametrize(
    ("rest_min", "planned"),
    [(120, ["rested.test", "part.test"]), (0, ["just.test", "rested.test", "part.test"])],
    ids=["передышка", "без передышки"],
)
async def test_a_site_rests_after_its_regular_crawl(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, rest_min: int, planned: list[str]
) -> None:
    """Обычный обход сайта кончился меньше двух часов назад — часть 24/7 подождёт (шапка: утром
    19.11 части, начатые сразу после ночного обхода, вставали по доле отказов). Своя часть 24/7
    передышки не требует, а донор на передышке не пройден: он в очереди, «пройдено всё» не звучит."""
    monkeypatch.setattr(cfg, "REST_MIN", rest_min)
    for host in ("just.test", "rested.test", "part.test"):
        await _donor(session, host)
    await _run(session, "just.test", finished_at=NOON - timedelta(minutes=30))
    await _run(session, "rested.test", finished_at=NOON - timedelta(hours=3))
    await _run(session, "part.test")
    await _depth(session, "part.test", 1_000)
    await _run(
        session,
        "part.test",
        part=1,
        outcome=CrawlOutcome.PARTIAL,
        stop_reason=StopReason.PAUSED,
        finished_at=NOON - timedelta(minutes=10),
    )
    await _on(session)

    found = await always.survey(session, await always_switch.state(session), NOON)

    assert [spec.host for spec in always.plan(found)] == planned
    assert {donor.host for donor in found.remaining()} == {"just.test", "rested.test", "part.test"}


# --- отказ и повтор --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "outcome", "stop", "pages", "resumes", "rests"),
    [
        (CrawlStatus.DONE, CrawlOutcome.BLOCKED, StopReason.NO_START, 0, 0, True),
        (CrawlStatus.DONE, CrawlOutcome.PARTIAL, StopReason.UNHEALTHY, 1_500, 0, True),
        (CrawlStatus.DONE, CrawlOutcome.PARTIAL, StopReason.TIMEOUT, 1_000, 0, True),
        (CrawlStatus.DONE, CrawlOutcome.PARTIAL, StopReason.TIMEOUT, 1_200, 0, False),
        (CrawlStatus.DONE, CrawlOutcome.PARTIAL, StopReason.PAUSED, 1_000, 0, False),
        (CrawlStatus.STOPPED, None, None, 1_100, 2, True),
        (CrawlStatus.STOPPED, None, None, 0, 0, False),
    ],
    ids=[
        "закрылся",
        "стал отказывать",
        "ничего нового",
        "что-то новое",
        "ночь или пауза",
        "остановлен разбором",
        "очередь задач не ответила",
    ],
)
async def test_a_refused_part_rests_until_the_site_opens_again(
    session: AsyncSession,
    status: CrawlStatus,
    outcome: CrawlOutcome | None,
    stop: StopReason | None,
    pages: int,
    resumes: int,
    rests: bool,
) -> None:
    await _donor(session, "shut.test")
    await _run(session, "shut.test", part=1, pages_opened=1_000)
    await _depth(session, "shut.test", 1_000)
    await _run(
        session,
        "shut.test",
        part=2,
        status=status,
        outcome=outcome,
        stop_reason=stop,
        pages_opened=pages,
        chain_from=1_000,
        resumes=resumes,
    )
    await _on(session)

    assert (await _plan(session) == []) is rests
    await _run(session, "shut.test", pages_opened=800)
    planned = await _plan(session)

    assert [(spec.host, spec.part) for spec in planned] == [("shut.test", 3)], (
        "сайт снова открылся обходу не из цепочки — повтор"
    )
    assert planned[0].retry is rests


# --- впустую ---------------------------------------------------------------------------------------


async def test_a_chain_that_hit_the_first_ceiling_goes_deeper_below_ten_thousand(
    session: AsyncSession,
) -> None:
    """17–18.11.2026: цепочка стояла на 9 973 разных страницах, а часть «до 10 000» упиралась в
    потолок за минуту — адреса карты уводили на открытое. Упёрлась — первые 10 000 пройдены
    (`always_depth.reached_first`), и следующая часть идёт дальше, а не по кругу."""
    await _donor(session, "stuck.test")
    await _run(session, "stuck.test", part=1, pages_opened=1_000)
    await _depth(session, "stuck.test", 9_973, first_done_at=NOON)
    await _run(
        session,
        "stuck.test",
        part=2,
        max_pages=10_000,
        pages_opened=10_000,
        chain_from=9_973,
        chain_to=9_973,
    )
    await _on(session)

    planned = await _plan(session)

    assert [(spec.host, spec.part, spec.max_pages, spec.retry) for spec in planned] == [
        ("stuck.test", 3, 19_973, False)
    ], "потолок вырос — следующей части есть куда идти"


@pytest.mark.parametrize(
    ("stop", "chain_to", "rests"),
    [
        (StopReason.EXHAUSTED, 12_000, True),
        (StopReason.MAX_PAGES, 12_000, True),
        (StopReason.EXHAUSTED, 12_001, False),
        (StopReason.PAUSED, 12_000, False),
        (StopReason.EXHAUSTED, None, False),
    ],
    ids=[
        "адреса кончились, цепочка не выросла",
        "упёрлась, цепочка не выросла",
        "одна новая страница",
        "ночь или пауза",
        "часть до правки — по открытому",
    ],
)
async def test_a_part_that_adds_no_page_rests_until_the_site_opens_again(
    session: AsyncSession, stop: StopReason, chain_to: int | None, rests: bool
) -> None:
    await _donor(session, "idle.test")
    await _run(session, "idle.test", part=1, pages_opened=1_000)
    await _depth(session, "idle.test", 12_000, first_done_at=NOON, yield_pct=4.0)
    await _run(
        session,
        "idle.test",
        part=2,
        max_pages=22_000,
        outcome=CrawlOutcome.PARTIAL if stop is StopReason.PAUSED else CrawlOutcome.OK,
        stop_reason=stop,
        pages_opened=12_027,
        chain_from=12_000,
        chain_to=chain_to,
    )
    await _on(session)

    assert (await _plan(session) == []) is rests, "тот же потолок — те же адреса"
    await _run(session, "idle.test", pages_opened=800)
    planned = await _plan(session)

    assert [(spec.host, spec.part, spec.max_pages) for spec in planned] == [
        ("idle.test", 3, 22_000)
    ], "сайт снова открылся обходу не из цепочки — повтор"
    assert planned[0].retry is rests


# --- пройдено всё ----------------------------------------------------------------------------------


async def test_all_done_is_said_once_and_again_only_after_new_work(
    session: AsyncSession, said: list[str]
) -> None:
    for host in ("done.test", "weak.test"):
        await _donor(session, host)
        await _run(session, host)
    await _depth(session, "done.test", 4_000, first_done_at=NOON, exhausted_at=NOON)
    await _depth(session, "weak.test", 10_000, first_done_at=NOON, yield_pct=0.0)
    await _on(session)

    await _tick(session)
    await _tick(session, NOON + timedelta(minutes=1))

    assert len(said) == 1
    assert "пройдено всё" in said[0]
    assert "до конца — 1, мало рекламодателей — 1" in said[0]
    assert said[0].endswith(DONE.words)
    _plain(DONE)

    await _donor(session, "fresh.test")
    assert await _tick(session, NOON + timedelta(minutes=2)) == ["fresh.test"]
    signal = await session.scalar(select(CrawlAlwaysSignalModel))
    assert signal is not None
    assert signal.reopened_at is not None, "новая работа — новая эпоха"
    part = (await _parts(session))[-1]
    part.status, part.outcome, part.stop_reason = (
        CrawlStatus.DONE,
        CrawlOutcome.OK,
        StopReason.EXHAUSTED,
    )
    await _depth(session, "fresh.test", 300, first_done_at=NOON, exhausted_at=NOON)

    await _tick(session, NOON + timedelta(minutes=3))

    assert len(said) == 2
    epochs = await session.scalars(select(CrawlAlwaysSignalModel.epoch))
    assert sorted(epochs.all()) == [1, 2]


async def test_a_retry_after_refusal_is_not_new_work(
    session: AsyncSession, said: list[str]
) -> None:
    await _donor(session, "shut.test")
    await _run(
        session,
        "shut.test",
        part=1,
        outcome=CrawlOutcome.BLOCKED,
        stop_reason=StopReason.NO_START,
        pages_opened=0,
    )
    await _on(session)

    await _tick(session)
    assert len(said) == 1, "ждущий после отказа — тоже «сделано всё, что можно»"
    await _run(session, "shut.test", pages_opened=500)

    assert await _tick(session, NOON + timedelta(minutes=1)) == ["shut.test"]
    signal = await session.scalar(select(CrawlAlwaysSignalModel))
    assert signal is not None
    assert signal.reopened_at is None, "повтор после отказа эпоху не открывает"


async def test_nothing_is_said_without_donors_or_while_parts_run(
    session: AsyncSession, said: list[str]
) -> None:
    await _on(session)
    await _tick(session)
    await _donor(session, "done.test")
    await _run(session, "done.test")
    await _depth(session, "done.test", 4_000, first_done_at=NOON, exhausted_at=NOON)
    await _run(session, "done.test", part=2, status=CrawlStatus.RUNNING, outcome=None)

    await _tick(session)

    assert said == []


# --- память процесса разбора -----------------------------------------------------------------------


async def test_the_step_and_the_screen_never_read_checkpoints_or_links(
    session: AsyncSession,
) -> None:
    """Выдача живёт в процессе разбора с потолком памяти в 256 МБ: чекпоинты цепочек (мегабайты на
    донора) и ссылки обходов она не читает — только колонки, числа и флаги. Экран — тоже."""
    await _base(session)
    big = Checkpoint("sitemap", pages=[f"https://first.test/p{n}" for n in range(5_000)])
    for row in (await session.scalars(select(CrawlDepthModel))).all():
        row.checkpoint = always_depth.pack(big)
    await _run(session, "low.test", part=1, status=CrawlStatus.RUNNING, outcome=None)
    last = await session.scalar(select(CrawlRunModel).order_by(CrawlRunModel.id.desc()))
    assert last is not None
    last.checkpoint = big.as_dict()
    await _on(session)
    session.expunge_all()
    said: list[str] = []

    def remember(*args: object) -> None:
        said.append(str(args[2]))

    connection = (await session.connection()).sync_connection
    assert connection is not None
    event.listen(connection, "before_cursor_execute", remember)
    try:
        issued = await _tick(session)
        await always_view.view(session, now=NOON)
        await always_view.depths(session, ["low.test", "high.test"], threshold=0.1)
        reads = [one for one in said if one.lstrip().upper().startswith(("SELECT", "WITH"))]
        await session.execute(select(CrawlDepthModel.checkpoint).limit(1))
    finally:
        event.remove(connection, "before_cursor_execute", remember)

    assert issued
    assert reads
    assert [one for one in reads if "checkpoint" in one or "outlinks" in one] == [], (
        "запись части (её чекпоинт пуст) — не чтение"
    )
    assert "checkpoint" in said[-1], "проверка видит чтение чекпоинта, когда оно есть"


# --- в проходе очереди загрузки --------------------------------------------------------------------


async def test_the_step_runs_in_the_backlog_pass_right_after_the_night(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    order: list[str] = []

    async def step(name: str) -> None:
        order.append(name)

    async def nightly(_session: AsyncSession, **_kwargs: object) -> None:
        await step("ночь")

    async def always_step(_session: AsyncSession, **kwargs: object) -> list[str]:
        await step(f"24/7 {kwargs['stop']}")
        return []

    async def judged(_session: AsyncSession, **_kwargs: object) -> None:
        await step("судья")

    async def rounded(_session: AsyncSession, **_kwargs: object) -> None:
        await step("автокруг")

    monkeypatch.setattr(night, "tick", nightly)
    monkeypatch.setattr(always, "tick", always_step)
    monkeypatch.setattr(judge_night, "tick", judged)
    monkeypatch.setattr(auto_round, "tick", rounded)

    await backlog.feed(session, workers=4, enqueue=_Enqueue(), job_id=_ids, now=NOON)

    assert order == ["ночь", "24/7 None", "судья", "автокруг"]
