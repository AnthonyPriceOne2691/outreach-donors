"""Учётки из консоли сервера — `outreach user-add` и `outreach user-reset` (`cli/access_admin.py`).

Команды ходят в базу своим подключением (`storage.DSN`) и фиксируют сами, поэтому тест — на
настоящих фиксациях (`committed_sessions`: после теста база вычищается целиком). Что проверяем:
разовый пароль показан один раз и со сроком (аудит 20.11.2026, `ACCESS_ONE_TIME_TTL_HOURS`),
занятая почта и неизвестная почта — отказ словами с подсказкой, сброс снимает паузу входа и
счёт неудач подряд.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

import pytest
from backend.cli import access_admin
from backend.config import access, storage
from backend.features.access.passwords import verify_password
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from sqlalchemy import select, update
from tests.conftest import TEST_DSN
from tests.test_send_race import committed_sessions

EMAIL = "ops@team.example.test"


@pytest.fixture(autouse=True)
def console(monkeypatch: pytest.MonkeyPatch) -> None:
    """Команда — к тестовой базе этого процесса; проверка окружения сервера здесь не нужна."""
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(access_admin, "check_storage", lambda: None)


def _shown_password(out: str) -> str:
    """Разовый пароль из вывода команды — последнее слово строки, где он назван."""
    line = next(line for line in out.splitlines() if "азовый пароль" in line)
    return line.rsplit(" ", 1)[-1]


async def test_user_add_shows_the_one_time_password_once_with_its_deadline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with committed_sessions() as factory:
        code = await access_admin.cmd_user_add(argparse.Namespace(email=EMAIL, role="operator"))
        out = capsys.readouterr().out
        async with factory() as session:
            user = (await session.scalars(select(UserModel))).one()

        assert code == access_admin.EXIT_OK
        assert f"Учётка заведена: {EMAIL}, роль operator" in out
        assert f"Действует {access.ONE_TIME_TTL_HOURS} ч" in out
        assert "Это первая учётка в базе" in out
        assert (user.email, user.role, user.must_change_password) == (
            EMAIL,
            UserRole.OPERATOR,
            True,
        )
        assert verify_password(_shown_password(out), user.password_hash)


async def test_user_add_on_a_taken_email_is_refused_in_words(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with committed_sessions() as factory:
        await access_admin.cmd_user_add(argparse.Namespace(email=EMAIL, role="admin"))
        capsys.readouterr()

        code = await access_admin.cmd_user_add(argparse.Namespace(email=EMAIL, role="operator"))
        out = capsys.readouterr().out
        async with factory() as session:
            users = (await session.scalars(select(UserModel))).all()

        assert code == access_admin.EXIT_TAKEN
        assert "outreach user-reset --email" in out
        assert [(user.email, user.role) for user in users] == [(EMAIL, UserRole.ADMIN)]


async def test_user_reset_gives_a_new_one_time_password_and_lifts_the_pause(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with committed_sessions() as factory:
        await access_admin.cmd_user_add(argparse.Namespace(email=EMAIL, role="operator"))
        first = _shown_password(capsys.readouterr().out)
        paused = datetime.now(UTC) + timedelta(minutes=30)
        async with factory() as session:
            await session.execute(update(UserModel).values(failed_logins=9, locked_until=paused))
            await session.commit()

        code = await access_admin.cmd_user_reset(argparse.Namespace(email=EMAIL))
        out = capsys.readouterr().out
        async with factory() as session:
            user = (await session.scalars(select(UserModel))).one()

        assert code == access_admin.EXIT_OK
        assert f"Новый разовый пароль для {EMAIL}:" in out
        assert f"Действует {access.ONE_TIME_TTL_HOURS} ч" in out
        second = out.splitlines()[0].rsplit(" ", 1)[-1]
        assert second != first
        assert verify_password(second, user.password_hash)
        assert (user.must_change_password, user.failed_logins, user.locked_until) == (
            True,
            0,
            None,
        )


async def test_user_reset_of_an_unknown_email_is_refused_in_words(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with committed_sessions():
        code = await access_admin.cmd_user_reset(argparse.Namespace(email=EMAIL))
        out = capsys.readouterr().out

    assert code == access_admin.EXIT_TAKEN
    assert f"Учётки {EMAIL} нет" in out
    assert "outreach user-add --email" in out
