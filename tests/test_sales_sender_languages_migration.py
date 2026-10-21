"""Ревизия наборов «Отправителя» по языку письма (`abcc1e4edf4a`) — вверх, вниз и снова вверх на базе
теста.

Подъём добавляет пустую колонку `sales_settings.languages` (JSONB) и строку отправителя не
переписывает: общий набор цел, у прежней строки наборов нет — письма подписаны им, как раньше.
Откат снимает колонку — заданные наборы теряются, общий набор цел. Замок — с пределом ожидания в
обе стороны (`SET LOCAL lock_timeout`): строку отправителя читает каждая отправка. В ревизии — только
литералы: код приложения меняется, а ревизия обязана подниматься такой, какой её записали.
"""

from __future__ import annotations

import ast
from typing import Any

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.sales.models import SalesSettingsModel
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import VERSIONS, load_migration

REVISION = "abcc1e4edf4a_sales_sender_languages.py"


def _column(connection: Connection) -> tuple[Any, ...] | None:
    """Тип, умолчание и пустота колонки наборов; колонки нет — `None`."""
    row = connection.execute(
        text(
            "SELECT data_type, column_default, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'sales_settings' AND column_name = 'languages'"
        )
    ).first()
    return None if row is None else tuple(row)


def _file(connection: Connection) -> int:
    """Файл таблицы на диске: переписанная таблица получает новый."""
    return int(
        connection.execute(
            text("SELECT relfilenode FROM pg_class WHERE oid = 'sales_settings'::regclass")
        ).scalar_one()
    )


def _step(connection: Connection, call: Any) -> str:
    """Шаг ревизии с чистым пределом ожидания — и какой предел шаг поставил себе сам."""
    connection.execute(text("SET LOCAL lock_timeout = 0"))
    call()
    return str(connection.execute(text("SHOW lock_timeout")).scalar_one())


def _up_down_up(connection: Connection) -> dict[str, Any]:
    connection.execute(
        text(
            "INSERT INTO sales_settings (id, sender_name, signature, physical_address) "
            "VALUES (1, 'Мира Тестова', 'Мира Тестова\nвыдуманное агентство', 'Выдуманная ул., 7')"
        )
    )
    migration = load_migration(REVISION)
    seen: dict[str, Any] = {}
    with Operations.context(MigrationContext.configure(connection)):
        seen["start"] = _step(connection, migration.downgrade)  # база теста собрана по моделям
        file_before = _file(connection)
        seen["up"] = _step(connection, migration.upgrade)
        seen["column"] = _column(connection)
        seen["rewritten"] = _file(connection) != file_before
        seen["kept"] = tuple(
            connection.execute(
                text("SELECT sender_name, physical_address, languages FROM sales_settings")
            ).one()
        )
        connection.execute(
            text("""UPDATE sales_settings SET languages = '{"en": {"signature": "Mira"}}'""")
        )
        seen["down"] = _step(connection, migration.downgrade)
        seen["gone"] = _column(connection)
        seen["common"] = connection.execute(text("SELECT sender_name FROM sales_settings")).one()[0]
        migration.upgrade()
        seen["again"] = connection.execute(text("SELECT languages FROM sales_settings")).one()[0]
    return seen


async def test_upgrade_downgrade_upgrade_keeps_the_common_set_and_rewrites_nothing(
    session: AsyncSession,
) -> None:
    connection = await session.connection()

    seen = await connection.run_sync(_up_down_up)

    assert (seen["start"], seen["up"], seen["down"]) == ("5s", "5s", "5s")
    assert seen["column"] == ("jsonb", None, "YES")
    assert seen["rewritten"] is False
    assert seen["kept"] == ("Мира Тестова", "Выдуманная ул., 7", None)
    assert (seen["gone"], seen["common"], seen["again"]) == (None, "Мира Тестова", None)
    assert "languages" in SalesSettingsModel.__table__.columns


def test_the_revision_follows_the_head_and_holds_only_literals() -> None:
    """Ревизия не берёт код приложения: имена таблиц, колонок и предел — литералами в файле."""
    migration = load_migration(REVISION)
    tree = ast.parse((VERSIONS / REVISION).read_text(encoding="utf-8"))
    imported = {
        (node.module or "") if isinstance(node, ast.ImportFrom) else alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }

    revisions = ("abcc1e4edf4a", "c7d2e9a14b38")  # pragma: allowlist secret — номера ревизий
    assert (migration.revision, migration.down_revision) == revisions
    assert imported == {"typing", "sqlalchemy", "alembic", "sqlalchemy.dialects"}
