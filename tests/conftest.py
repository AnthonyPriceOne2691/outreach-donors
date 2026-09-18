"""Оснастка тестов, которым нужна настоящая база.

Ядро тестируется без сети и без сервера — так записано в конституции, и сотня
тестов идёт за две десятых секунды. Репозиторий исключение по существу:
проверять запрос к базе на подделке значит проверять подделку.

База настоящая, но отдельная — `outreach_test`. Каждый тест работает во внешней
транзакции, которая откатывается: тесты не видят следов друг друга и не зависят
от порядка.

Про циклы событий. Схема поднимается один раз собственным `asyncio.run`, а не
асинхронной фикстурой уровня сессии: соединение asyncpg привязано к тому циклу,
в котором создано, а каждому тесту достаётся свой. Движок поэтому тоже
пофункциональный.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator

import pytest
from backend.features.core import models  # noqa: F401  — регистрирует таблицы
from backend.shared.database.base import Base
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

TEST_DSN = os.getenv(
    "TEST_STORAGE_DSN",
    "postgresql+asyncpg://outreach:outreach@localhost:5442/outreach_test",
)


async def _recreate_schema() -> None:
    engine = create_async_engine(TEST_DSN)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    """Схема создаётся один раз на прогон, в своём цикле событий."""
    asyncio.run(_recreate_schema())


@pytest.fixture
async def engine() -> AsyncIterator[AsyncEngine]:
    eng = create_async_engine(TEST_DSN)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """Сессия во внешней транзакции. Тест может коммитить внутри — откатывается
    внешняя, и база остаётся чистой."""
    connection = await engine.connect()
    transaction = await connection.begin()
    factory = async_sessionmaker(bind=connection, expire_on_commit=False)
    async with factory() as s:
        yield s
    await transaction.rollback()
    await connection.close()
