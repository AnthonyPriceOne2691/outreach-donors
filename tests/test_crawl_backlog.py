"""Очередь загрузки обхода (`crawl/backlog.py`) — на настоящей базе, очередь обходов подменена.

Проверяется то, ради чего модуль: загруженные встают в очередь загрузки один раз; проход отдаёт
обходчикам ровно столько, сколько свободно, по порядку загрузки и той же постановкой, что кнопка;
донор, переставший подходить, строку теряет и не обходится; идущий обход второго не заводит;
очередь обходов, не ответившая, строку не теряет; снять очередь можно целиком, и это в журнале;
срок называется по последним обходам. Постановка фиксирует каждого донора сама — сессии
с настоящими фиксациями (`committed_sessions`).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.core.domain import AuditAction, CrawlOutcome, CrawlStatus
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.crawl import CrawlBacklogModel, CrawlRunModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl import backlog
from backend.workers import reaper
from sqlalchemy import select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import make_donor
from tests.migration_helpers import load_migration
from tests.test_send_race import committed_sessions

WHO = "anna@ours.example.test"


class _Enqueue:
    """Очередь обходов в памяти; `broken` — она не отвечает вовсе."""

    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.queued: list[int] = []

    def __call__(self, run_id: int, job_id: str) -> None:
        if self.broken:
            raise ConnectionError("Redis недоступен")
        self.queued.append(run_id)


def _ids(run_id: int) -> str:
    return f"crawl-{run_id}-test"


async def _priced(session: AsyncSession, host: str, *, days: int = 1) -> None:
    """Принятый годный донор с ценой `days` дней назад — цель обхода, пока цена свежа."""
    domain = await make_donor(session, host)
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    donor.last_price = Decimal("100.00")
    donor.last_price_at = datetime.now(UTC) - timedelta(days=days)


async def _waiting(factory: async_sessionmaker[AsyncSession]) -> list[str]:
    async with factory() as session:
        rows = await session.scalars(select(CrawlBacklogModel.host).order_by(CrawlBacklogModel.id))
        return list(rows.all())


async def _crawls(factory: async_sessionmaker[AsyncSession]) -> dict[str, CrawlStatus]:
    async with factory() as session:
        rows = (await session.execute(select(CrawlRunModel.host, CrawlRunModel.status))).tuples()
        return dict(rows.all())


async def test_a_host_waits_once_however_often_it_is_loaded(session: AsyncSession) -> None:
    assert (
        await backlog.add(
            session, ["one.example", "two.example", "one.example"], by=WHO, author_id=None
        )
        == 2
    )
    assert await backlog.add(session, ["two.example", "three.example"], by=WHO, author_id=None) == 1
    assert await backlog.waiting(session) == 3


async def test_the_pass_fills_only_free_crawlers_in_loading_order() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            for host in ("one.example", "two.example", "three.example", "busy.example"):
                await _priced(session, host)
            session.add(CrawlRunModel(host="busy.example", status=CrawlStatus.RUNNING))
            await backlog.add(
                session, ["one.example", "two.example", "three.example"], by=WHO, author_id=None
            )
            await session.commit()
        enqueue = _Enqueue()
        async with factory() as session:
            fed = await backlog.feed(session, workers=3, enqueue=enqueue, job_id=_ids)
            await session.commit()

        assert list(fed.queued) == ["one.example", "two.example"]
        assert len(enqueue.queued) == 2
        assert await _waiting(factory) == ["three.example"]
        crawls = await _crawls(factory)
        assert (crawls["one.example"], crawls["two.example"]) == (CrawlStatus.QUEUED,) * 2


async def test_no_free_crawler_means_nobody_is_queued() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            session.add(CrawlRunModel(host="other.example", status=CrawlStatus.QUEUED))
            await backlog.add(session, ["one.example"], by=WHO, author_id=None)
            await session.commit()
        async with factory() as session:
            fed = await backlog.feed(session, workers=1, enqueue=_Enqueue(), job_id=_ids)

        assert not fed
        assert await _waiting(factory) == ["one.example"]


async def test_a_donor_that_no_longer_fits_loses_its_row_and_is_not_crawled() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "stale.example", days=400)
            await make_donor(session, "unpriced.example")
            await _priced(session, "fine.example")
            await backlog.add(
                session,
                ["stale.example", "unpriced.example", "gone.example", "fine.example"],
                by=WHO,
                author_id=None,
            )
            await session.commit()
        async with factory() as session:
            fed = await backlog.feed(session, workers=4, enqueue=_Enqueue(), job_id=_ids)
            await session.commit()

        assert list(fed.queued) == ["fine.example"]
        assert fed.dropped == {
            "stale.example": "цена старше 150 дней",
            "unpriced.example": "цены нет",
            "gone.example": backlog._OTHER,
        }
        assert await _waiting(factory) == []
        assert set(await _crawls(factory)) == {"fine.example"}


async def test_a_donor_already_crawled_leaves_the_queue_without_a_second_crawl() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            session.add(CrawlRunModel(host="one.example", status=CrawlStatus.RUNNING))
            await backlog.add(session, ["one.example"], by=WHO, author_id=None)
            await session.commit()
        async with factory() as session:
            fed = await backlog.feed(session, workers=8, enqueue=_Enqueue(), job_id=_ids)
            await session.commit()

        assert (fed.queued, fed.busy) == ({}, ["one.example"])
        assert await _waiting(factory) == []


async def test_nobody_is_given_while_someone_waits_in_the_crawl_queue() -> None:
    """Ждущий в очереди обходов уходит первым: сторож нагрузки держит его — держит и выдачу."""
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            session.add(CrawlRunModel(host="human.example", status=CrawlStatus.QUEUED))
            await backlog.add(session, ["one.example"], by=WHO, author_id=None)
            await session.commit()
        async with factory() as session:
            fed = await backlog.feed(session, workers=16, enqueue=_Enqueue(), job_id=_ids)

        assert not fed
        assert await _waiting(factory) == ["one.example"]


async def test_a_pass_gives_no_more_than_its_share() -> None:
    async with committed_sessions() as factory:
        hosts = [f"d{i}.example" for i in range(5)]
        async with factory() as session:
            for host in hosts:
                await _priced(session, host)
            await backlog.add(session, hosts, by=WHO, author_id=None)
            await session.commit()
        async with factory() as session:
            fed = await backlog.feed(session, workers=16, enqueue=_Enqueue(), job_id=_ids)
            await session.commit()

        assert list(fed.queued) == hosts[: backlog.PER_PASS]
        assert await _waiting(factory) == hosts[backlog.PER_PASS :]


async def test_a_silent_crawl_queue_keeps_the_row_for_the_next_pass() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            await backlog.add(session, ["one.example"], by=WHO, author_id=None)
            await session.commit()
        async with factory() as session:
            fed = await backlog.feed(session, workers=2, enqueue=_Enqueue(broken=True), job_id=_ids)
            await session.commit()

        assert fed.failed == ["one.example"]
        assert await _waiting(factory) == ["one.example"]


async def test_queued_crawls_are_in_the_journal_on_behalf_of_the_loader() -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await _priced(session, "one.example")
            await backlog.add(session, ["one.example"], by=WHO, author_id=None)
            await session.commit()
        async with factory() as session:
            await backlog.feed(session, workers=2, enqueue=_Enqueue(), job_id=_ids)
            await session.commit()
        async with factory() as session:
            run = await session.scalar(select(CrawlRunModel))
            entry = await session.scalar(
                select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
            )

        assert run is not None
        assert run.requested_by == WHO
        assert entry is not None
        assert (entry.target, (entry.details or {}).get("как")) == (
            f"crawl:{run.id}",
            "очередь загрузки",
        )


async def test_dropping_the_queue_is_one_journal_entry(session: AsyncSession) -> None:
    await backlog.add(session, ["one.example", "two.example"], by=WHO, author_id=None)

    assert await backlog.drop_all(session, author_id=None) == 2
    assert await backlog.drop_all(session, author_id=None) == 0

    entries = (
        await session.scalars(
            select(AuditLogModel.details).where(
                AuditLogModel.action == AuditAction.CRAWL_BACKLOG_DROPPED
            )
        )
    ).all()
    assert [(entry or {}).get("снято доноров") for entry in entries] == [2]


def _done(host: str, elapsed: Any, outcome: CrawlOutcome = CrawlOutcome.OK) -> CrawlRunModel:
    return CrawlRunModel(
        host=host,
        status=CrawlStatus.DONE,
        outcome=outcome,
        finished_at=datetime.now(UTC),
        stats={"elapsed_sec": elapsed},
    )


async def test_the_term_is_the_median_of_recent_crawls(session: AsyncSession) -> None:
    session.add_all(
        [
            _done("a.example", 600),
            _done("b.example", 1200),
            _done("c.example", 6000),
            _done("d.example", 5, CrawlOutcome.BLOCKED),
            _done("e.example", None),
        ]
    )
    await session.flush()

    assert await backlog.eta_sec(session, 16, 8) == pytest.approx(2 * 1200)


async def test_without_recent_crawls_the_term_is_half_an_hour_a_donor(
    session: AsyncSession,
) -> None:
    assert await backlog.eta_sec(session, 8, 4) == pytest.approx(2 * backlog.TYPICAL_SEC)


@pytest.mark.parametrize("workers", [None, 0])
async def test_without_crawlers_there_is_no_term(
    session: AsyncSession, workers: int | None
) -> None:
    assert await backlog.eta_sec(session, 10, workers) is None


@pytest.mark.parametrize("workers", [None, 0])
async def test_the_reaper_pass_waits_while_nobody_crawls(
    monkeypatch: pytest.MonkeyPatch, workers: int | None
) -> None:
    called: list[int] = []

    async def feed(_session: AsyncSession, **kwargs: Any) -> backlog.Fed:
        called.append(kwargs["workers"])
        return backlog.Fed()

    monkeypatch.setattr(reaper, "workers_alive", lambda **_: workers)
    monkeypatch.setattr(backlog, "feed", feed)

    await reaper.feed_backlog()

    assert called == []


async def test_the_reaper_pass_feeds_the_crawlers_it_sees(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[int] = []

    async def feed(_session: AsyncSession, **kwargs: Any) -> backlog.Fed:
        called.append(kwargs["workers"])
        return backlog.Fed()

    monkeypatch.setattr(reaper, "workers_alive", lambda **_: 8)
    monkeypatch.setattr(backlog, "feed", feed)

    async with committed_sessions():
        await reaper.feed_backlog()

    assert called == [8]


# --- ревизия очереди загрузки ----------------------------------------------------------------


REVISION = "5649d5547a41_crawl_backlog.py"


def _table_down_and_up(connection: Connection) -> tuple[bool, bool]:
    """Ревизия очереди загрузки вниз и вверх на соединении теста: подъём сьюта идёт подпроцессом."""
    migration = load_migration(REVISION)
    present = "SELECT to_regclass('crawl_backlog') IS NOT NULL"
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        gone = bool(connection.execute(text(present)).scalar())
        migration.upgrade()
    return gone, bool(connection.execute(text(present)).scalar())


async def test_backlog_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    assert await connection.run_sync(_table_down_and_up) == (False, True)


def _journal_values(connection: Connection) -> list[str]:
    """Ревизия журнала ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие
    его не видит. `ADD VALUE IF NOT EXISTS` делает повтор безвредным."""
    migration = load_migration("409decc6a434_crawl_backlog_dropped_audit_action.py")
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()
    values = connection.execute(text("SELECT unnest(enum_range(NULL::auditaction))::text"))
    return list(values.scalars())


async def test_journal_value_is_there_once_and_survives_a_rerun(session: AsyncSession) -> None:
    connection = await session.connection()

    assert (await connection.run_sync(_journal_values)).count("crawl_backlog_dropped") == 1
