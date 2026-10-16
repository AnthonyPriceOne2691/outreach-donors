"""Ревизия кэша помощника описания (`e14b9ba7f441`) — вниз и вверх на базе теста.

Откат снимает только кэш: следующий вызов помощника спросит модель снова. Подъём строит
таблицу заново — те же столбцы, что у модели, пустые списки по умолчанию и ключ (версия
промпта, модель, отпечаток черновика), на котором держится «тот же черновик второй раз не
оплачивается».
"""

from __future__ import annotations

from typing import Any

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.sales.segment_models import SalesSegmentHelpModel
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration

REVISION = "e14b9ba7f441_sales_segment_helps.py"
TABLE = SalesSegmentHelpModel.__tablename__
KEY = "uq_sales_segment_helps_key"


def _shape(connection: Connection) -> tuple[set[str], set[str]]:
    """Столбцы таблицы и её уникальные ключи; таблицы нет — оба пустые."""
    columns = connection.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name = :table"),
        {"table": TABLE},
    )
    keys = connection.execute(
        text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = to_regclass(:table) AND contype = 'u'"
        ),
        {"table": TABLE},
    )
    return set(columns.scalars()), set(keys.scalars())


def _down_and_up(connection: Connection) -> tuple[Any, Any, tuple[Any, ...]]:
    migration = load_migration(REVISION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = _shape(connection)
        migration.upgrade()
    row = connection.execute(
        text(
            "INSERT INTO sales_segment_helps "
            "(prompt_version, model, draft_digest, draft, description) "
            "VALUES ('v-test', 'model-test', 'digest-test', 'draft', 'description') "
            "RETURNING unclear, problems, tokens"
        )
    ).one()
    return down, _shape(connection), tuple(row)


async def test_rollback_drops_the_cache_and_upgrade_builds_it_as_the_model(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    down, up, defaults = await connection.run_sync(_down_and_up)

    assert down == (set(), set())
    assert up == (set(SalesSegmentHelpModel.__table__.columns.keys()), {KEY})
    assert defaults == ([], [], 0)
