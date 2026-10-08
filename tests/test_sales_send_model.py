"""Явная связь диалога продаж с лидом на настоящей базе — срез 4.6b, T1.

Один диалог на лида: второе первое письмо тому же человеку база не пустит. Диалог
уходит — связь с ним (уходит только липовая переписка); лида и гипотезу с диалогом
удалить нельзя. Ревизия гоняется вниз и вверх на базе теста — в процессе: подъём
сьюта идёт подпроцессом, и покрытие его не видит.
"""

from __future__ import annotations

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.features.sales.models import (
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from sqlalchemy import Connection, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration
from tests.test_sales_handoff_rows import sales_dialog

MIGRATION = "fa927869a835_sales_threads.py"
VERSION = "chain-3f1c9a07b2d4"


def _present(connection: Connection) -> bool:
    found = connection.execute(text("SELECT to_regclass('public.sales_threads') IS NOT NULL"))
    return bool(found.scalar_one())


def _down_and_up(connection: Connection) -> tuple[bool, bool]:
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = _present(connection)
        migration.upgrade()
    return down, _present(connection)


async def test_downgrade_drops_the_table_and_upgrade_runs_again(session: AsyncSession) -> None:
    connection = await session.connection()
    assert await connection.run_sync(_down_and_up) == (False, True)


async def _link(session: AsyncSession, *, hypothesis_id: int | None = None) -> SalesThreadModel:
    dialog = await sales_dialog(session)
    link = SalesThreadModel(
        thread_id=dialog.thread.id,
        lead_id=dialog.lead.id,
        chain_hypothesis_id=hypothesis_id,
        language="en",
        chain_version=VERSION,
    )
    session.add(link)
    await session.flush()
    return link


async def test_link_keeps_the_lead_the_chain_set_and_its_version(session: AsyncSession) -> None:
    link = await _link(session)
    found = await session.scalar(select(SalesThreadModel))
    assert found is not None
    assert (found.lead_id, found.chain_hypothesis_id, found.language, found.chain_version) == (
        link.lead_id,
        None,
        "en",
        VERSION,
    )


async def test_second_dialog_of_one_lead_is_refused(session: AsyncSession) -> None:
    link = await _link(session)
    other = await sales_dialog(session, host="other.example.test", email="olga@other.example.test")
    with pytest.raises(IntegrityError, match="uq_sales_threads_lead"):
        async with session.begin_nested():
            session.add(
                SalesThreadModel(
                    thread_id=other.thread.id,
                    lead_id=link.lead_id,
                    language="en",
                    chain_version=VERSION,
                )
            )


async def test_dialog_goes_and_its_link_goes_with_it(session: AsyncSession) -> None:
    link = await _link(session)
    await session.execute(text("DELETE FROM threads WHERE id = :id"), {"id": link.thread_id})
    session.expunge_all()
    assert await session.scalar(select(SalesThreadModel.thread_id)) is None


async def test_lead_with_a_dialog_cannot_be_deleted(session: AsyncSession) -> None:
    link = await _link(session)
    session.expunge_all()
    with pytest.raises(IntegrityError, match="sales_threads"):
        async with session.begin_nested():
            await session.execute(delete(SalesLeadModel).where(SalesLeadModel.id == link.lead_id))


async def test_hypothesis_of_the_chain_set_cannot_be_deleted(session: AsyncSession) -> None:
    hypothesis = SalesHypothesisModel(name="набор цепочки")
    session.add(hypothesis)
    await session.flush()
    await _link(session, hypothesis_id=hypothesis.id)
    session.expunge_all()
    with pytest.raises(IntegrityError, match="sales_threads"):
        async with session.begin_nested():
            await session.execute(
                delete(SalesHypothesisModel).where(SalesHypothesisModel.id == hypothesis.id)
            )
