"""Состояние поиска адресов — одно на доноров и рекламодателей Этапа 2.

Проверяется то, что видит строка «ждут адреса» на экране: идущий поиск
не показывает прошлого отчёта, законченный — показывает свой, а задача,
которую очередь уже забыла, не роняет экран.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.api.contacts import search_state as state_module
from backend.features.ops.job_outcome import JobOutcome
from rq.exceptions import NoSuchJobError


class _Result:
    def __init__(self, value: Any) -> None:
        self.return_value = value


class _Job:
    def __init__(self, value: Any, *, finished: bool = True) -> None:
        self.is_finished = finished
        self._value = value

    def latest_result(self) -> _Result:
        return _Result(self._value)


class _Queue:
    connection = object()


@pytest.fixture(autouse=True)
def quiet_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(state_module, "runs_queue", _Queue)


def _fetch_returns(monkeypatch: pytest.MonkeyPatch, job: _Job) -> None:
    monkeypatch.setattr(state_module.Job, "fetch", lambda *_a, **_k: job)


class TestSearchState:
    def test_running_search_hides_the_previous_report(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Пока поиск идёт, «прошлый проход» был бы чужим отчётом."""
        monkeypatch.setattr(state_module, "job_alive", lambda _job: True)
        outcome = JobOutcome(job_id="job-1", kind="поиск контактов", state="running")
        monkeypatch.setattr(state_module, "job_outcome", lambda _job: outcome)

        state = state_module.search_state(3, "job-1", 2)

        assert state.running is True
        assert state.last is None
        assert state.job is not None
        assert state.job.title == "идёт"
        assert state.workers == 2

    def test_finished_search_shows_its_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(state_module, "job_alive", lambda _job: False)
        monkeypatch.setattr(state_module, "job_outcome", lambda _job: None)
        _fetch_returns(monkeypatch, _Job({"walked": 3, "saved": 2}))

        state = state_module.search_state(1, "job-1", 1)

        assert state.running is False
        assert state.last == {"walked": 3, "saved": 2}
        assert state.job is None

    def test_no_job_means_no_questions_to_the_queue(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def asked(_job: str) -> bool:
            raise AssertionError("без номера задачи очередь не спрашивают")

        monkeypatch.setattr(state_module, "job_alive", asked)

        state = state_module.search_state(5, None, None)

        assert state.pending == 5
        assert state.running is False
        assert state.last is None


class TestLastReport:
    def test_forgotten_job_is_no_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Итог живёт в очереди сутки: забытая задача — не сбой экрана."""

        def gone(*_args: Any, **_kwargs: Any) -> _Job:
            raise NoSuchJobError("нет такой")

        monkeypatch.setattr(state_module.Job, "fetch", gone)

        assert state_module.last_report("job-старый") is None

    def test_unfinished_job_has_no_report(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fetch_returns(monkeypatch, _Job({"walked": 1}, finished=False))

        assert state_module.last_report("job-1") is None

    def test_report_that_is_not_a_dict_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _fetch_returns(monkeypatch, _Job("готово"))

        assert state_module.last_report("job-1") is None
