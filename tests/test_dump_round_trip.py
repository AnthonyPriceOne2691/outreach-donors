"""Копия базы разворачивается так, как это сделает `scripts/restore.sh` (аудит 20.11.2026).

`pg_dump -Fc` снимает базу тестов — схему после всех ревизий, запись журнала действий, его
замок и выдачи роли приложения; `pg_restore` разворачивает копию в чистую базу с флагами
`restore.sh` — дважды: второй раз поверх развёрнутой (`--clean`). Вопросы:
- копия с выдачами роли, которой в кластере нет (новая машина, `.env` без пароля роли),
  разворачивается — и только с `--no-privileges`: без него та же копия отказывает целиком,
  а база остаётся пустой (одна транзакция);
- после развёртывания та же версия схемы и те же таблицы, запись журнала на месте — замок
  не помешал её вставить, — а правка журнала снова отказ.

Утилиты — из контейнера базы (`docker exec`), как у `scripts/backup.sh`: версия pg_dump та же,
что у сервера. Контейнер называет `TEST_PG_CONTAINER` — в CI служебный контейнер джобы
(`.github/workflows/ci.yml`), на машине разработчика — контейнер дев-базы (`docker ps`).
Не задан — тест пропускается и говорит почему.
"""

from __future__ import annotations

import os
import subprocess

import pytest
from sqlalchemy import make_url, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool
from tests.conftest import TEST_DSN
from tests.test_db_app_role import _drop, _ensure, _fresh_name

CONTAINER = os.getenv("TEST_PG_CONTAINER", "")
#: В CI контейнер базы есть всегда (`ci.yml`): пропуск там значил бы проверку, которая молча не
#: идёт, — без контейнера тест в CI падает, а не пропускается.
IN_CI = os.getenv("GITHUB_ACTIONS") == "true"
pytestmark = pytest.mark.skipif(
    not CONTAINER and not IN_CI,
    reason="TEST_PG_CONTAINER не задан: pg_dump и pg_restore идут в контейнере базы, "
    "как у scripts/backup.sh",
)

#: Флаги восстановления — те же, что у `scripts/restore.sh`.
RESTORE_FLAGS = ("--clean", "--if-exists", "--single-transaction", "--no-privileges")
URL = make_url(TEST_DSN)
SOURCE = str(URL.database)
SCRATCH = f"{SOURCE}_restore"
USER = str(URL.username)
PROBE = "dump:probe"


def _in_container(*command: str, stdin: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["docker", "exec", "-i", CONTAINER, *command],
        input=stdin,
        capture_output=True,
        check=False,
        timeout=180,
    )


def _restore(dump: bytes, *flags: str) -> subprocess.CompletedProcess[bytes]:
    return _in_container("pg_restore", "-U", USER, "-d", SCRATCH, *flags, stdin=dump)


def _engine(database: str, **options: str) -> AsyncEngine:
    return create_async_engine(URL.set(database=database), poolclass=NullPool, **options)


async def _run(database: str, *statements: str) -> None:
    """Команды владельцем — одной транзакцией, с фиксацией."""
    engine = _engine(database)
    try:
        async with engine.begin() as conn:
            for statement in statements:
                await conn.exec_driver_sql(statement)
    finally:
        await engine.dispose()


async def _recreate_scratch(*, again: bool) -> None:
    """Чистая база под копию; `again=False` — только снести. Вне транзакции: `CREATE DATABASE`
    в ней не идёт."""
    engine = _engine("postgres", isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            await conn.exec_driver_sql(f'DROP DATABASE IF EXISTS "{SCRATCH}" WITH (FORCE)')
            if again:
                await conn.exec_driver_sql(f'CREATE DATABASE "{SCRATCH}"')
    finally:
        await engine.dispose()


async def _facts(database: str) -> tuple[set[str], str, list[str]]:
    """Таблицы схемы, версия схемы и цели записей журнала."""
    engine = _engine(database)
    try:
        async with engine.connect() as conn:
            tables = set(
                await conn.scalars(
                    text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
                )
            )
            if "alembic_version" not in tables:
                return tables, "", []
            version = str(await conn.scalar(text("SELECT version_num FROM alembic_version")))
            targets = list(await conn.scalars(text("SELECT target FROM audit_log ORDER BY id")))
    finally:
        await engine.dispose()
    return tables, version, targets


async def _journal_refusal(database: str) -> str:
    """Код отказа правки журнала; правка прошла — тест падает, а правка откатывается."""
    engine = _engine(database)
    try:
        async with engine.connect() as conn:
            transaction = await conn.begin()
            try:
                with pytest.raises(DBAPIError) as caught:
                    await conn.execute(text("UPDATE audit_log SET target = 'правка'"))
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
    return str(getattr(caught.value.orig, "sqlstate", None))


async def _dump_with_grants_to_a_gone_role() -> tuple[bytes, str]:
    """Копия базы тестов с записью журнала и выдачами роли, которой после снятия уже нет."""
    role = _fresh_name()
    await _run(SOURCE, f"INSERT INTO audit_log (action, target) VALUES ('login', '{PROBE}')")
    try:
        await _ensure(role, "dump-check-password")
        done = _in_container("pg_dump", "-U", USER, "-d", SOURCE, "-Fc")
    finally:
        await _drop(role)
        # Запись журнала стирается только в обход замка — как у оснастки тестов с фиксациями.
        await _run(
            SOURCE,
            "SET LOCAL session_replication_role = replica",
            f"DELETE FROM audit_log WHERE target = '{PROBE}'",
        )
    assert done.returncode == 0, done.stderr.decode()
    assert done.stdout.startswith(b"PGDMP")
    return done.stdout, role


async def test_the_copy_restores_like_restore_sh_without_the_role_it_grants_to() -> None:
    assert CONTAINER, "TEST_PG_CONTAINER пуст в CI: шаг тестов в ci.yml его не передал"
    dump, role = await _dump_with_grants_to_a_gone_role()
    tables, version, _ = await _facts(SOURCE)
    await _recreate_scratch(again=True)
    try:
        with_grants = _restore(dump, *(flag for flag in RESTORE_FLAGS if flag != "--no-privileges"))
        assert with_grants.returncode != 0
        assert f'role "{role}" does not exist' in with_grants.stderr.decode()
        assert await _facts(SCRATCH) == (set(), "", []), "одна транзакция: отказ — база пуста"

        for _ in range(2):
            done = _restore(dump, *RESTORE_FLAGS)
            assert done.returncode == 0, done.stderr.decode()

        assert await _facts(SCRATCH) == (tables, version, [PROBE])
        assert await _journal_refusal(SCRATCH) == "42501"
    finally:
        await _recreate_scratch(again=False)
