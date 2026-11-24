"""Окружение миграций (`backend/migrations/env.py`) ходит владельцем схемы, а не ролью сервисов.

Alembic исполняет env.py сам, и набор тестов поднимает схему отдельным процессом
(`conftest._migrate_to_head`), поэтому строки env.py не проходил ни один тест. Здесь он исполняется
тем же путём, что `alembic …` в контейнере `migrate`, но в процессе тестов:

- без базы (`upgrade --sql`) SQL ревизии собирается по адресу владельца, хоть рядом и задан адрес роли;
- с базой `alembic current` соединяется владельцем с тестовой базой и читает голову.

Файл настроек `alembic.ini` не читается (`Config()` без файла): его `fileConfig` в процессе тестов
выключил бы уже заведённые логгеры остальных тестов.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from backend.config import storage
from tests.conftest import TEST_DSN

MIGRATIONS = Path(__file__).resolve().parent.parent / "backend" / "migrations"
#: Адреса без паролей: проверяется, чей адрес взят, а не секрет.
OWNER = "postgresql+asyncpg://owner@db:5432/outreach"
APP = "postgresql+asyncpg://outreach_app@db:5432/outreach"


def _config(buffer: io.StringIO) -> Config:
    config = Config(output_buffer=buffer, stdout=buffer)
    config.set_main_option("script_location", str(MIGRATIONS))
    return config


def test_offline_sql_is_built_for_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "OWNER_DSN", OWNER)
    monkeypatch.setattr(storage, "DSN", APP)
    buffer = io.StringIO()
    config = _config(buffer)

    command.upgrade(config, "c0cf586c2995:d4f8a2c61e93", sql=True)

    assert config.get_main_option("sqlalchemy.url") == OWNER
    sql = buffer.getvalue()
    assert "CREATE TRIGGER audit_log_append_only_rows" in sql
    assert "CREATE TRIGGER audit_log_append_only_truncate" in sql


def test_online_reads_the_head_as_the_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "OWNER_DSN", TEST_DSN)
    monkeypatch.setattr(storage, "DSN", APP)
    buffer = io.StringIO()
    config = _config(buffer)

    command.current(config)

    assert config.get_main_option("sqlalchemy.url") == TEST_DSN
    assert "ce69f6ea12a1 (head)" in buffer.getvalue()  # pragma: allowlist secret
