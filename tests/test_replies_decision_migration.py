"""Решение человека у ответа — колонка `replies.decision` (25.11.2026): «лид в отказе».

Что пишет «В отказ» и как его видят переписка, CRM и выгрузка — `test_lead_refusal.py`; здесь —
ревизия колонки.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up

REVISION = "e9cc40d23f04_replies_decision.py"


async def test_the_column_goes_down_and_comes_back(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up = await connection.run_sync(
        lambda sync: columns_down_and_up(sync, REVISION, "replies", {"decision"})
    )

    assert (down, up) == (set(), {"decision"})
