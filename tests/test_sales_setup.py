"""Как модуль «Продажи» заводится: команда гипотез, право и выключатель — срез 1.1a.

Стартовые гипотезы заводятся данными, а не миграцией: их тексты не должны
попасть в публичный репозиторий. Путь команды — тот же, что в консоли; база —
настоящая, сессия — сьюта.
"""

from __future__ import annotations

import pytest
from backend.cli.main import build_parser
from backend.cli.sales import EXIT_BAD_NAME, EXIT_OK, EXIT_TAKEN, run_hypothesis_add
from backend.config import sales as sales_cfg
from backend.features.access.permissions import Actor, has_permission
from backend.features.core.domain import Permission, UserRole
from backend.features.sales.models import SalesHypothesisModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


async def _hypotheses(session: AsyncSession) -> list[tuple[str, str | None]]:
    rows = await session.execute(
        select(SalesHypothesisModel.name, SalesHypothesisModel.description).order_by(
            SalesHypothesisModel.id
        )
    )
    return [(name, description) for name, description in rows]


def test_console_knows_the_command() -> None:
    args = build_parser().parse_args(
        ["sales-hypothesis-add", "--name", "сайты EN", "--description", "кому и зачем"]
    )
    assert (args.command, args.name, args.description) == (
        "sales-hypothesis-add",
        "сайты EN",
        "кому и зачем",
    )


async def test_hypothesis_is_stored_with_its_description(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    code = await run_hypothesis_add(session, "  сайты   EN ", "  кому и зачем  ")

    assert code == EXIT_OK
    assert await _hypotheses(session) == [("сайты EN", "кому и зачем")]
    assert "«сайты EN»" in capsys.readouterr().out


async def test_taken_name_is_refused_in_words(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:  # A8
    assert await run_hypothesis_add(session, "сайты EN", None) == EXIT_OK
    capsys.readouterr()

    code = await run_hypothesis_add(session, "сайты  EN", "другое описание")

    assert code == EXIT_TAKEN
    assert await _hypotheses(session) == [("сайты EN", None)]
    assert "имя «сайты EN» уже у гипотезы №" in capsys.readouterr().out


@pytest.mark.parametrize(("name", "words"), [("   ", "нет имени"), ("я" * 129, "длиннее 128")])
async def test_unusable_name_is_refused_in_words(
    session: AsyncSession, capsys: pytest.CaptureFixture[str], name: str, words: str
) -> None:  # A8
    code = await run_hypothesis_add(session, name, "кому и зачем")

    assert code == EXIT_BAD_NAME
    assert await _hypotheses(session) == []
    assert words in capsys.readouterr().out


def test_sales_is_off_by_default_and_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Урок L4 соседнего проекта: поле настроек читается только по алиасу,
    и тест, не меняющий значения, зелёный всегда. Поэтому — оба значения."""
    monkeypatch.delenv("SALES_ENABLED", raising=False)
    assert sales_cfg._Sales(_env_file=None).enabled is False
    monkeypatch.setenv("SALES_ENABLED", "true")
    assert sales_cfg._Sales(_env_file=None).enabled is True


def test_operator_gets_sales_only_by_name() -> None:
    """Админу право приходит с ролью, оператору — только поимённо, как отправка."""
    operator = Actor(user_id=1, role=UserRole.OPERATOR)
    granted = Actor(user_id=2, role=UserRole.OPERATOR, overrides={"sales": True})
    admin = Actor(user_id=3, role=UserRole.ADMIN)
    allowed = [has_permission(who, Permission.SALES) for who in (operator, granted, admin)]
    assert allowed == [False, True, True]
