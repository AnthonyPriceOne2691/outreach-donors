"""Помощники очереди: что они отвечают, когда Redis молчит или задачи нет.

Задачи, экраны и разбор мёртвых прогонов стоят на этих ответах, а тесты
задач подменяют помощников целиком — и сами помощники до 04.10.2026 не
проверялись ничем (гейт покрытия изменённых файлов на #160: 46%). Здесь —
их обещания: `None` значит «не выяснили», а не «мертва»; сбой Redis не
роняет вызывающего; трассировка сворачивается в строку исключения.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.shared import queue
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.exceptions import NoSuchJobError


class FakeRedis:
    """Хранилище строк в памяти; `broken` — Redis недоступен."""

    def __init__(self, *, broken: bool = False) -> None:
        self.broken = broken
        self.data: dict[str, Any] = {}

    def _check(self) -> None:
        if self.broken:
            raise RedisConnectionError("Redis недоступен")

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self._check()
        self.data[key] = value.encode()

    def get(self, key: str) -> bytes | None:
        self._check()
        return self.data.get(key)

    def exists(self, key: str) -> int:
        self._check()
        return int(key in self.data)


class FakeJob:
    def __init__(self, state: str, *, worker: str | None = None, trace: str | None = None) -> None:
        self.state = state
        self.worker_name = worker
        self.trace = trace

    def get_status(self, refresh: bool = True) -> str:
        return self.state

    def latest_result(self) -> Any:
        if self.trace is None:
            return None
        return type("Result", (), {"exc_string": self.trace})()


def _fetching(found: FakeJob | Exception) -> Any:
    def fetch(job_id: str, connection: Any) -> FakeJob:
        if isinstance(found, Exception):
            raise found
        return found

    return fetch


class TestRememberedErrors:
    def test_error_is_kept_and_read_back(self) -> None:
        redis = FakeRedis()
        queue.remember_job_error("job-1", "ConnectionError: провайдер не ответил", redis)  # type: ignore[arg-type]
        assert queue.job_error("job-1", redis) == "ConnectionError: провайдер не ответил"  # type: ignore[arg-type]

    def test_broken_redis_does_not_break_the_job(self) -> None:
        queue.remember_job_error("job-1", "сбой", FakeRedis(broken=True))  # type: ignore[arg-type]


class TestContactsJob:
    def test_number_is_remembered_and_read(self) -> None:
        redis = FakeRedis()
        queue.remember_contacts_job("contacts-7", redis)  # type: ignore[arg-type]
        assert queue.contacts_job_id(redis) == "contacts-7"  # type: ignore[arg-type]

    def test_nothing_remembered_is_none(self) -> None:
        assert queue.contacts_job_id(FakeRedis()) is None  # type: ignore[arg-type]

    def test_silent_redis_is_none_not_a_crash(self) -> None:
        broken = FakeRedis(broken=True)
        queue.remember_contacts_job("contacts-7", broken)  # type: ignore[arg-type]
        assert queue.contacts_job_id(broken) is None  # type: ignore[arg-type]


class TestJobAlive:
    def test_no_number_is_unknown(self) -> None:
        assert queue.job_alive(None) is None

    @pytest.mark.parametrize("missing", [NoSuchJobError("нет"), ValueError("негодный номер")])
    def test_missing_job_is_dead(self, monkeypatch: pytest.MonkeyPatch, missing: Exception) -> None:
        monkeypatch.setattr(queue.Job, "fetch", _fetching(missing))
        assert queue.job_alive("job-1", FakeRedis()) is False  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        ("state", "alive"), [("queued", True), ("scheduled", True), ("finished", False)]
    )
    def test_state_decides_unless_started(
        self, monkeypatch: pytest.MonkeyPatch, state: str, alive: bool
    ) -> None:
        monkeypatch.setattr(queue.Job, "fetch", _fetching(FakeJob(state)))
        assert queue.job_alive("job-1", FakeRedis()) is alive  # type: ignore[arg-type]

    def test_started_lives_only_with_its_worker(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """«Выполняется» остаётся и после смерти воркера — решает его ключ."""
        redis = FakeRedis()
        monkeypatch.setattr(queue.Job, "fetch", _fetching(FakeJob("started", worker="w1")))
        assert queue.job_alive("job-1", redis) is False  # type: ignore[arg-type]
        redis.data[f"{queue.WORKER_KEY_PREFIX}w1"] = b"1"
        assert queue.job_alive("job-1", redis) is True  # type: ignore[arg-type]
        monkeypatch.setattr(queue.Job, "fetch", _fetching(FakeJob("started", worker=None)))
        assert queue.job_alive("job-1", redis) is False  # type: ignore[arg-type]

    def test_silent_redis_is_unknown_not_dead(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Неизвестность за смерть не принимается: иначе прогон заплатят дважды."""
        monkeypatch.setattr(queue.Job, "fetch", _fetching(RedisConnectionError("нет связи")))
        assert queue.job_alive("job-1", FakeRedis()) is None  # type: ignore[arg-type]


class TestJobFailure:
    def test_failed_job_names_its_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        trace = "Traceback (most recent call last):\n  File x\nMissingGreenlet: greenlet_spawn\n\n"
        monkeypatch.setattr(queue.Job, "fetch", _fetching(FakeJob("failed", trace=trace)))
        assert queue.job_failure("job-1", FakeRedis()) == "MissingGreenlet: greenlet_spawn"  # type: ignore[arg-type]

    @pytest.mark.parametrize(
        "found",
        [FakeJob("finished"), NoSuchJobError("нет"), RedisConnectionError("нет связи")],
    )
    def test_no_failure_or_no_answer_is_none(
        self, monkeypatch: pytest.MonkeyPatch, found: FakeJob | Exception
    ) -> None:
        monkeypatch.setattr(queue.Job, "fetch", _fetching(found))
        assert queue.job_failure("job-1", FakeRedis()) is None  # type: ignore[arg-type]

    def test_no_number_is_none(self) -> None:
        assert queue.job_failure(None) is None


class TestPlumbing:
    def test_workers_unknown_when_redis_is_silent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def silent(**_: object) -> list[object]:
            raise RedisConnectionError("нет связи")

        monkeypatch.setattr(queue.Worker, "all", silent)
        assert queue.workers_alive(FakeRedis()) is None  # type: ignore[arg-type]

    def test_queue_is_ours_with_the_long_timeout(self) -> None:
        built = queue.runs_queue(queue.connection())
        assert built.name == queue.QUEUE_NAME
        assert built._default_timeout == queue.JOB_TIMEOUT

    def test_last_line_of_an_empty_trace_is_none(self) -> None:
        assert queue.last_error_line(None) is None
        assert queue.last_error_line("  \n ") is None
