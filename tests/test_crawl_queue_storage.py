"""Обход задачей очереди — в базе: строка до задачи, пачки по ходу, исход в конце.

Против настоящей базы: замок «один обход донора за раз» держит уникальный
индекс, и проверить его можно только там, где он живёт.
"""

from __future__ import annotations

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.crawl.links import OutLink
from backend.features.crawl.progress import Batch, Checkpoint
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.repository import (
    CrawlBusyError,
    finish_crawl,
    queue_crawl,
    save_batch,
)
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration

pytestmark = pytest.mark.asyncio

HOST = "donor.example.test"
MIGRATION = "fc721be3d031_crawl_run_lifecycle.py"


def _link(root: str, *, in_body: bool = True) -> OutLink:
    return OutLink(
        page_url=f"https://{HOST}/post",
        url=f"https://{root}/offer",
        target_host=root,
        target_root=root,
        anchor="оффер",
        anchor_key="оффер",
        nofollow=False,
        sponsored=False,
        ugc=False,
        in_body=in_body,
    )


def _batch(*roots: str, pages: int) -> Batch:
    point = Checkpoint(
        source="sitemap", pages=[f"https://{HOST}/p{n}" for n in range(pages)], articles=pages
    )
    return Batch(links=[_link(root) for root in roots], checkpoint=point)


async def test_queued_crawl_has_no_outcome_yet(session: AsyncSession) -> None:
    run = await queue_crawl(session, "WWW.Donor.Example.Test", by="op@t.test")

    assert run.host == HOST
    assert run.status is CrawlStatus.QUEUED
    assert (run.outcome, run.stop_reason) == (None, None)
    assert run.requested_by == "op@t.test"
    assert run.resumes == 0


async def test_one_unfinished_crawl_per_donor(session: AsyncSession) -> None:
    """Вторая кнопка поверх идущего обхода не заводит второй обход —
    это держит база, а не проверка в коде перед записью."""
    first = await queue_crawl(session, HOST, by=None)

    with pytest.raises(CrawlBusyError, match="уже идёт"):
        await queue_crawl(session, HOST, by=None)

    first.status = CrawlStatus.DONE
    await session.flush()
    again = await queue_crawl(session, HOST, by=None)
    other = await queue_crawl(session, "other.example.test", by=None)
    assert again.id != first.id
    assert other.status is CrawlStatus.QUEUED


async def test_batches_land_with_their_checkpoint(session: AsyncSession) -> None:
    run = await queue_crawl(session, HOST, by=None)

    await save_batch(session, run.id, _batch("a.com", "b.com", pages=25))
    await save_batch(session, run.id, _batch("c.com", pages=40))
    await session.refresh(run)

    links = (await session.execute(select(func.count()).select_from(OutLinkModel))).scalar_one()
    assert links == 3
    assert run.pages_opened == 40
    assert run.articles == 40
    assert Checkpoint.of(run.checkpoint) == _batch(pages=40).checkpoint


async def test_finish_counts_links_from_the_base_not_from_memory(session: AsyncSession) -> None:
    """После продолжения у задачи в памяти только её часть ссылок — числа
    обхода берутся по базе, иначе обход выглядел бы меньше, чем был."""
    run = await queue_crawl(session, HOST, by=None)
    run.resumes = 1
    await save_batch(session, run.id, _batch("a.com", "b.com", pages=10))
    report = CrawlReport(
        host=HOST,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages=[f"https://{HOST}/p{n}" for n in range(12)],
        links=[_link("c.com", in_body=False)],
        articles=11,
    )
    await save_batch(session, run.id, Batch(links=report.links, checkpoint=Checkpoint("sitemap")))

    stats = await finish_crawl(session, run, report)

    assert run.status is CrawlStatus.DONE
    assert (run.outcome, run.stop_reason) == (CrawlOutcome.OK, StopReason.EXHAUSTED)
    assert run.checkpoint is None
    # Стёртый чекпоинт — пустое поле, а не JSON `null`: иначе `IS NULL` врёт.
    cleared = await session.scalar(
        select(CrawlRunModel.id).where(CrawlRunModel.checkpoint.is_(None))
    )
    assert cleared == run.id
    assert run.finished_at is not None
    assert (run.pages_opened, run.articles) == (12, 11)
    assert stats["links_found"] == 3
    assert stats["advertisers"] == 3
    assert stats["links_in_body"] == 2
    assert stats["roots_guessed"] == 0
    assert stats["resumes"] == 1


def _round_trip(connection: Connection) -> tuple[list[str], list[str], str]:
    """Ревизия вниз и вверх; незаконченный обход переживает спуск с исходом."""
    connection.execute(
        text(
            "INSERT INTO crawl_runs (host, status, pages_opened, articles) "
            "VALUES ('queued.example.test', 'queued', 0, 0)"
        )
    )
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = _shape(connection)
        outcome = connection.execute(
            text("SELECT outcome FROM crawl_runs WHERE host = 'queued.example.test'")
        ).scalar_one()
        migration.upgrade()
    return down, _shape(connection), outcome


def _shape(connection: Connection) -> list[str]:
    columns = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'crawl_runs' "
            "AND column_name IN ('status', 'job_id', 'requested_by', 'resumes', 'checkpoint')"
        )
    ).scalars()
    index = connection.execute(
        text("SELECT indexname FROM pg_indexes WHERE indexname = 'uq_crawl_runs_active_host'")
    ).scalars()
    return sorted([*columns, *index])


async def test_migration_goes_down_and_up(session: AsyncSession) -> None:
    """Ревизия, которую выкатка применит к проду, — вниз и вверх на тестовой базе."""
    connection = await session.connection()

    down, up, outcome = await connection.run_sync(_round_trip)

    assert down == []
    assert up == [
        "checkpoint",
        "job_id",
        "requested_by",
        "resumes",
        "status",
        "uq_crawl_runs_active_host",
    ]
    assert outcome == "failed"
    status = await session.scalar(
        select(CrawlRunModel.status).where(CrawlRunModel.host == "queued.example.test")
    )
    assert status is CrawlStatus.DONE
