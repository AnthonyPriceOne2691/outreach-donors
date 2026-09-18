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

Схему поднимает **`alembic upgrade head`, а не `create_all`**. Разница не
стилистическая: `create_all` строит базу по моделям, то есть проверяет модели
сами собой, а в прод едет цепочка миграций — единственный шаг развёртывания,
который до этого не исполнялся ни разу. Расхождение между моделями и цепочкой
при `create_all` невидимо: тесты зелёные, а `upgrade head` на чистой базе даёт
не ту схему. Поэтому здесь ровно та команда, что и в проде, и на пустой схеме.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from backend.features.core import models  # noqa: F401  — регистрирует таблицы
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_ROOT = Path(__file__).resolve().parent.parent

TEST_DSN = os.getenv(
    "TEST_STORAGE_DSN",
    "postgresql+asyncpg://outreach:outreach@localhost:5442/outreach_test",
)


async def _empty_the_database() -> None:
    """Снести схему целиком, включая `alembic_version`.

    `drop_all` по моделям оставил бы таблицы, которых в моделях уже нет, и
    отметку о последней миграции — то есть база была бы не чистой, а
    «как сложилось». Ровно этот класс прячет миграцию, которая не доезжает
    с нуля, но проходит на дев-базе.
    """
    engine = create_async_engine(TEST_DSN, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
    finally:
        await engine.dispose()


def _migrate_to_head() -> None:
    """Та же команда, что и в проде, тем же способом получения адреса базы."""
    env = {**os.environ, "STORAGE_DSN": TEST_DSN}
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,  # разбираем код сами: нужно своё сообщение, а не CalledProcessError
    )
    if result.returncode != 0:
        raise RuntimeError(
            "alembic upgrade head не прошёл на чистой базе — это отказ развёртывания, "
            f"а не теста.\n{result.stdout}\n{result.stderr}"
        )


@pytest.fixture(scope="session", autouse=True)
def _schema() -> None:
    """Схема создаётся один раз на прогон, в своём цикле событий."""
    asyncio.run(_empty_the_database())
    _migrate_to_head()


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
