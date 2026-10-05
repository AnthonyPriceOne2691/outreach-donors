"""Консоль писем: собрать очередь, показать её, отправить одно письмо — словами.

Команды открывают свою базу (`_sessions`), поэтому здесь настоящие
фиксации и чистка после (`committed_sessions`). Проверяется то, что видит
человек в терминале: отчёт сборки, пустая очередь словами, исход отправки —
и «исход неизвестен» без трассировки.
"""

from __future__ import annotations

import argparse

import pytest
from backend.cli import letters_queue
from backend.config import storage
from backend.features.letters import transport_factory
from backend.features.letters.transport import MaybeSentError, NullTransport, Outgoing
from tests.conftest import TEST_DSN, make_donor, make_sender
from tests.test_send_race import _one_letter, committed_sessions


@pytest.fixture(autouse=True)
def _test_base(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(storage, "DSN", TEST_DSN)


def test_commands_and_their_arguments() -> None:
    parser = argparse.ArgumentParser()
    letters_queue.add_parser(parser.add_subparsers(dest="command"))

    build = parser.parse_args(["letters-build", "--campaign", "Октябрь", "--stage", "advertisers"])
    show = parser.parse_args(["letters"])
    send = parser.parse_args(["letters-send", "--id", "7"])

    assert (build.campaign, build.stage, build.limit, build.runs) == (
        "Октябрь",
        "advertisers",
        50,
        "",
    )
    assert show.limit == 20
    assert send.id == 7


async def test_build_reports_in_words(capsys: pytest.CaptureFixture[str]) -> None:
    args = argparse.Namespace(
        campaign="Консоль", stage="donors", country="us", niche="", limit=5, runs=""
    )
    async with committed_sessions() as factory:
        async with factory() as session:
            await make_donor(session, "cli.example.test", email="editor@cli.example.test")
            await make_sender(session, "outreach1@mail.example.test")
            await session.commit()

        code = await letters_queue.cmd_letters_build(args)

    out = capsys.readouterr().out
    assert code == letters_queue.EXIT_OK
    assert "подготовлено писем 1" in out
    assert "Отбор:" in out


async def test_queue_is_shown_and_empty_queue_says_how_to_fill_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with committed_sessions() as factory:
        empty = await letters_queue.cmd_letters(argparse.Namespace(limit=20))
        said_empty = capsys.readouterr().out
        letter_id = await _one_letter(factory)
        await letters_queue.cmd_letters(argparse.Namespace(limit=20))
        said_full = capsys.readouterr().out

    assert empty == letters_queue.EXIT_OK
    assert "Очередь пуста" in said_empty
    assert f"№{letter_id:>5}" in said_full
    assert "race.example.test" in said_full


async def test_send_tells_the_fate_and_the_unknown_outcome_without_a_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], filled_legal: None
) -> None:
    class Silent(NullTransport):
        async def send(self, outgoing: Outgoing) -> str:
            raise MaybeSentError("почтовая платформа не ответила — письмо могло уйти")

    async with committed_sessions() as factory:
        sent_id = await _one_letter(factory)
        sent = await letters_queue.cmd_letters_send(argparse.Namespace(id=sent_id))
        said_sent = capsys.readouterr().out

    monkeypatch.setattr(transport_factory, "build_transport", lambda *_, **__: Silent())
    async with committed_sessions() as factory:
        unknown_id = await _one_letter(factory)
        unknown = await letters_queue.cmd_letters_send(argparse.Namespace(id=unknown_id))
        said_unknown = capsys.readouterr().out

    assert sent == letters_queue.EXIT_OK
    assert "НЕ ушло (нулевой транспорт)" in said_sent
    assert unknown == letters_queue.EXIT_NOT_SENT
    assert "Исход неизвестен, письмо осталось «отправляется»" in said_unknown
