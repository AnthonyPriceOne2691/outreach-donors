"""Снять паузу из консоли: ящик снова пишет, а ступень разгона остаётся (02.11.2026).

Чужой сбой поставил ящики на паузу, а кнопка «Включить заново» начинает разгон с первой ступени:
она права, когда причина — наша репутация, и она не отличает одно от другого. Команда — для случая,
когда причина известна, поэтому она только в консоли и пишет журнал.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import pytest
from backend.cli import senders_admin
from backend.config import storage
from backend.features.core.domain import AuditAction, SenderStatus
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.outreach import SenderModel
from backend.features.outreach import senders
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import TEST_DSN, make_sender
from tests.test_send_race import committed_sessions

#: Разгон ящика — некруглые минуты, не дата-обязательство.
STARTED = datetime(2026, 10, 1, 9, 17, 43, tzinfo=UTC)
FIRST = datetime(2026, 10, 2, 8, 41, 5, tzinfo=UTC)
PAUSED = "a@mail-a.example.test"
WORKING = "b@mail-b.example.test"


@pytest.fixture(autouse=True)
def base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)


async def _boxes(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as session:
        paused = await make_sender(session, PAUSED)
        paused.warmup_started_at, paused.warmup_first_sent_at = STARTED, FIRST
        senders.disable(paused, "отказов 3 в окне 50 писем", now=FIRST)
        await make_sender(session, WORKING)
        await session.commit()


async def _box(factory: async_sessionmaker[AsyncSession], email: str) -> SenderModel:
    async with factory() as session:
        found = await session.scalar(select(SenderModel).where(SenderModel.email == email))
    assert found is not None
    return found


def _args(*emails: str, yes: bool) -> argparse.Namespace:
    return argparse.Namespace(email=list(emails), yes=yes)


async def test_the_pause_goes_and_the_warmup_stays() -> None:
    async with committed_sessions() as factory:
        await _boxes(factory)

        done = await senders_admin.cmd_sender_resume(_args(f" {PAUSED.upper()} ", yes=True))

        box = await _box(factory, PAUSED)
        async with factory() as session:
            [note] = (await session.scalars(select(AuditLogModel))).all()

    assert done == senders_admin.EXIT_OK
    assert (box.enabled, box.status) == (True, SenderStatus.FREE)
    assert (box.paused_at, box.pause_reason) == (None, None)
    assert (box.warmup_started_at, box.warmup_first_sent_at) == (STARTED, FIRST)
    assert note.action is AuditAction.USER_UPDATED
    assert note.target == f"sender:{box.id}"
    assert note.details["разгон"] == "на месте"


async def test_without_yes_nothing_changes(capsys: pytest.CaptureFixture[str]) -> None:
    async with committed_sessions() as factory:
        await _boxes(factory)

        done = await senders_admin.cmd_sender_resume(_args(PAUSED, yes=False))

        box = await _box(factory, PAUSED)
        async with factory() as session:
            notes = (await session.scalars(select(AuditLogModel))).all()
    said = capsys.readouterr().out

    assert done == senders_admin.EXIT_OK
    assert (box.enabled, box.status, notes) == (False, SenderStatus.PAUSED, [])
    assert "пауза «отказов 3 в окне 50 писем», разгон с 01.10.2026" in said
    assert "Только показ" in said


async def test_a_box_that_writes_is_left_alone(capsys: pytest.CaptureFixture[str]) -> None:
    async with committed_sessions() as factory:
        await _boxes(factory)

        done = await senders_admin.cmd_sender_resume(_args(WORKING, yes=True))

        async with factory() as session:
            notes = (await session.scalars(select(AuditLogModel))).all()

    assert done == senders_admin.EXIT_OK
    assert notes == []
    assert f"{WORKING}: не на паузе, не трогаю" in capsys.readouterr().out


async def test_an_unknown_address_stops_the_whole_command(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with committed_sessions() as factory:
        await _boxes(factory)

        done = await senders_admin.cmd_sender_resume(
            _args(PAUSED, "nobody@mail-c.example.test", yes=True)
        )

        box = await _box(factory, PAUSED)

    assert done == senders_admin.EXIT_NOT_FOUND
    assert box.enabled is False
    assert "Таких ящиков нет: nobody@mail-c.example.test" in capsys.readouterr().out


def test_the_console_takes_several_addresses() -> None:
    parser = argparse.ArgumentParser()
    senders_admin.add_parser(parser.add_subparsers(dest="command"))

    args = parser.parse_args(["sender-resume", "--email", PAUSED, "--email", WORKING, "--yes"])

    assert (args.email, args.yes) == ([PAUSED, WORKING], True)
    assert parser.parse_args(["sender-resume", "--email", PAUSED]).yes is False
