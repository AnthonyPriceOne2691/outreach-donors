"""Ревизия миграции — вниз и вверх на соединении теста.

Схема тестовой базы собирается по моделям, поэтому файл ревизии тестами
сам по себе не исполняется: проверка покрытия видит его нулём, а ошибку
в нём впервые увидел бы прод при выкатке. Здесь ревизия загружается
файлом и гоняется вниз-вверх на той же базе, где идут тесты.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.engine import Connection

VERSIONS = Path(__file__).resolve().parents[1] / "backend/migrations/versions"


def load_migration(filename: str) -> ModuleType:
    """Модуль ревизии по имени файла в `backend/migrations/versions`."""
    spec = importlib.util.spec_from_file_location(filename.removesuffix(".py"), VERSIONS / filename)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _present(connection: Connection, table: str, columns: set[str]) -> set[str]:
    rows = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = ANY(:columns)"
        ),
        {"table": table, "columns": sorted(columns)},
    )
    return set(rows.scalars())


def columns_down_and_up(
    connection: Connection, filename: str, table: str, columns: set[str]
) -> tuple[set[str], set[str]]:
    """Какие из `columns` есть в `table` после `downgrade` и после `upgrade`."""
    migration = load_migration(filename)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = _present(connection, table, columns)
        migration.upgrade()
    return down, _present(connection, table, columns)
