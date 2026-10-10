"""Исход фоновой задачи словами человека.

Статус приходит перечислением rq, а не строкой: `str()` у него даёт
«JobStatus.FINISHED», и до живой проверки 24.09.2026 все задачи
показывались «в очереди». Тесты подают настоящие значения перечисления.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from backend.features.ops import job_outcome as module
from backend.features.ops.job_outcome import SILENT, job_outcome
from backend.shared.queue import (
    CRAWL_JOB,
    CRAWL_QUEUE_NAME,
    PARSE_JOB,
    QUEUE_NAME,
    SALES_QUEUE_NAME,
    SALES_REPLY_JOB,
)
from backend.workers import jobs
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus

AT = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _job(status: JobStatus, *, value: Any = None, exc: str | None = None) -> SimpleNamespace:
    result = SimpleNamespace(return_value=value, exc_string=exc) if (value or exc) else None
    return SimpleNamespace(
        id="job-1",
        func_name="backend.workers.jobs.build_letter_queue",
        origin=QUEUE_NAME,
        retries_left=2,
        ended_at=AT,
        get_status=lambda refresh=True: status,
        latest_result=lambda: result,
    )


@pytest.fixture
def fetched(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    box: dict[str, Any] = {}

    def fetch(job_id: str, connection: object) -> SimpleNamespace:
        found = box["job"]
        if isinstance(found, Exception):
            raise found
        return found

    monkeypatch.setattr(module.Job, "fetch", fetch)
    monkeypatch.setattr(module, "job_error", lambda job_id, conn: box.get("remembered"))
    monkeypatch.setattr(module, "_next_try", lambda job, conn: AT)
    return box


def test_finished_with_a_report_is_done(fetched: dict[str, Any]) -> None:
    fetched["job"] = _job(JobStatus.FINISHED, value={"prepared": 3})
    outcome = job_outcome("job-1", redis=object())  # type: ignore[arg-type]
    assert outcome is not None
    assert (outcome.state, outcome.title, outcome.kind) == ("done", "готово", "сборка писем")
    assert outcome.report == {"prepared": 3}


def test_finished_with_a_reason_is_refused(fetched: dict[str, Any]) -> None:
    """Постоянный отказ возвращается итогом, а не падением."""
    fetched["job"] = _job(
        JobStatus.FINISHED, value={"error": "TemplateError: нет зоны", "permanent": True}
    )
    outcome = job_outcome("job-1", redis=object())  # type: ignore[arg-type]
    assert outcome is not None
    # Имя класса — для журнала, на экран выходит сообщение (правило
    # `runs/reasons.py`); в самой очереди текст остаётся полным.
    assert (outcome.state, outcome.error) == ("refused", "нет зоны")


def test_waiting_for_retry_names_the_reason_and_the_time(fetched: dict[str, Any]) -> None:
    fetched["job"] = _job(JobStatus.SCHEDULED)
    fetched["remembered"] = "ConnectError: сеть"
    outcome = job_outcome("job-1", redis=object())  # type: ignore[arg-type]
    assert outcome is not None
    assert (outcome.state, outcome.title) == ("retry_wait", "ждёт повтора")
    assert outcome.error == "сеть"
    assert outcome.next_try_at == AT
    assert outcome.retries_left == 2


def test_failed_carries_the_last_exception_line(fetched: dict[str, Any]) -> None:
    trace = "Traceback (most recent call last):\n  File x\nRuntimeError: база легла\n"
    fetched["job"] = _job(JobStatus.FAILED, exc=trace)
    outcome = job_outcome("job-1", redis=object())  # type: ignore[arg-type]
    assert outcome is not None
    assert (outcome.state, outcome.error) == ("failed", "база легла")


def test_foreign_message_is_named_in_general_words(fetched: dict[str, Any]) -> None:
    """Чужое сообщение (библиотека, сеть) — общими словами и именем в скобках:
    по имени сбой находят в журнале, а английская строка человеку не нужна."""
    trace = "Traceback (most recent call last):\n  File x\nhttpx.ReadTimeout: timed out\n"
    fetched["job"] = _job(JobStatus.FAILED, exc=trace)
    outcome = job_outcome("job-1", redis=object())  # type: ignore[arg-type]
    assert outcome is not None
    assert outcome.error == "техническая ошибка (ReadTimeout)"


def test_lost_reason_is_said_in_words_not_by_queue_status(fetched: dict[str, Any]) -> None:
    """Без сохранённой причины раньше выходило «задача failed»."""
    fetched["job"] = _job(JobStatus.FAILED)
    outcome = job_outcome("job-1", redis=object())  # type: ignore[arg-type]
    assert outcome is not None
    assert outcome.error == "причина не сохранилась"


@pytest.mark.parametrize(
    ("status", "state"), [(JobStatus.QUEUED, "queued"), (JobStatus.STARTED, "running")]
)
def test_waiting_and_running(fetched: dict[str, Any], status: JobStatus, state: str) -> None:
    fetched["job"] = _job(status)
    outcome = job_outcome("job-1", redis=object())  # type: ignore[arg-type]
    assert outcome is not None
    assert outcome.state == state


def test_unknown_job_and_silent_queue_are_different(fetched: dict[str, Any]) -> None:
    fetched["job"] = NoSuchJobError("нет")
    assert job_outcome("job-1", redis=object()) is None  # type: ignore[arg-type]

    fetched["job"] = RedisConnectionError("нет соединения")
    outcome = job_outcome("job-1", redis=object())  # type: ignore[arg-type]
    assert outcome is not None
    assert (outcome.state, outcome.title) == ("unknown", "очередь не отвечает")


def test_silent_queue_does_not_show_its_address(
    fetched: dict[str, Any], caplog: pytest.LogCaptureFixture
) -> None:
    """Текст redis-py несёт адрес и порт очереди: на экран — слова, сам текст — в журнал полем."""
    raw = "Error 111 connecting to redis:6379. Connection refused."
    fetched["job"] = RedisConnectionError(raw)

    with caplog.at_level(logging.WARNING, logger="backend.features.ops.job_outcome"):
        outcome = job_outcome("job-1", redis=object())  # type: ignore[arg-type]

    assert outcome is not None
    assert outcome.error == SILENT
    assert "6379" not in outcome.error
    assert [getattr(record, "error", None) for record in caplog.records] == [raw]
    assert all("6379" not in record.getMessage() for record in caplog.records)


class _Scheduled:
    """Redis ровно настолько, насколько его читает реестр отложенных задач rq:
    отложенная задача лежит в отсортированном наборе СВОЕЙ очереди."""

    def __init__(self, queue: str, job_id: str, at: datetime) -> None:
        self._sets = {f"rq:scheduled:{queue}": {job_id: at.timestamp()}}

    def zscore(self, key: str, member: str) -> float | None:
        return self._sets.get(key, {}).get(member)


@pytest.mark.parametrize(
    ("origin", "path"),
    [(SALES_QUEUE_NAME, SALES_REPLY_JOB), (CRAWL_QUEUE_NAME, CRAWL_JOB), (QUEUE_NAME, PARSE_JOB)],
)
def test_retry_time_is_read_from_the_queue_the_job_came_from(
    monkeypatch: pytest.MonkeyPatch, origin: str, path: str
) -> None:
    """Задача ждёт повтора в отложенных своей очереди. До 07.10.2026 время искали
    только в `runs`, и у ответа продаж (`sales`) и обхода Этапа 2 (`crawl`)
    «ждёт повтора» стояло без времени. Задача, реестр и очередь — настоящие rq."""
    conn: Any = _Scheduled(origin, "job-1", AT)
    job = Job.create(path, args=(5,), connection=conn, id="job-1", origin=origin)
    monkeypatch.setattr(job, "get_status", lambda refresh=True: JobStatus.SCHEDULED)
    monkeypatch.setattr(job, "latest_result", lambda: None)
    monkeypatch.setattr(module.Job, "fetch", lambda job_id, connection: job)
    monkeypatch.setattr(module, "job_error", lambda job_id, conn: "ConnectError: сеть")

    outcome = job_outcome("job-1", redis=conn)

    assert outcome is not None
    assert (outcome.state, outcome.next_try_at, outcome.error) == ("retry_wait", AT, "сеть")


class TestJobsSettleTheirOutcome:
    def test_permanent_refusal_is_an_outcome_not_a_crash(self) -> None:
        from backend.features.letters.template import TemplateError  # noqa: PLC0415

        def broken() -> dict[str, Any]:
            raise TemplateError("в шаблоне нет зоны offer")

        result = jobs._settled(broken, what="сборка писем")
        assert result == {"error": "TemplateError: в шаблоне нет зоны offer", "permanent": True}

    def test_passing_trouble_is_remembered_and_raised_for_retry(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        remembered: list[tuple[str, str]] = []
        monkeypatch.setattr(jobs, "get_current_job", lambda: SimpleNamespace(id="job-9"))
        monkeypatch.setattr(
            jobs, "remember_job_error", lambda job_id, text: remembered.append((job_id, text))
        )

        def flaky() -> dict[str, Any]:
            raise ConnectionError("провайдер не ответил")

        with pytest.raises(ConnectionError):
            jobs._settled(flaky, what="поиск контактов")
        assert remembered == [("job-9", "ConnectionError: провайдер не ответил")]
