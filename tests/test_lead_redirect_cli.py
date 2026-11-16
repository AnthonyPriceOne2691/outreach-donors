"""Разовый проход переадресации по диалогам, уже ждущим человека: `outreach lead-redirect`.

Предпросмотр по умолчанию говорит, что бы ушло, кому и почему, — и ничего не пишет: ни строки
решения, ни контакта, ни письма, ни строки расхода, и модель не зовёт. `--write` — только с
переписками или `--all`: разбирает моделью и шлёт теми же правилами, что проход. И ревизия
таблицы решений ходит вниз и вверх.
"""

from __future__ import annotations

import argparse

import pytest
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.cli import lead_redirect as command
from backend.config import redirect as redirect_cfg
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.outreach import MessageModel
from backend.features.core.models.redirect import LeadRedirectModel
from backend.features.letters.chain import REDIRECT_STEP
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import load_migration
from tests.test_lead_redirect import (
    FIXED,
    LATER,
    PARTNERS,
    Model,
    Recording,
    asked,
    legal,
)

__all__ = ["legal"]  # фикстура письма — отсюда её видит pytest

MIGRATION = "c050e74c96dd_lead_redirects.py"


@pytest.fixture
def backlog(legal: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ответ пришёл до переадресации: приём его на разбор не ставил."""
    monkeypatch.setattr(redirect_cfg, "ENABLED", False)


async def _count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


def test_the_command_and_its_arguments() -> None:
    parser = argparse.ArgumentParser()
    command.add_parser(parser.add_subparsers(dest="command"))

    look = parser.parse_args(["lead-redirect"])
    write = parser.parse_args(["lead-redirect", "--write", "--threads", "12, 15"])

    assert (look.write, look.all, look.threads) == (False, False, "")
    assert (write.write, command._threads(write.threads)) == (True, [12, 15])


async def test_write_names_threads_or_all() -> None:
    """Без переписок и без `--all` запись не начинается — до базы и до модели."""
    said = await command.cmd_lead_redirect(
        argparse.Namespace(write=True, threads="", all=False, limit=10)
    )

    assert said == command.EXIT_MISUSED


@pytest.mark.usefixtures("backlog")
async def test_the_preview_says_what_would_go_and_writes_nothing(session: AsyncSession) -> None:
    first, reply = await asked(session)
    letters, contacts = await _count(session, MessageModel), await _count(session, ContactModel)

    found = await command.waiting(session)
    looks = await command.preview(session, found)

    [one] = looks
    assert (one.found.reply.id, one.found.thread_id) == (reply.id, first.thread_id)
    assert one.addresses == (PARTNERS,)
    assert one.asks
    assert f"спросит модель, просят ли писать на {PARTNERS}" in one.verdict
    assert FIXED in one.verdict
    assert await _count(session, LeadRedirectModel) == 0
    assert await _count(session, MessageModel) == letters
    assert await _count(session, ContactModel) == contacts
    spent = select(UsageRecordModel.id).where(UsageRecordModel.operation == "lead_redirect")
    assert await session.scalar(spent) is None


@pytest.mark.usefixtures("backlog")
async def test_the_preview_says_why_not(session: AsyncSession) -> None:
    await asked(session, "Thanks! Just reply here, or to max@offers.test.")

    [one] = await command.preview(session, await command.waiting(session))

    assert one.addresses == ()
    assert not one.asks
    assert "нет адресов" in one.verdict


@pytest.mark.usefixtures("backlog")
async def test_write_sends_for_the_named_thread(session: AsyncSession) -> None:
    first, _ = await asked(session)
    model, transport = Model(), Recording()
    found = await command.waiting(session, [first.thread_id or 0])

    try:
        done = await command.write(session, model.client(), transport, found, now=LATER)
    finally:
        await model.http.aclose()

    assert len(model.seen) == 1
    [out] = transport.seen
    assert out.to == PARTNERS
    assert done[first.thread_id or 0].startswith(f"Переслали на {PARTNERS} по ответу ")
    letter = await session.scalar(select(MessageModel).where(MessageModel.step == REDIRECT_STEP))
    assert letter is not None
    assert letter.thread_id == first.thread_id
    # Повтор команды второго письма не шлёт: ответ уже разобран и в предпросмотр не попадает.
    assert await command.waiting(session, [first.thread_id or 0]) == []


def _down_and_up(connection: Connection) -> tuple[bool, bool, set[str]]:
    """Ревизия вниз и вверх на соединении теста: откат снимает таблицу, подъём ставит её
    с индексами по переписке и состоянию."""

    def present() -> bool:
        return bool(connection.scalar(text("SELECT to_regclass('lead_redirects') IS NOT NULL")))

    migration = load_migration(MIGRATION)
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        down = present()
        migration.upgrade()
    indexes = connection.scalars(
        text("SELECT indexname FROM pg_indexes WHERE tablename = 'lead_redirects'")
    )
    return down, present(), set(indexes)


async def test_the_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up, indexes = await connection.run_sync(_down_and_up)

    assert (down, up) == (False, True)
    assert {"idx_lead_redirects_thread", "idx_lead_redirects_state"} <= indexes
