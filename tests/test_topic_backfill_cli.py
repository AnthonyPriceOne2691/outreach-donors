"""Команда досуда тематики: план до траты, «да» или «нет», итог словами.

Модель и главные — подставные: без сети. Домены — в зоне `.example` (`test_topic_backfill`).
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.cli.main import main
from backend.cli.topic_backfill import add_parser, run_topic_backfill
from backend.features.topics import backfill as topic_backfill
from backend.features.topics.classifier import TopicVerdict
from backend.features.topics.cost import ESTIMATE
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN
from tests.test_topic_backfill import _base, _model, _opened, _topic


def _args(**extra: Any) -> argparse.Namespace:
    values = {
        "limit": None,
        "dry_run": False,
        "no_home": False,
        "again": False,
        "again_before": None,
        "yes": False,
    }
    return argparse.Namespace(**{**values, **extra})


class TestCommand:
    async def test_dry_run_names_the_cost_and_spends_nothing(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        await _base(session)
        asked = _model(monkeypatch, {})

        code = await run_topic_backfill(session, _args(dry_run=True), http=None, home_client=None)  # type: ignore[arg-type]

        printed = capsys.readouterr().out
        assert code == 0
        assert asked == []
        assert "Определить тему: 3 сайтов (доноров: 1)" in printed
        assert "уже с темой этой версии: 1" in printed
        assert "текст есть (страница из выдачи прогона): 1" in printed
        assert "откроем главную (бесплатно; `--no-home` — пропустить): 2" in printed
        assert "у 1 из них — заголовок из суда запасом, если главная не откроется" in printed
        assert f"Токены модели: до ≈ {3 * ESTIMATE.tokens:,}".replace(",", " ") in printed
        assert "по оценке, замера ещё не было" in printed
        assert "Деньги: до ≈ $" in printed
        assert "заново" not in printed

    async def test_again_prints_the_moment_to_resume_with(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """«Заново» — до момента старта, и момент — в плане: повтор после обрыва с ним
        продолжает с места. Момент без пояса — UTC, кривой — ошибка разбора, а не тихий ноль."""
        await _base(session)
        _model(monkeypatch, {})

        await run_topic_backfill(
            session, _args(dry_run=True, again=True), http=None, home_client=None
        )  # type: ignore[arg-type]

        printed = capsys.readouterr().out
        assert "Определить тему: 4 сайтов" in printed
        assert "продолжить после обрыва: --again-before 20" in printed
        moment = printed.split("--again-before ")[1].split()[0]
        parser = argparse.ArgumentParser()
        add_parser(parser.add_subparsers())
        resumed = parser.parse_args(["topic-backfill", "--again-before", moment])
        assert resumed.again_before == datetime.fromisoformat(moment)
        naive = parser.parse_args(["topic-backfill", "--again-before", "2026-10-20T12:00:00"])
        assert naive.again_before == datetime(2026, 10, 20, 12, tzinfo=UTC)
        with pytest.raises(SystemExit):
            parser.parse_args(["topic-backfill", "--again-before", "вчера"])

    async def test_a_no_keeps_everything_as_it_was(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        await _base(session)
        asked = _model(monkeypatch, {})
        monkeypatch.setattr("builtins.input", lambda _prompt: "n")

        code = await run_topic_backfill(session, _args(), http=None, home_client=None)  # type: ignore[arg-type]

        assert code == 1
        assert asked == []
        assert "Не определяли." in capsys.readouterr().out

    async def test_yes_classifies_and_reports(
        self,
        session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        await _base(session)
        monkeypatch.setattr(topic_backfill, "check_home", _opened)
        broken = TopicVerdict(
            None, None, "ответ модели обрезан потолком вывода", "gpt-5-mini", 900,
            unanswered=True, unreadable=True,
        )  # fmt: skip
        _model(
            monkeypatch,
            {
                "saved-page.example": _topic("finance"),
                "judged-home.example": broken,
                "no-text.example": _topic(None),
            },
        )

        code = await run_topic_backfill(
            session,
            _args(yes=True),
            http=None,
            home_client=object(),  # type: ignore[arg-type]
        )

        printed = capsys.readouterr().out
        assert code == 0
        assert "тема определена: 1, не ясна: 1" in printed
        assert "ответ не по форме у 1: ответ модели обрезан потолком вывода" in printed
        assert "главных открыто: 2, не открылось: 0" in printed
        assert "Финансы" in printed


def test_named_in_the_console(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Команда собрана в консоли: пустая база — «определять некому», модель не зовут."""
    monkeypatch.setattr("backend.config.storage.DSN", TEST_DSN)
    monkeypatch.setattr("backend.config.storage.REDIS_URL", "redis://localhost:6379/0")
    monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)

    assert main(["topic-backfill", "--dry-run"]) == 0
    assert "Определить тему: 0 сайтов" in capsys.readouterr().out
