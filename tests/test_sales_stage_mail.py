"""Этап продаж в общей почте, письма — срез 1.1b, часть «а».

Почта продажи ещё не ведёт. Каждая её ветка, где этап решает путь, обязана
отказать продажам словами, а не увести их путём доноров. Проверяется на базе,
где путь доноров дал бы результат: домен письма продаж — принятый донор
с адресом, ящики есть у обоих этапов. Отказ здесь виден по тому, чего в базе
нет: письма не ушло, рассылки не заведено, срок добивки не погашен.
"""

from __future__ import annotations

from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.features.core.stages import SalesNotConnectedError
from backend.features.runs.failures import is_permanent
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_model import ROOT, _migration

MIGRATION = ROOT / "backend/migrations/versions/39e342cb2b21_stage_sales.py"


# --- A1: значение типа ---------------------------------------------------------------


def _stage_values(connection: Connection) -> list[str]:
    """Ревизия ещё раз, в процессе: подъём сьюта идёт подпроцессом, и покрытие его
    не видит. `ADD VALUE IF NOT EXISTS` делает повтор безвредным."""
    migration = _migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.upgrade()
        migration.downgrade()
    return list(connection.execute(text("SELECT unnest(enum_range(NULL::stage))::text")).scalars())


async def test_a1_stage_type_has_sales_once_and_a_rerun_is_harmless(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    assert await connection.run_sync(_stage_values) == ["donors", "advertisers", "sales"]


def test_refusal_is_permanent_for_the_job_queue() -> None:
    """Повтор задачи не подключит продажи: итог «не выполнена», а не три попытки."""
    assert is_permanent(SalesNotConnectedError("Очередь писем не собрана"))
