"""Сессии с настоящими фиксациями и чистка базы после них — одна на все такие тесты.

Тест обрыва или гонки двух соединений фиксирует по-настоящему, и внешняя транзакция фикстуры
`session` его строк не откатит: после него база вычищается целиком — остальные тесты ждут
пустые таблицы. Раньше чистку несли четыре копии одного кода.

**Журнал действий чистится в обход своего замка** (аудит 20.11.2026, ревизия `d4f8a2c61e93`):
TRUNCATE `audit_log` — отказ триггера, а `TRUNCATE … CASCADE` от `users` доходит и до журнала.
`SET LOCAL session_replication_role = replica` не зовёт обычные триггеры до конца транзакции;
задать её может только суперпользователь — тестовая база ходит владельцем схемы, а роль
приложения так не может (`tests/test_db_app_role.py`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from tests.conftest import TEST_DSN


async def empty_all_tables(conn: AsyncConnection) -> None:
    """Все таблицы схемы `public`, кроме отметки alembic, — пустые, номера с единицы.

    `conn` — в транзакции: обход триггеров кончается вместе с ней."""
    tables = (
        await conn.execute(
            text(
                "SELECT tablename FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        )
    ).scalars()
    listed = ", ".join(f'"{table}"' for table in tables)
    await conn.execute(text("SET LOCAL session_replication_role = replica"))
    await conn.execute(text(f"TRUNCATE {listed} RESTART IDENTITY CASCADE"))


@asynccontextmanager
async def committed_sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Сессии на настоящих соединениях с настоящими фиксациями; после — пустая база."""
    engine = create_async_engine(TEST_DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with engine.begin() as conn:
            await empty_all_tables(conn)
        await engine.dispose()
