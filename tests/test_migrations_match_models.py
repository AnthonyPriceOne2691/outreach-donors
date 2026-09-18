"""Цепочка миграций и модели обязаны сходиться.

Модели описывают, какую схему хочет видеть код. Миграции — единственный
способ, которым схема появляется в проде. Разойтись они могут молча: поле
добавили в модель и забыли миграцию, и всё зелёное, потому что тесты до
сих пор строили базу по моделям, а не по цепочке.

Проверка идёт от базы, поднятой `alembic upgrade head` (см. conftest):
alembic сравнивает её с моделями и говорит, что бы он дописал. Пусто —
значит цепочка доезжает ровно до того, что ждёт код.
"""

from __future__ import annotations

import asyncio

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from backend.features.core import models  # noqa: F401  — регистрирует таблицы
from backend.shared.database.base import Base
from sqlalchemy import Column, Connection, Integer, MetaData, Table
from sqlalchemy.ext.asyncio import create_async_engine
from tests.conftest import TEST_DSN

#: Служебная таблица alembic: в моделях её нет и быть не должно.
_IGNORED_TABLES = frozenset({"alembic_version"})


def _diff_against(connection: Connection, metadata: MetaData) -> list[object]:
    context = MigrationContext.configure(
        connection,
        opts={"compare_type": True, "compare_server_default": True},
    )
    found = compare_metadata(context, metadata)
    return [d for d in found if not _touches_ignored_table(d)]


def _touches_ignored_table(diff: object) -> bool:
    parts = diff if isinstance(diff, tuple) else (diff,)
    return any(isinstance(p, str) and p in _IGNORED_TABLES for p in parts)


async def _collect(metadata: MetaData) -> list[object]:
    engine = create_async_engine(TEST_DSN)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(lambda sync_conn: _diff_against(sync_conn, metadata))
    finally:
        await engine.dispose()


def test_migrations_produce_exactly_the_schema_models_expect() -> None:
    diffs = asyncio.run(_collect(Base.metadata))
    assert not diffs, (
        "схема после `alembic upgrade head` расходится с моделями — нужна миграция.\n"
        "Alembic дописал бы:\n  " + "\n  ".join(repr(d) for d in diffs)
    )


def test_the_check_itself_can_fail() -> None:
    """Проверка, способная только проходить, ничего не доказывает.

    Подсовываем метаданные с таблицей, которой в базе нет, и требуем,
    чтобы сравнение её заметило. Иначе зелёный тест выше означал бы
    только то, что `compare_metadata` промолчал.
    """
    probe = MetaData()
    Table("таблица_которой_нет", probe, Column("id", Integer, primary_key=True))
    assert asyncio.run(_collect(probe)), "сравнение не увидело лишнюю таблицу"
