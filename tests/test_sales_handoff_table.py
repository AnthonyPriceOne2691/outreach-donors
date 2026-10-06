"""Таблица передачи лида продаж на настоящей базе — срез 5.3, T1.

Ключ — диалог: вторая строка на тот же диалог не встаёт. Диалог уходит —
передача с ним (уходит только липовая переписка); лид с передачей удалить
нельзя — номер сделки в чужой CRM не теряется. Ревизия гоняется вниз и вверх
на базе теста: таблица не уносит с собой типы перечислений (урок L5).
"""

from __future__ import annotations

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.features.sales.models import (
    HandoffKommo,
    HandoffTelegram,
    SalesHandoffModel,
    SalesLeadModel,
)
from sqlalchemy import Connection, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration
from tests.sales_handoff_rows import sales_dialog

MIGRATION = "a9e76c0eb5b0_sales_handoffs.py"
SCHEMA = frozenset({"sales_handoffs", "sales_handoff_kommo", "sales_handoff_telegram"})


def _present(connection: Connection) -> set[str]:
    tables = connection.execute(
        text("SELECT tablename FROM pg_tables WHERE tablename = ANY(:names)"),
        {"names": sorted(SCHEMA)},
    )
    types = connection.execute(
        text("SELECT typname FROM pg_type WHERE typname = ANY(:names)"), {"names": sorted(SCHEMA)}
    )
    return set(tables.scalars()) | set(types.scalars())


def _down_and_up(connection: Connection) -> tuple[set[str], set[str]]:
    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = _present(connection)
        migration.upgrade()
    return down, _present(connection)


async def test_upgrade_head_creates_the_table_and_both_types(session: AsyncSession) -> None:
    connection = await session.connection()
    assert await connection.run_sync(_present) == SCHEMA


async def test_downgrade_drops_table_and_types_and_upgrade_runs_again(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    down, up = await connection.run_sync(_down_and_up)
    assert down == set()
    assert up == SCHEMA


async def test_new_handoff_waits_for_both_steps(session: AsyncSession) -> None:
    dialog = await sales_dialog(session)
    session.add(SalesHandoffModel(thread_id=dialog.thread.id, lead_id=dialog.lead.id))
    await session.flush()
    row = await session.scalar(select(SalesHandoffModel))
    assert row is not None
    assert (row.kommo, row.telegram, row.attempts) == (
        HandoffKommo.PENDING,
        HandoffTelegram.PENDING,
        0,
    )
    assert row.kommo_lead_id is None


async def test_second_handoff_of_one_dialog_is_refused(session: AsyncSession) -> None:
    dialog = await sales_dialog(session)
    session.add(SalesHandoffModel(thread_id=dialog.thread.id, lead_id=dialog.lead.id))
    await session.flush()
    with pytest.raises(IntegrityError, match="uq_sales_handoffs_thread"):
        async with session.begin_nested():
            session.add(SalesHandoffModel(thread_id=dialog.thread.id, lead_id=dialog.lead.id))


async def test_dialog_goes_and_its_handoff_goes_with_it(session: AsyncSession) -> None:
    dialog = await sales_dialog(session)
    session.add(SalesHandoffModel(thread_id=dialog.thread.id, lead_id=dialog.lead.id))
    await session.flush()
    await session.execute(text("DELETE FROM threads WHERE id = :id"), {"id": dialog.thread.id})
    assert await session.scalar(select(SalesHandoffModel.id)) is None


async def test_lead_with_a_handoff_cannot_be_deleted(session: AsyncSession) -> None:
    dialog = await sales_dialog(session)
    session.add(SalesHandoffModel(thread_id=dialog.thread.id, lead_id=dialog.lead.id))
    await session.flush()
    session.expunge_all()
    with pytest.raises(IntegrityError, match="sales_handoffs"):
        async with session.begin_nested():
            await session.execute(delete(SalesLeadModel).where(SalesLeadModel.id == dialog.lead.id))
