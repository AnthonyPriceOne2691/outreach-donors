"""Здоровье фоновых процессов: зависший цикл обязан стать видимым.

Проверяется не «процесс жив» — это докер знает и сам, — а то, что докер
не знает: цикл встал на зависшем ожидании или крутится вхолостую, падая
на каждом проходе.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.workers import health, ticker
from redis.exceptions import ConnectionError as RedisConnectionError


def _write(directory: Path, name: str, *, age: float, allowed: float, failures: int = 0) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    payload = {"at": time.time() - age, "allowed": allowed, "failures": failures}
    (directory / f"{name}.json").write_text(json.dumps(payload), encoding="utf-8")


class TestBeats:
    def test_fresh_loops_are_healthy(self, tmp_path: Path) -> None:
        _write(tmp_path, "Добивки", age=30, allowed=660)
        _write(tmp_path, "Сторож тишины", age=500, allowed=1200)

        assert health.beat_problems(tmp_path) == []

    def test_stuck_loop_is_named(self, tmp_path: Path) -> None:
        _write(tmp_path, "Добивки", age=30, allowed=660)
        _write(tmp_path, "Разбор мёртвых прогонов", age=900, allowed=660)

        problems = health.beat_problems(tmp_path)

        assert len(problems) == 1
        assert "Разбор мёртвых прогонов" in problems[0]
        assert "завис" in problems[0]

    def test_loop_failing_every_pass_is_not_healthy(self, tmp_path: Path) -> None:
        """Крутится — ещё не работает: база легла, и каждый проход падает."""
        _write(tmp_path, "Добивки", age=5, allowed=660, failures=health.FAILURES_UNHEALTHY)

        assert "подряд с ошибкой" in health.beat_problems(tmp_path)[0]

    def test_one_failure_is_not_an_alarm(self, tmp_path: Path) -> None:
        _write(tmp_path, "Добивки", age=5, allowed=660, failures=1)

        assert health.beat_problems(tmp_path) == []

    def test_no_beats_at_all_is_a_problem(self, tmp_path: Path) -> None:
        """Отметок нет — цикл не начался или пишет не туда. «Нет данных» не
        может значить «здоров»: иначе сломанная запись отметок выглядела бы
        как вечно здоровый процесс."""
        assert health.beat_problems(tmp_path / "нет")
        assert health.beat_problems(tmp_path)

    def test_broken_beat_is_reported_not_crashed(self, tmp_path: Path) -> None:
        (tmp_path / "Добивки.json").write_text("{битый", encoding="utf-8")

        assert "не прочитать" in health.beat_problems(tmp_path)[0]


class TestLoopWritesBeats:
    async def test_loop_beats_and_counts_failures_in_a_row(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ticker, "BEATS_DIR", tmp_path)
        calls = 0

        async def failing() -> None:
            nonlocal calls
            calls += 1
            raise RuntimeError("база легла")

        task = asyncio.create_task(ticker.every(0.01, failing, name="Проба"))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        data = json.loads((tmp_path / "Проба.json").read_text(encoding="utf-8"))
        assert calls >= health.FAILURES_UNHEALTHY
        assert data["failures"] >= health.FAILURES_UNHEALTHY
        assert data["allowed"] == pytest.approx(0.01 + ticker.PASS_BUDGET_SEC)
        # Проверка читает ровно то, что пишет цикл.
        assert "подряд с ошибкой" in health.beat_problems(tmp_path)[0]

    async def test_success_resets_the_count(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(ticker, "BEATS_DIR", tmp_path)
        outcomes = iter([RuntimeError("раз"), RuntimeError("два"), None, None, None, None])

        async def flaky() -> None:
            outcome = next(outcomes, None)
            if outcome is not None:
                raise outcome

        task = asyncio.create_task(ticker.every(0.01, flaky, name="Проба"))
        await asyncio.sleep(0.2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert json.loads((tmp_path / "Проба.json").read_text(encoding="utf-8"))["failures"] == 0
        assert health.beat_problems(tmp_path) == []

    def test_unwritable_beat_does_not_break_the_loop(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blocker = tmp_path / "файл-вместо-папки"
        blocker.write_text("", encoding="utf-8")
        monkeypatch.setattr(ticker, "BEATS_DIR", blocker)

        ticker.beat("Проба", allowed=60, failures=0)  # не бросает


class _Workers:
    """Подмена `Worker.all`: воркеры задаются тестом."""

    def __init__(self, workers: list[SimpleNamespace] | Exception) -> None:
        self.workers = workers

    def __call__(self, **kwargs: object) -> list[SimpleNamespace]:
        if isinstance(self.workers, Exception):
            raise self.workers
        return self.workers


NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


class TestWorker:
    def _check(self, monkeypatch: pytest.MonkeyPatch, workers: object) -> list[str]:
        monkeypatch.setattr(health.Worker, "all", _Workers(workers))  # type: ignore[arg-type]
        monkeypatch.setattr(health, "Queue", lambda name, connection: name)
        return health.worker_problems(None, "runs", hostname="c0ffee", now=NOW)  # type: ignore[arg-type]

    def test_own_fresh_worker_is_healthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        worker = SimpleNamespace(hostname="c0ffee", last_heartbeat=NOW - timedelta(seconds=90))

        assert self._check(monkeypatch, [worker]) == []

    def test_worker_of_another_container_does_not_count(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Живой сосед не делает здоровым этот контейнер."""
        other = SimpleNamespace(hostname="beef", last_heartbeat=NOW)

        assert "не отмечен" in self._check(monkeypatch, [other])[0]

    def test_stale_heartbeat_is_a_hang(self, monkeypatch: pytest.MonkeyPatch) -> None:
        stale = NOW - timedelta(seconds=health.WORKER_STALE_SEC + 30)
        worker = SimpleNamespace(hostname="c0ffee", last_heartbeat=stale)

        assert "завис" in self._check(monkeypatch, [worker])[0]

    def test_redis_down_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        problems = self._check(monkeypatch, RedisConnectionError("нет соединения"))

        assert "очередь не отвечает" in problems[0]


def test_command_says_what_to_check(capsys: pytest.CaptureFixture[str]) -> None:
    assert health.main([]) == 2
    assert "beats" in capsys.readouterr().out
