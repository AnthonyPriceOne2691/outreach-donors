"""Журнал действий только дописывается — это держит база (аудит 20.11.2026, п. 36).

Ревизия `d4f8a2c61e93`: UPDATE и DELETE строки `audit_log` и TRUNCATE таблицы — отказ `42501`
со словами, добавление — как раньше. Вопросы теста:
- правка, удаление и очистка журнала — отказ, и отказ называет, что запрещено;
- удаление учётки, на которую ссылается журнал, — тоже отказ: `SET NULL` стёр бы автора;
- суперпользователь обходит замок только осознанно (`session_replication_role`) — так чистит
  базу оснастка тестов с фиксациями (`tests/committed_db.py`);
- `downgrade` снимает замок, `upgrade` ставит обратно.
"""

from __future__ import annotations

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration

REVISION = "d4f8a2c61e93_audit_log_append_only.py"
REFUSED = "42501"


async def _author(session: AsyncSession, email: str = "автор@team.example.test") -> UserModel:
    return await AccessRepository(session).create(
        email=email, password="пароль-для-теста", role=UserRole.ADMIN
    )


async def _record(session: AsyncSession) -> UserModel:
    """Учётка и запись журнала о её входе — как их оставляет вход. Заведение учётки — тоже
    запись журнала (`USER_CREATED`), без автора."""
    author = await _author(session)
    await AccessRepository(session).record(AuditAction.LOGIN, author_id=author.id, target="вход")
    await session.flush()
    return author


async def _refusal(session: AsyncSession, statement: str) -> str:
    """Команда — отказ 42501 в своей точке сохранения; текст отказа. Внешняя транзакция теста
    после отказа жива."""
    with pytest.raises(DBAPIError) as caught:
        async with session.begin_nested():
            await session.execute(text(statement))
    orig = caught.value.orig
    assert getattr(orig, "sqlstate", None) == REFUSED, orig
    return str(orig)


async def _count(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(AuditLogModel)) or 0)


def _revision(connection: Connection, step: str) -> None:
    with Operations.context(MigrationContext.configure(connection)):
        getattr(load_migration(REVISION), step)()


async def test_a_record_is_added_as_before(session: AsyncSession) -> None:
    author = await _record(session)

    entry = await session.scalar(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.LOGIN)
    )
    assert entry is not None
    assert (entry.user_id, entry.target) == (author.id, "вход")


@pytest.mark.parametrize(
    ("statement", "named"),
    [
        ("UPDATE audit_log SET target = 'правка'", "UPDATE"),
        ("DELETE FROM audit_log", "DELETE"),
        ("TRUNCATE audit_log", "TRUNCATE"),
    ],
    ids=["правка", "удаление", "очистка"],
)
async def test_a_record_is_neither_rewritten_nor_removed(
    session: AsyncSession, statement: str, named: str
) -> None:
    await _record(session)
    before = await _count(session)

    said = await _refusal(session, statement)

    assert f"{named} в audit_log запрещён" in said
    assert "только дописывается" in said
    assert await _count(session) == before


async def test_truncating_users_does_not_reach_the_journal(session: AsyncSession) -> None:
    """`TRUNCATE … CASCADE` от учёток дошёл бы до журнала по внешнему ключу автора."""
    await _record(session)
    before = await _count(session)

    await _refusal(session, "TRUNCATE users CASCADE")

    assert await _count(session) == before


async def test_an_author_with_records_is_not_deleted_but_one_without_is(
    session: AsyncSession,
) -> None:
    """`ON DELETE SET NULL` переписал бы автора записи — это та же правка журнала."""
    author = await _record(session)
    stranger = await _author(session, "без-записей@team.example.test")

    await _refusal(session, f"DELETE FROM users WHERE id = {author.id}")
    await session.execute(text("DELETE FROM users WHERE id = :id"), {"id": stranger.id})

    kept = await session.scalar(
        select(AuditLogModel.user_id).where(AuditLogModel.action == AuditAction.LOGIN)
    )
    assert kept == author.id


async def test_the_owner_passes_the_lock_only_on_purpose(session: AsyncSession) -> None:
    """Суперпользователь — и только он — выключает обычные триггеры до конца транзакции; без
    этой строки отказ тот же, что у всех."""
    await _record(session)
    before = await _count(session)

    nested = await session.begin_nested()
    await session.execute(text("SET LOCAL session_replication_role = replica"))
    await session.execute(text("DELETE FROM audit_log"))
    assert await _count(session) == 0
    await nested.rollback()

    assert await _count(session) == before
    await _refusal(session, "DELETE FROM audit_log")


async def test_the_lock_is_an_ordinary_trigger_not_an_always_one(session: AsyncSession) -> None:
    """`ENABLE ALWAYS` сработал бы и под `session_replication_role = replica`: суперпользователь
    не почистил бы журнал и между тестами (`tests/committed_db.py`)."""
    rows = await session.execute(
        text(
            "SELECT tgname, tgenabled::text FROM pg_trigger "
            "WHERE tgrelid = 'audit_log'::regclass AND NOT tgisinternal ORDER BY tgname"
        )
    )

    assert [tuple(row) for row in rows] == [
        ("audit_log_append_only_rows", "O"),
        ("audit_log_append_only_truncate", "O"),
    ]


async def test_downgrade_takes_the_lock_off_and_upgrade_puts_it_back(
    session: AsyncSession,
) -> None:
    await _record(session)
    connection = await session.connection()

    await connection.run_sync(_revision, "downgrade")
    await session.execute(text("UPDATE audit_log SET target = 'без замка'"))
    await connection.run_sync(_revision, "upgrade")

    targets = set(await session.scalars(select(AuditLogModel.target)))
    assert targets == {"без замка"}
    await _refusal(session, "UPDATE audit_log SET target = 'снова'")
