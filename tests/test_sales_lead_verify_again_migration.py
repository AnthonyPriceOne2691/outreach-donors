"""Ревизия срока и окна «ещё проверяет» у лида (`b6440cd714cc`) — вниз и вверх на базе теста.

Откат снимает обе колонки и индекс: отложенная перепроверка прежнему коду неизвестна, лид
остаётся «новым» со словами и ждёт очистки, как до неё. Подъём возвращает их такими, как у
модели: оба момента — пустые у уже заведённых лидов, а индекс по сроку — частичный (ревью 20.10:
задача перепроверки ищет лидов по сроку, а срок есть у единиц из многих).
"""

from __future__ import annotations

from typing import Any

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.sales.models import SalesLeadModel
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up, load_migration

REVISION = "b6440cd714cc_sales_lead_verify_again.py"
COLUMNS = {"verify_since", "verify_again_at"}
INDEX = "idx_sales_leads_verify_again"


def _index(connection: Connection) -> str | None:
    return connection.execute(
        text("SELECT indexdef FROM pg_indexes WHERE tablename = 'sales_leads' AND indexname = :i"),
        {"i": INDEX},
    ).scalar()


def _down_and_up(connection: Connection) -> tuple[set[str], set[str], Any, Any]:
    down, up = columns_down_and_up(connection, REVISION, SalesLeadModel.__tablename__, COLUMNS)
    nullable = connection.execute(
        text(
            "SELECT column_name, column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'sales_leads' AND column_name = ANY(:names) ORDER BY column_name"
        ),
        {"names": sorted(COLUMNS)},
    ).all()
    return down, up, [tuple(row) for row in nullable], _index(connection)


async def test_rollback_drops_the_term_and_the_window_and_upgrade_brings_them_back(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    down, up, columns, index = await connection.run_sync(_down_and_up)

    assert down == set()
    assert up == COLUMNS
    assert columns == [("verify_again_at", None, "YES"), ("verify_since", None, "YES")]
    assert index is not None
    assert index.endswith("(verify_again_at) WHERE (verify_again_at IS NOT NULL)")
    assert set(SalesLeadModel.__table__.columns.keys()) >= COLUMNS
    assert INDEX in {found.name for found in SalesLeadModel.__table__.indexes}


async def test_rollback_drops_the_index_too(session: AsyncSession) -> None:
    def down(connection: Connection) -> str | None:
        migration = load_migration(REVISION)
        with Operations.context(MigrationContext.configure(connection)):
            migration.downgrade()
            gone = _index(connection)
            migration.upgrade()
        return gone

    connection = await session.connection()
    assert await connection.run_sync(down) is None
