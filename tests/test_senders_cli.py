"""Консоль ящиков рассылки: завести ящик и показать ящики — словами.

Команды открывают свою базу, поэтому здесь настоящие фиксации и чистка после
(`committed_sessions`). Проверяется то, что видит человек в терминале, и то, что
легло в базу: заведённый ящик начинает разгон с этой минуты, а ступень потолка
считается от первого письма — до него ящик на первой ступени.
"""

from __future__ import annotations

import argparse

import pytest
from backend.cli import senders_admin
from backend.config import outreach as cfg
from backend.config import storage
from backend.features.core.models.outreach import SenderModel
from backend.features.outreach.senders import warmup_state
from sqlalchemy import select
from tests.conftest import TEST_DSN, make_sender
from tests.test_send_race import committed_sessions


@pytest.fixture(autouse=True)
def _test_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)


def _add(email: str, **given: object) -> argparse.Namespace:
    return argparse.Namespace(
        email=email,
        domain=given.get("domain"),
        daily_cap=given.get("daily_cap", cfg.DAILY_CAP_PER_SENDER),
        stage=given.get("stage", "donors"),
    )


def test_commands_and_their_arguments() -> None:
    parser = argparse.ArgumentParser()
    senders_admin.add_parser(parser.add_subparsers(dest="command"))

    add = parser.parse_args(["sender-add", "--email", "a@mail-a.example.test"])
    staged = parser.parse_args(
        ["sender-add", "--email", "b@mail-b.example.test", "--daily-cap", "30", "--stage", "sales"]
    )

    assert (add.domain, add.daily_cap, add.stage) == (None, cfg.DAILY_CAP_PER_SENDER, "donors")
    assert (staged.daily_cap, staged.stage) == (30, "sales")
    assert parser.parse_args(["senders"]).command == "senders"


async def test_a_new_mailbox_starts_its_warmup_on_the_first_step(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with committed_sessions() as factory:
        done = await senders_admin.cmd_sender_add(_add(" Outreach@Mail-A.example.test "))

        async with factory() as session:
            [sender] = (await session.execute(select(SenderModel))).scalars().all()
        said = capsys.readouterr().out

    assert done == senders_admin.EXIT_OK
    assert (sender.email, sender.domain, sender.enabled) == (
        "outreach@mail-a.example.test",
        "mail-a.example.test",
        True,
    )
    assert sender.warmup_started_at is not None
    assert sender.warmup_first_sent_at is None
    assert "Заведён outreach@mail-a.example.test (домен mail-a.example.test, этап donors)" in said
    assert f"сегодня по разгону — {warmup_state(sender).allowance}" in said
    assert warmup_state(sender).allowance == cfg.WARMUP_DAILY_CAPS[0]


async def test_the_same_mailbox_twice_is_refused_in_words(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with committed_sessions():
        first = await senders_admin.cmd_sender_add(_add("outreach@mail-a.example.test"))
        second = await senders_admin.cmd_sender_add(_add("OUTREACH@mail-a.example.test"))

    assert (first, second) == (senders_admin.EXIT_OK, senders_admin.EXIT_TAKEN)
    assert "Ящик outreach@mail-a.example.test уже заведён" in capsys.readouterr().out


@pytest.mark.parametrize("email", ["mail-a.example.test", "@mail-a.example.test", "outreach@"])
async def test_not_an_address_is_refused_before_the_base(
    email: str, capsys: pytest.CaptureFixture[str]
) -> None:
    done = await senders_admin.cmd_sender_add(_add(email))

    assert done == senders_admin.EXIT_BAD_ADDRESS
    assert f"«{email}» не похоже на адрес почты" in capsys.readouterr().out


async def test_no_mailboxes_is_said_with_the_way_to_add_one(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with committed_sessions():
        done = await senders_admin.cmd_senders(argparse.Namespace())

    assert done == senders_admin.EXIT_OK
    assert "Ящиков нет. Завести: outreach sender-add" in capsys.readouterr().out


async def test_mailboxes_are_listed_with_their_state(capsys: pytest.CaptureFixture[str]) -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await make_sender(session, "working@mail-a.example.test")
            paused = await make_sender(session, "paused@mail-b.example.test")
            off = await make_sender(session, "off@mail-c.example.test")
            paused.pause_reason = "отказ платформы"
            off.enabled = False
            await session.commit()

        done = await senders_admin.cmd_senders(argparse.Namespace())

    lines = capsys.readouterr().out.splitlines()
    assert done == senders_admin.EXIT_OK
    [working] = [line for line in lines if line.startswith("working@")]
    [held] = [line for line in lines if line.startswith("paused@")]
    [disabled] = [line for line in lines if line.startswith("off@")]
    assert working.split()[1:3] == ["donors", "20"]
    assert held.endswith("(отказ платформы)")
    assert disabled.endswith("выключен")
    assert f"Транспорт: {cfg.TRANSPORT}" in lines
