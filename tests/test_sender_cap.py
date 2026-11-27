"""Дневной потолок ящика из консоли (27.11.2026): экрана для него нет, а правка базы руками на проде
не делается. Показ без `--yes`, запись — с журналом; этап целиком или названные ящики; разгон,
который держит ящик ниже нового потолка, команда называет.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

import pytest
from backend.cli import senders_admin
from backend.config import storage
from backend.features.core.domain import AuditAction, Stage
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.outreach import SenderModel
from backend.features.outreach import senders
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.committed_db import committed_sessions
from tests.conftest import TEST_DSN, make_sender

SALES_ONE = "a@mail-sales-a.example.test"
SALES_TWO = "b@mail-sales-b.example.test"
DONOR = "c@mail-donor.example.test"


def _steps(monkeypatch: pytest.MonkeyPatch, *steps: int) -> None:
    """Ступени разгона (`OUTREACH_WARMUP_CAPS`) — у разгона и у команды одни."""
    monkeypatch.setattr(senders, "WARMUP_STEPS", steps)
    monkeypatch.setattr(senders_admin, "WARMUP_STEPS", steps)


@pytest.fixture(autouse=True)
def base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    _steps(monkeypatch, 5, 10, 15, 20, 25, 30)


async def _boxes(factory: async_sessionmaker[AsyncSession]) -> None:
    """Два ящика продаж — второй с разгоном, давно на последней ступени, — и ящик доноров."""
    async with factory() as session:
        for email in (SALES_ONE, SALES_TWO):
            box = await make_sender(session, email)
            box.stage = Stage.SALES
        warmed = await session.scalar(select(SenderModel).where(SenderModel.email == SALES_TWO))
        assert warmed is not None
        long_ago = datetime.now(UTC) - timedelta(days=40)
        warmed.warmup_started_at, warmed.warmup_first_sent_at = long_ago, long_ago
        await make_sender(session, DONOR)
        await session.commit()


async def _caps(factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with factory() as session:
        found = await session.scalars(select(SenderModel))
        return {box.email: box.daily_cap for box in found.all()}


async def _notes(factory: async_sessionmaker[AsyncSession]) -> list[AuditLogModel]:
    async with factory() as session:
        return list((await session.scalars(select(AuditLogModel))).all())


def _args(
    cap: int, *, stage: str | None = None, emails: tuple[str, ...] = (), yes: bool
) -> argparse.Namespace:
    return argparse.Namespace(stage=stage, email=list(emails), daily_cap=cap, yes=yes)


async def test_the_stage_gets_the_new_cap_and_the_journal_says_so() -> None:
    async with committed_sessions() as factory:
        await _boxes(factory)

        done = await senders_admin.cmd_sender_cap(_args(25, stage="sales", yes=True))

        caps, notes = await _caps(factory), await _notes(factory)

    assert done == senders_admin.EXIT_OK
    assert caps == {SALES_ONE: 25, SALES_TWO: 25, DONOR: 20}
    assert [note.action for note in notes] == [AuditAction.USER_UPDATED] * 2
    assert {note.details["стало"] for note in notes} == {25}
    assert {note.details["было"] for note in notes} == {20}


async def test_without_yes_nothing_changes(capsys: pytest.CaptureFixture[str]) -> None:
    async with committed_sessions() as factory:
        await _boxes(factory)

        done = await senders_admin.cmd_sender_cap(_args(25, emails=(SALES_TWO,), yes=False))

        caps, notes = await _caps(factory), await _notes(factory)
    said = capsys.readouterr().out

    assert done == senders_admin.EXIT_OK
    assert (caps[SALES_TWO], notes) == (20, [])
    assert f"{SALES_TWO}: потолок 20 → 25, сегодня по разгону — 25" in said
    assert "Только показ" in said


async def test_the_warmup_holding_the_box_below_the_cap_is_named(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ступени разгона кончаются на 20 — ящик с разгоном выше не пойдёт, и команда это говорит."""
    _steps(monkeypatch, 5, 10, 15, 20)
    async with committed_sessions() as factory:
        await _boxes(factory)

        await senders_admin.cmd_sender_cap(_args(25, emails=(SALES_TWO,), yes=False))

    assert "Разгон кончается ступенью 20 (OUTREACH_WARMUP_CAPS)" in capsys.readouterr().out


async def test_an_unknown_address_stops_the_whole_command() -> None:
    async with committed_sessions() as factory:
        await _boxes(factory)

        done = await senders_admin.cmd_sender_cap(
            _args(25, emails=(SALES_ONE, "nobody@mail-x.example.test"), yes=True)
        )

        caps = await _caps(factory)

    assert done == senders_admin.EXIT_NOT_FOUND
    assert caps[SALES_ONE] == 20


@pytest.mark.parametrize("cap", [0, senders_admin.MAX_DAILY_CAP + 1])
async def test_a_cap_out_of_bounds_changes_nothing(cap: int) -> None:
    async with committed_sessions() as factory:
        await _boxes(factory)

        done = await senders_admin.cmd_sender_cap(_args(cap, stage="sales", yes=True))

        caps = await _caps(factory)

    assert done == senders_admin.EXIT_BAD_CAP
    assert set(caps.values()) == {20}


async def test_the_same_cap_is_left_alone(capsys: pytest.CaptureFixture[str]) -> None:
    async with committed_sessions() as factory:
        await _boxes(factory)

        await senders_admin.cmd_sender_cap(_args(20, emails=(SALES_ONE,), yes=True))

        notes = await _notes(factory)

    assert notes == []
    assert f"{SALES_ONE}: потолок уже 20, не трогаю" in capsys.readouterr().out
