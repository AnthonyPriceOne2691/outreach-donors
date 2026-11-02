"""Ночной обход (`crawl/night.py`): в полночь по Москве, один раз, за окно не длиннее семи часов.

Проверяется то, ради чего круг: расчёт — до запуска и по настоящей мощности, порядок — кому повтор
нужнее, ночь одна при рестарте и двух процессах, после срока новые обходы не выдаются, обходы
человека важнее ночных, и всё это видно на следующее утро. Круг пишет на настоящей базе и фиксирует
сам — сессии с настоящими фиксациями (`committed_sessions`).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.api.crawls import routes
from backend.config import crawl as crawl_cfg
from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.crawl import CrawlBacklogModel, CrawlNightModel, CrawlRunModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl import backlog, night
from backend.features.crawl.night import LastRun, NightStatus, Power, Rank, longest_of
from backend.shared.crawl_marks import Held
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer, make_donor
from tests.migration_helpers import load_migration
from tests.test_send_race import committed_sessions

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Полночь на 3 ноября по Москве — 21:00 по UTC накануне.
MIDNIGHT = datetime(2026, 11, 2, 21, 0, tzinfo=UTC)
NIGHT = date(2026, 11, 3)
WHO = "anna@ours.example.test"


class _Enqueue:
    """Очередь обходов в памяти."""

    def __init__(self) -> None:
        self.queued: list[int] = []

    def __call__(self, run_id: int, job_id: str) -> None:
        self.queued.append(run_id)


def _ids(run_id: int) -> str:
    return f"crawl-{run_id}-test"


@pytest.fixture(autouse=True)
def alerts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Круг включён, Redis не спрашиваем, тревоги — в список."""
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(night, "send_alert", send)
    monkeypatch.setattr(night, "crawlers_held", lambda: Held(0))
    monkeypatch.setattr(crawl_cfg, "NIGHT_ROUND_ENABLED", True)
    return said


async def _priced(session: AsyncSession, host: str) -> None:
    """Принятый годный донор со свежей ценой — цель обхода."""
    domain = await make_donor(session, host)
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    donor.last_price = Decimal("100.00")
    donor.last_price_at = datetime.now(UTC) - timedelta(days=1)


def _crawled(
    host: str,
    *,
    pages: int,
    stop: StopReason | None = StopReason.EXHAUSTED,
    days: int = 5,
    status: CrawlStatus = CrawlStatus.DONE,
    max_pages: int | None = None,
    seconds: float | None = None,
) -> CrawlRunModel:
    """Обход донора `days` дней до полуночи круга."""
    return CrawlRunModel(
        host=host,
        status=status,
        outcome=CrawlOutcome.OK if pages else CrawlOutcome.BLOCKED,
        stop_reason=stop,
        pages_opened=pages,
        max_pages=max_pages,
        created_at=MIDNIGHT - timedelta(days=days),
        stats={"elapsed_sec": seconds} if seconds is not None else None,
    )


async def _feed(
    factory: async_sessionmaker[AsyncSession],
    now: datetime = MIDNIGHT,
    *,
    workers: int = 16,
) -> backlog.Fed:
    async with factory() as session:
        fed = await backlog.feed(session, workers=workers, enqueue=_Enqueue(), job_id=_ids, now=now)
        await session.commit()
    return fed


async def _tick(factory: async_sessionmaker[AsyncSession], now: datetime = MIDNIGHT) -> None:
    """Шаг круга без выдачи: что круг поставил, остаётся в очереди загрузки."""
    async with factory() as session:
        await night.tick(session, workers=16, per_minute=2, add=backlog.add, now=now)


async def _rounds(factory: async_sessionmaker[AsyncSession]) -> list[CrawlNightModel]:
    async with factory() as session:
        return list((await session.scalars(select(CrawlNightModel))).all())


async def _waiting(factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with factory() as session:
        rows = await session.scalars(select(CrawlBacklogModel.host).order_by(CrawlBacklogModel.id))
        return list(rows.all())


# --- очередь круга ----------------------------------------------------------------------------


def _last(pages: int, stop: StopReason | None = None, **rest: Any) -> LastRun:
    return LastRun(
        pages=pages,
        stop_reason=stop,
        status="done",
        max_pages=rest.get("max_pages"),
        created_at=rest.get("at", MIDNIGHT - timedelta(days=5)),
    )


@pytest.mark.parametrize(
    ("last", "rank"),
    [
        (_last(1000, StopReason.MAX_PAGES), Rank.LIMIT),
        (_last(1000, StopReason.EXHAUSTED), Rank.LIMIT),  # ровно потолок — обрезан он или нет
        (_last(0, StopReason.NO_START), Rank.NO_START),
        (_last(0, StopReason.ROBOTS), Rank.NO_START),
        (None, Rank.NO_START),  # не обходили — о сайте ничего не известно
        (_last(1, StopReason.TIMEOUT), Rank.PARTIAL),
        (_last(999, StopReason.EXHAUSTED), Rank.PARTIAL),
    ],
)
def test_who_needs_a_repeat_more(last: LastRun | None, rank: Rank) -> None:
    assert night.rank_of(last) == rank


def test_the_queue_goes_by_rank_and_from_the_oldest_crawl() -> None:
    last = {
        "partial.example": _last(500, at=MIDNIGHT - timedelta(days=9)),
        "newer-limit.example": _last(1000, StopReason.MAX_PAGES, at=MIDNIGHT - timedelta(days=1)),
        "older-limit.example": _last(1000, StopReason.MAX_PAGES, at=MIDNIGHT - timedelta(days=3)),
        "blocked.example": _last(0, StopReason.NO_START),
    }

    queue, rested = night.order([*last, "never.example"], last, now=MIDNIGHT)

    assert queue == [
        "older-limit.example",
        "newer-limit.example",
        "never.example",  # не обходили — самый давний из «не начались»
        "blocked.example",
        "partial.example",
    ]
    assert rested == 0


def test_all_donors_in_one_rank_still_go_from_the_oldest() -> None:
    last = {f"site{day}.example": _last(10, at=MIDNIGHT - timedelta(days=day)) for day in (1, 7, 3)}

    queue, _ = night.order(list(last), last, now=MIDNIGHT)

    assert queue == ["site7.example", "site3.example", "site1.example"]


def test_a_recent_deep_crawl_rests_and_an_old_one_does_not() -> None:
    """Обычный обход заменил бы найденное на десяти тысячах страниц найденным на тысяче."""
    last = {
        "fresh-deep.example": _last(9000, max_pages=10_000, at=MIDNIGHT - timedelta(days=29)),
        "old-deep.example": _last(9000, max_pages=10_000, at=MIDNIGHT - timedelta(days=31)),
    }

    queue, rested = night.order(list(last), last, now=MIDNIGHT)

    assert queue == ["old-deep.example"]
    assert rested == 1


# --- мощность ---------------------------------------------------------------------------------


def _power(**changed: Any) -> Power:
    values: dict[str, Any] = {
        "workers": 16,
        "deep": 0,
        "held": 0,
        "ahead": 0,
        "typical_sec": 1_800.0,
        "longest_sec": 3_600.0,
        "window_sec": 25_200.0,
        "per_minute": 2,
    }
    return Power(**(values | changed))


def test_power_is_starts_in_the_issuing_time() -> None:
    """Выдача идёт шесть часов из семи: 16 обходчиков по получасу — 192 старта."""
    assert _power().capacity == 192


def test_deep_crawls_and_held_crawlers_take_their_slots() -> None:
    assert _power(deep=4, held=2).slots == 10
    assert _power(deep=4, held=2).capacity == 120


def test_who_already_waits_goes_first() -> None:
    assert _power(ahead=100).capacity == 92
    assert _power(ahead=500).capacity == 0


def test_the_pace_of_the_feed_is_a_ceiling_too() -> None:
    """Короткие обходы упираются не в обходчиков, а в два старта в минуту."""
    assert _power(typical_sec=10.0, longest_sec=60.0).capacity == 838


def test_power_less_than_one_donor_is_zero() -> None:
    assert _power(workers=1, typical_sec=30_000.0, longest_sec=30_000.0).capacity == 0
    assert _power(workers=0).capacity == 0
    assert _power(workers=4, deep=4).capacity == 0


def test_a_full_round_fits_the_window() -> None:
    power = _power()

    assert power.estimate_sec(power.capacity) <= power.window_sec
    assert power.estimate_sec(0) == 0.0


def test_the_long_crawl_is_the_ninetieth_percentile_and_not_less_than_typical() -> None:
    assert longest_of([float(n) for n in range(1, 11)], 5.0) == 9.0
    assert longest_of([], 1_500.0) == 1_500.0
    assert longest_of([100.0], 1_500.0) == 1_500.0


# --- начало круга -----------------------------------------------------------------------------


async def test_the_round_starts_at_midnight_with_a_plan_in_the_order_of_need() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            for host in ("partial.example", "limit.example", "never.example"):
                await _priced(session, host)
            session.add(_crawled("partial.example", pages=300))
            session.add(_crawled("limit.example", pages=1000, stop=StopReason.MAX_PAGES))
            await session.commit()

        await _feed(factory, workers=1)

        [found] = await _rounds(factory)
        waiting = await _waiting(factory)
    assert (found.night, found.status) == (NIGHT, NightStatus.RUNNING.value)
    assert found.taken == 3
    assert waiting == ["never.example", "partial.example"]  # одного уже отдали обходчику
    assert found.plan is not None
    assert found.plan["ranks"] == {"limit": 1, "no_start": 1, "partial": 1}
    assert found.plan["workers"] == 1
    assert found.plan["left_out"] == 0


async def test_midnight_is_moscow_and_the_day_before_is_not_it() -> None:
    """За секунду до полуночи по Москве идёт ещё прошлая ночь: новый круг не начинается."""
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            session.add(CrawlNightModel(night=date(2026, 11, 2), status="done"))
            await session.commit()

        await _feed(factory, MIDNIGHT - timedelta(seconds=1))

        assert len(await _rounds(factory)) == 1

        await _feed(factory, MIDNIGHT)

        assert sorted(row.night for row in await _rounds(factory)) == [date(2026, 11, 2), NIGHT]


async def test_switched_off_the_round_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawl_cfg, "NIGHT_ROUND_ENABLED", False)
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            await session.commit()

        await _feed(factory)

        assert await _rounds(factory) == []
        assert await _waiting(factory) == []


async def test_the_second_pass_of_the_night_starts_nothing() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            await session.commit()

        await _tick(factory)
        await _tick(factory, MIDNIGHT + timedelta(minutes=1))

        assert len(await _rounds(factory)) == 1
        assert await _waiting(factory) == ["one.example"]


async def test_a_restart_at_half_past_midnight_still_starts_the_round() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            await session.commit()

        await _tick(factory, MIDNIGHT + timedelta(minutes=30))

        [found] = await _rounds(factory)
    assert found.status == NightStatus.RUNNING.value


async def test_two_o_clock_is_too_late_and_the_alarm_says_so(alerts: list[str]) -> None:
    """Круг шёл прошлой ночью, а этой нет — процесс лежал: об этом тревога, один раз."""
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            session.add(CrawlNightModel(night=date(2026, 11, 2), status="done"))
            await session.commit()

        await _feed(factory, MIDNIGHT + timedelta(hours=2))
        await _feed(factory, MIDNIGHT + timedelta(hours=2, minutes=1))

        rounds = {row.night: row for row in await _rounds(factory)}
        waiting = await _waiting(factory)
    assert rounds[NIGHT].status == NightStatus.MISSED.value
    assert rounds[NIGHT].note == "процесс не работал до 02:00"
    assert waiting == []
    assert len(alerts) == 1
    assert "пропущен" in alerts[0]


async def test_switched_on_by_day_it_is_quiet_until_the_next_midnight(alerts: list[str]) -> None:
    """Включили днём: пропускать было нечего, и тревога была бы ложной."""
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            await session.commit()

        await _feed(factory, MIDNIGHT + timedelta(hours=15))

        [found] = await _rounds(factory)
    assert found.status == NightStatus.MISSED.value
    assert found.note == "круг включён после окна начала"
    assert alerts == []


async def test_two_processes_start_one_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """Гонка на настоящих фиксациях: оба дошли до захвата разом, строка и доноры — одни."""
    meet = asyncio.Barrier(2)
    real = night._claim
    added: list[int] = []

    async def claim(session: AsyncSession, day: date, moment: datetime) -> int | None:
        await meet.wait()
        return await real(session, day, moment)

    async def add(session: AsyncSession, hosts: Any, **kwargs: Any) -> int:
        added.append(len(hosts))
        return await backlog.add(session, hosts, **kwargs)

    monkeypatch.setattr(night, "_claim", claim)
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            await session.commit()

        async def tick() -> None:
            async with factory() as session:
                await night.tick(session, workers=8, per_minute=2, add=add, now=MIDNIGHT)

        await asyncio.gather(tick(), tick())

        assert len(await _rounds(factory)) == 1
        assert added == [1]
        assert await _waiting(factory) == ["one.example"]


# --- расчёт на базе ---------------------------------------------------------------------------


async def test_the_plan_leaves_out_who_goes_who_waits_and_who_rests() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            for host in ("going.example", "waits.example", "rests.example", "fits.example"):
                await _priced(session, host)
            session.add(_crawled("going.example", pages=0, stop=None, status=CrawlStatus.RUNNING))
            session.add(_crawled("rests.example", pages=9000, max_pages=10_000, days=3))
            await backlog.add(session, ["waits.example"], by=WHO, author_id=None)
            await session.commit()

        await _tick(factory)

        [found] = await _rounds(factory)
        waiting = await _waiting(factory)
    assert waiting == ["waits.example", "fits.example"]
    assert found.plan is not None
    assert (found.plan["donors"], found.plan["rested"], found.plan["ahead"]) == (1, 1, 1)


async def test_the_measure_comes_from_real_crawls_and_not_a_constant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            for number in range(10):
                session.add(
                    _crawled(f"old{number}.example", pages=900, seconds=600.0 * (number + 1))
                )
            await session.commit()

        await _tick(factory)

        [found] = await _rounds(factory)
    assert found.plan is not None
    assert found.plan["typical_sec"] == 3_300
    assert found.plan["longest_sec"] == 5_400
    assert found.issue_until == MIDNIGHT + timedelta(seconds=25_200 - 5_400)


async def test_held_crawlers_and_deep_crawls_are_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(night, "crawlers_held", lambda: Held(3, "занято"))
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            session.add(
                _crawled(
                    "deep.example",
                    pages=10,
                    stop=None,
                    status=CrawlStatus.RUNNING,
                    max_pages=10_000,
                )
            )
            await session.commit()

        await _feed(factory, workers=16)

        [found] = await _rounds(factory)
    assert found.plan is not None
    assert (found.plan["deep"], found.plan["held"], found.plan["slots"]) == (1, 3, 12)


async def test_when_redis_does_not_answer_nobody_is_held(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(night, "crawlers_held", lambda: None)
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            await session.commit()

        await _tick(factory)

        [found] = await _rounds(factory)
    assert found.plan is not None
    assert found.plan["held"] == 0


async def test_power_for_nobody_ends_the_round_at_once_and_says_who_was_left_out(
    monkeypatch: pytest.MonkeyPatch, alerts: list[str]
) -> None:
    monkeypatch.setattr(crawl_cfg, "NIGHT_WINDOW_SEC", 60.0)
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            await session.commit()

        await _feed(factory)

        [found] = await _rounds(factory)
        waiting = await _waiting(factory)
    assert found.status == NightStatus.DONE.value
    assert found.taken == 0
    assert found.plan is not None
    assert (found.plan["capacity"], found.plan["left_out"]) == (0, 1)
    assert waiting == []
    assert alerts == []  # мощности нет — не поломка, и утренняя строка это скажет


async def test_an_empty_base_ends_the_round_at_once() -> None:
    async with committed_sessions() as factory:
        await _feed(factory)

        [found] = await _rounds(factory)
    assert found.status == NightStatus.DONE.value
    assert found.plan is not None
    assert found.plan["donors"] == 0


async def test_who_does_not_fit_waits_for_the_next_night(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crawl_cfg, "NIGHT_WINDOW_SEC", 3_000.0)
    async with committed_sessions() as factory:
        async with factory() as session:
            for number in range(3):
                await _priced(session, f"site{number}.example")
            await session.commit()

        await _feed(factory, workers=1)  # 1 500 с на выдачу и 1 500 на обход — один донор

        [found] = await _rounds(factory)
    assert found.taken == 1
    assert found.plan is not None
    assert found.plan["left_out"] == 2


async def test_a_failed_plan_is_said_once_and_not_repeated_every_minute(
    monkeypatch: pytest.MonkeyPatch, alerts: list[str]
) -> None:
    async def broken(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("расчёт сломан")

    monkeypatch.setattr(night, "plan", broken)
    async with committed_sessions() as factory:
        await _feed(factory)
        await _feed(factory, MIDNIGHT + timedelta(minutes=1))

        [found] = await _rounds(factory)
    assert found.status == NightStatus.FAILED.value
    assert found.note is not None
    assert "расчёт сломан" in found.note
    assert len(alerts) == 1
    assert "не начался" in alerts[0]


async def test_a_claim_without_a_plan_closes_as_failed_after_a_while() -> None:
    """Процесс упал между захватом и расчётом: круг не висит «идёт» навсегда."""
    async with committed_sessions() as factory:
        async with factory() as session:
            session.add(
                CrawlNightModel(
                    night=NIGHT,
                    status=NightStatus.RUNNING.value,
                    started_at=MIDNIGHT,
                    issue_until=MIDNIGHT + timedelta(hours=7),
                )
            )
            await session.commit()

        await _feed(factory, MIDNIGHT + timedelta(minutes=5))
        assert (await _rounds(factory))[0].status == NightStatus.RUNNING.value

        await _feed(factory, MIDNIGHT + timedelta(minutes=11))

        [found] = await _rounds(factory)
    assert found.status == NightStatus.FAILED.value


# --- ход и конец круга ------------------------------------------------------------------------


async def _started(factory: async_sessionmaker[AsyncSession], hosts: list[str]) -> CrawlNightModel:
    async with factory() as session:
        for host in hosts:
            await _priced(session, host)
        await session.commit()
    await _tick(factory)
    [found] = await _rounds(factory)
    return found


async def test_after_the_term_nothing_is_given_and_what_is_left_is_recorded() -> None:
    async with committed_sessions() as factory:
        found = await _started(factory, ["one.example", "two.example", "three.example"])
        assert found.issue_until is not None

        given = await _feed(factory, found.issue_until - timedelta(minutes=1), workers=1)
        late = await _feed(factory, found.issue_until, workers=16)

        [closed] = await _rounds(factory)
        waiting = await _waiting(factory)
    assert len(given.queued) == 1
    assert not late.queued
    assert waiting == []
    assert closed.dropped == 2
    assert closed.status == NightStatus.RUNNING.value  # начатый обход ещё идёт


async def test_the_round_ends_when_its_last_crawl_ends() -> None:
    async with committed_sessions() as factory:
        found = await _started(factory, ["one.example"])
        assert found.issue_until is not None
        await _feed(factory, MIDNIGHT + timedelta(minutes=1))

        await _feed(factory, found.issue_until + timedelta(minutes=1))
        assert (await _rounds(factory))[0].status == NightStatus.RUNNING.value

        async with factory() as session:
            run = await session.scalar(select(CrawlRunModel))
            assert run is not None
            run.status, run.outcome = CrawlStatus.DONE, CrawlOutcome.OK
            run.created_at = MIDNIGHT + timedelta(minutes=1)  # час круга, а не часы теста
            await session.commit()
        await _feed(factory, found.issue_until + timedelta(minutes=2))

        [closed] = await _rounds(factory)
    assert closed.status == NightStatus.DONE.value
    assert (closed.given, closed.dropped) == (1, 0)
    assert closed.finished_at == found.issue_until + timedelta(minutes=2)


async def test_a_crawl_of_the_round_goes_by_the_name_of_the_round() -> None:
    async with committed_sessions() as factory:
        await _started(factory, ["one.example"])

        await _feed(factory, MIDNIGHT + timedelta(minutes=1))

        async with factory() as session:
            run = await session.scalar(select(CrawlRunModel))
    assert run is not None
    assert run.requested_by == night.NIGHT_BY


async def test_people_go_before_the_round() -> None:
    async with committed_sessions() as factory:
        await _started(factory, ["night.example"])
        async with factory() as session:
            await _priced(session, "loaded.example")
            await backlog.add(session, ["loaded.example"], by=WHO, author_id=None)
            await session.commit()

        fed = await _feed(factory, MIDNIGHT + timedelta(minutes=1), workers=1)

    assert list(fed.queued) == ["loaded.example"]


async def test_a_deep_press_takes_the_row_of_the_round_and_the_term_leaves_it() -> None:
    """Человек велел обойти донора заново: срок круга этого не снимает."""
    async with committed_sessions() as factory:
        found = await _started(factory, ["one.example"])
        assert found.issue_until is not None
        async with factory() as session:
            added = await backlog.add(
                session, ["one.example"], by=WHO, author_id=None, max_pages=10_000
            )
            await session.commit()

        await _tick(factory, found.issue_until)

        async with factory() as session:
            row = await session.scalar(select(CrawlBacklogModel))
        [closed] = await _rounds(factory)
    assert added == 1
    assert row is not None
    assert (row.requested_by, row.max_pages) == (WHO, 10_000)
    assert closed.dropped == 0


async def test_a_load_takes_the_row_of_the_round_too() -> None:
    async with committed_sessions() as factory:
        found = await _started(factory, ["one.example"])
        assert found.issue_until is not None
        async with factory() as session:
            await backlog.add(session, ["one.example"], by=WHO, author_id=None)
            await session.commit()

        await _tick(factory, found.issue_until)

        assert await _waiting(factory) == ["one.example"]


async def test_the_round_does_not_take_a_row_it_did_not_put(session: AsyncSession) -> None:
    await backlog.add(session, ["one.example"], by=WHO, author_id=None)

    assert await backlog.add(session, ["one.example"], by=night.NIGHT_BY, author_id=None) == 0

    row = await session.scalar(select(CrawlBacklogModel))
    assert row is not None
    assert row.requested_by == WHO


async def test_switched_off_mid_round_the_round_still_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with committed_sessions() as factory:
        found = await _started(factory, ["one.example"])
        assert found.issue_until is not None
        monkeypatch.setattr(crawl_cfg, "NIGHT_ROUND_ENABLED", False)

        await _tick(factory, found.issue_until)

        [closed] = await _rounds(factory)
    assert closed.status == NightStatus.DONE.value
    assert closed.dropped == 1


# --- экран ------------------------------------------------------------------------------------


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


async def test_the_tab_sees_that_the_round_is_switched_off(
    client: AsyncClient, operator: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(crawl_cfg, "NIGHT_ROUND_ENABLED", False)

    answer = await client.get("/api/crawls/night", headers=bearer(operator))

    assert answer.status_code == 200
    assert answer.json() == {"enabled": False, "window_sec": 25_200.0, "last": None}


async def test_the_tab_sees_the_last_round_with_its_plan(
    client: AsyncClient, session: AsyncSession, operator: str
) -> None:
    session.add(
        CrawlNightModel(
            night=NIGHT,
            status=NightStatus.DONE.value,
            started_at=MIDNIGHT,
            taken=120,
            given=118,
            dropped=2,
            plan={
                "workers": 16,
                "slots": 12,
                "typical_sec": 1_740,
                "longest_sec": 3_600,
                "estimate_sec": 21_000,
                "donors": 340,
                "left_out": 220,
                "rested": 3,
                "ranks": {"limit": 80, "no_start": 30, "partial": 10},
            },
        )
    )
    session.add(CrawlNightModel(night=date(2026, 11, 2), status=NightStatus.DONE.value, taken=1))
    await session.commit()

    answer = await client.get("/api/crawls/night", headers=bearer(operator))

    last = answer.json()["last"]
    assert last["night"] == "2026-11-03"
    assert (last["taken"], last["given"], last["dropped"]) == (120, 118, 2)
    assert (last["left_out"], last["rested"], last["slots"]) == (220, 3, 12)
    assert last["ranks"] == {"limit": 80, "no_start": 30, "partial": 10}


async def test_a_going_round_is_counted_live(
    client: AsyncClient, session: AsyncSession, operator: str
) -> None:
    session.add(
        CrawlNightModel(
            night=NIGHT, status=NightStatus.RUNNING.value, started_at=MIDNIGHT, taken=2, plan={}
        )
    )
    session.add(
        CrawlRunModel(
            host="one.example",
            status=CrawlStatus.RUNNING,
            requested_by=night.NIGHT_BY,
            created_at=MIDNIGHT + timedelta(minutes=1),
        )
    )
    await backlog.add(session, ["two.example"], by=night.NIGHT_BY, author_id=None)
    await session.commit()

    answer = await client.get("/api/crawls/night", headers=bearer(operator))

    last = answer.json()["last"]
    assert (last["given"], last["going"], last["waiting"]) == (1, 1, 1)


async def test_without_a_pass_the_round_is_not_shown(client: AsyncClient) -> None:
    assert (await client.get("/api/crawls/night")).status_code == 403


def test_the_tab_route_exists_under_the_crawls() -> None:
    assert any(route.path == "/crawls/night" for route in routes.router.routes)


# --- ревизия ----------------------------------------------------------------------------------


def _table_down_and_up(connection: Connection) -> tuple[bool, bool]:
    migration = load_migration("e3b94f10d7a2_crawl_nights.py")
    present = "SELECT to_regclass('crawl_nights') IS NOT NULL"
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        gone = bool(connection.execute(text(present)).scalar())
        migration.upgrade()
    return gone, bool(connection.execute(text(present)).scalar())


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    assert await connection.run_sync(_table_down_and_up) == (False, True)
