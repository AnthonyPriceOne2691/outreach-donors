"""Способ привязки ответа — у ответа в базе, а не только в журнале (24.11.2026).

Итог приёма называл способ (`Accepted.way`) только в журнале контейнера, а его стирает каждая
выкатка: доля привязок по отправителю не пережила первой же. Что пишется при приёме — проверяют
тесты привязки (`test_replies_by_sender.py`: метка, цепочка, отправитель, не привязан); здесь —
ревизия колонки.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up

REVISION = "ce69f6ea12a1_replies_bound_by.py"


async def test_the_column_goes_down_and_comes_back(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up = await connection.run_sync(
        lambda sync: columns_down_and_up(sync, REVISION, "replies", {"bound_by"})
    )

    assert (down, up) == (set(), {"bound_by"})
