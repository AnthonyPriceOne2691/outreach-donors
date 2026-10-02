"""Своя тестовая база у каждого дерева.

Прогон начинается со сноса схемы, и с одной базой на все деревья прогон
в одном дереве сносил её под прогоном в другом: 01.10.2026 pre-push дал
«20 failed, 1 error», а в тишине тот же набор был зелёным.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection
from tests import conftest
from tests.conftest import own_test_dsn


def test_each_tree_gets_its_own_database() -> None:
    main = make_url(own_test_dsn(Path("/repo"))).database
    tree = make_url(own_test_dsn(Path("/repo/.claude/worktrees/task"))).database
    assert main != tree
    assert main is not None
    assert main.startswith("outreach_test_")


def test_the_same_tree_keeps_its_database() -> None:
    """Иначе каждый прогон заводил бы новую базу, а старые копились бы."""
    assert own_test_dsn(Path("/repo")) == own_test_dsn(Path("/repo"))


class _RacedError(Exception):
    """Отказ драйвера: проигравший гонку `CREATE DATABASE` точно в ту же секунду."""

    pgcode = "23505"  # уникальный индекс pg_database, а не 42P04


def _race(monkeypatch: pytest.MonkeyPatch, *, made_by_neighbour: bool) -> None:
    """Проверка «базы нет», затем отказ создания; повторная проверка — как сказано."""
    answers = iter([False, made_by_neighbour])

    async def exists(_conn: object, _name: str | None) -> bool:
        return next(answers)

    async def create(*_args: object, **_kwargs: object) -> None:
        raise DBAPIError("CREATE DATABASE", None, _RacedError())

    monkeypatch.setattr(conftest, "_database_exists", exists)
    monkeypatch.setattr(AsyncConnection, "execute", create)


def test_a_database_made_by_a_neighbour_run_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Соседний прогон завёл базу между проверкой и созданием: «уже есть» —
    повод идти дальше, каким бы кодом ни отказал `CREATE DATABASE`."""
    _race(monkeypatch, made_by_neighbour=True)
    asyncio.run(conftest._ensure_the_database())


def test_a_real_refusal_still_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    """Создать не вышло, и базы нет — это отказ, а не гонка."""
    _race(monkeypatch, made_by_neighbour=False)
    with pytest.raises(DBAPIError):
        asyncio.run(conftest._ensure_the_database())
