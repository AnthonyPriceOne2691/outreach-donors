"""Ревизия срока и счёта «ещё проверяет» у лида (`b6440cd714cc`) — вниз и вверх на базе теста.

Откат снимает обе колонки: отложенная перепроверка прежнему коду неизвестна, лид остаётся «новым»
со словами и ждёт очистки, как до неё. Подъём возвращает их такими, как у модели: счёт — ноль по
умолчанию у уже заведённых лидов, срок — пустой.
"""

from __future__ import annotations

from typing import Any

from backend.features.sales.models import SalesLeadModel
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up

REVISION = "b6440cd714cc_sales_lead_verify_again.py"
COLUMNS = {"verify_tries", "verify_again_at"}


def _down_and_up(connection: Connection) -> tuple[set[str], set[str], Any]:
    down, up = columns_down_and_up(connection, REVISION, SalesLeadModel.__tablename__, COLUMNS)
    default = connection.execute(
        text(
            "SELECT column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'sales_leads' AND column_name = 'verify_tries'"
        )
    ).one()
    return down, up, tuple(default)


async def test_rollback_drops_the_term_and_the_count_and_upgrade_brings_them_back(
    session: AsyncSession,
) -> None:
    connection = await session.connection()
    down, up, default = await connection.run_sync(_down_and_up)

    assert down == set()
    assert up == COLUMNS
    assert default == ("'0'::smallint", "NO")  # счёт уже заведённых лидов — ноль
    assert set(SalesLeadModel.__table__.columns.keys()) >= COLUMNS
