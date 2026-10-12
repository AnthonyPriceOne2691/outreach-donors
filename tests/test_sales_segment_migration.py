"""Ревизия сбора базы (`ed31d48a1ec2`) — вниз и вверх на базе теста.

Откат переводит лидов сбора в `import`: значение `collected` из перечисления не убирается,
а старый код его не знает — список лидов на такой строке отдал бы 500.
"""

from __future__ import annotations

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.sales.models import LeadSource
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration
from tests.test_sales_segment_run import hypothesis
from tests.test_sales_send_world import lead

REVISION = "ed31d48a1ec2_sales_segment_collection.py"
TABLES = {"sales_segment_runs", "sales_segment_candidates", "sales_segment_verdicts"}


def _tables(connection: Connection) -> set[str]:
    rows = connection.execute(
        text("SELECT tablename FROM pg_tables WHERE tablename = ANY(:names)"),
        {"names": sorted(TABLES)},
    )
    return set(rows.scalars())


async def test_the_rollback_turns_collected_leads_into_imported_ones(
    session: AsyncSession,
) -> None:
    owner = await hypothesis(session)
    collected = await lead(
        session, owner.id, "jane@collected.example.test", source=LeadSource.COLLECTED
    )
    imported = await lead(session, owner.id, "john@imported.example.test")
    ids = [collected.id, imported.id]
    connection = await session.connection()

    def down_and_up(sync: Connection) -> tuple[dict[int, str], set[str], set[str]]:
        migration = load_migration(REVISION)
        with Operations.context(MigrationContext.configure(sync)):
            migration.downgrade()
            rows = sync.execute(
                text("SELECT id, source::text FROM sales_leads WHERE id = ANY(:ids)"), {"ids": ids}
            )
            sources = {row.id: row.source for row in rows}
            down = _tables(sync)
            migration.upgrade()
        return sources, down, _tables(sync)

    sources, down, up = await connection.run_sync(down_and_up)

    assert sources == {collected.id: "import", imported.id: "import"}
    assert (down, up) == (set(), TABLES)
