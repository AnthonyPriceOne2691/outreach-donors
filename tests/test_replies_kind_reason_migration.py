"""Почему ответ — автоответ — колонка `replies.kind_reason` (27.11.2026).

Что пишет приём и что видит переписка — `test_reply_kind_reason.py`; здесь — ревизия колонки.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up

REVISION = "1c887af8d105_replies_kind_reason.py"


async def test_the_column_goes_down_and_comes_back(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up = await connection.run_sync(
        lambda sync: columns_down_and_up(sync, REVISION, "replies", {"kind_reason"})
    )

    assert (down, up) == (set(), {"kind_reason"})
