"""Зависший прогон сбора (тест прогона 14.10): сбой после последнего повтора — «остановлен»
словами; задачи нет или умер её воркер — «остановлен» и «Продолжить»; воркеров нет —
«Задачу некому взять»; очередь не ответила — «не знаю», а не «мертва»; причина — без имени
класса и имён настроек; продолжение не трогает стадию и убирает задачу умершего воркера;
то же решение по сайту второй раз — 200 и одна запись журнала.

Очередь задач — подделка с правилами rq (`test_sales_clean_api._Jobs`), ответы Redis о
живости — подменой помощников `shared/queue` в `segment_watch`.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from backend.api.sales import segment as segment_api
from backend.config.startup_checks import ConfigError
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.sales import segment_jobs, segment_progress, segment_watch
from backend.features.sales.segment_estimate import SERP_AHREFS
from backend.features.sales.segment_jobs import SerpNotSetError, segment_job_id
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from httpx import AsyncClient
from rq.job import JobStatus
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_segment_api import RUNS, SEGMENT_URL, _review_site, headers, jobs
from tests.test_sales_segment_run import hypothesis, make_run

__all__ = ["headers", "jobs"]  # вход и подделка очереди — те же, что у маршрутов сбора

Run = SalesSegmentRunModel


# --- причина словами ---------------------------------------------------------------------------


def test_our_words_go_without_the_class_name() -> None:
    """«SerpNotSetError: …» на экране продаж (тест прогона 14.10) — имя класса для журнала."""
    assert segment_jobs.stop_words(SerpNotSetError(SERP_AHREFS)) == SERP_AHREFS


def test_words_that_name_a_setting_are_replaced_and_go_to_the_journal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    refused = ConfigError("SALES_VERIFIER_PROVIDER=live, а CONTACTS_HUNTER_API_KEY пуст")

    words = segment_jobs.stop_words(refused)

    assert words == segment_jobs.SETTINGS_WORDS
    assert "SALES_VERIFIER" not in words
    assert any("SALES_VERIFIER_PROVIDER" in str(r.__dict__.get("error")) for r in caplog.records)


def test_a_foreign_error_is_named_in_general_words() -> None:
    assert segment_jobs.stop_words(httpx.ReadTimeout("timed out")) == (
        "техническая ошибка (ReadTimeout)"
    )


# --- последний повтор задачи -----------------------------------------------------------------------


class _Job:
    def __init__(self, retries_left: int | None) -> None:
        self.id = "sales-segment-7"
        self.retries_left = retries_left


@pytest.fixture
def broken_pass(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, str]]:
    """Проход падает временным сбоем; что легло бы в строку прогона — в список."""
    stopped: list[tuple[int, str]] = []

    async def execute(_session: AsyncSession, run_id: int, _deps: object) -> dict[str, Any]:
        raise ConnectionError(f"база не ответила на прогоне {run_id}")

    async def stop(_factory: object, run_id: int, words: str) -> None:
        stopped.append((run_id, words))

    async def no_pulse(*_args: object, **_kw: object) -> None:
        return None

    monkeypatch.setattr(segment_jobs, "execute", execute)
    monkeypatch.setattr(segment_jobs, "_stop", stop)
    monkeypatch.setattr(segment_jobs, "pulse", no_pulse)
    monkeypatch.setattr("backend.config.sales.ENABLED", True)
    monkeypatch.setattr("backend.config.sales.VERIFIER_PROVIDER", "fixture")
    monkeypatch.setattr("backend.config.serp.PROVIDER", "dataforseo")
    monkeypatch.setattr("backend.config.serp.LOGIN", "login-test")
    monkeypatch.setattr("backend.config.serp.PASSWORD", "password-test")  # pragma: allowlist secret
    monkeypatch.setattr("backend.config.storage.DSN", TEST_DSN)
    return stopped


async def test_a_passing_failure_on_the_last_try_stops_the_run_in_words(
    broken_pass: list[tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """До 14.10 после трёх повторов задача rq «упала», а строка прогона оставалась «идёт» с
    пустой причиной навсегда."""
    monkeypatch.setattr(segment_jobs, "get_current_job", lambda: _Job(retries_left=0))

    with pytest.raises(ConnectionError):
        await segment_jobs.run_once(7)

    # Без имени класса: «ConnectionError: …» — для журнала, на экране — слова сбоя.
    assert broken_pass == [
        (
            7,
            "сбой не прошёл и после повторов задачи: база не ответила на прогоне 7 — "
            "продолжите прогон, когда он пройдёт",
        )
    ]


async def test_a_passing_failure_with_tries_left_waits_for_the_queue_retry(
    broken_pass: list[tuple[int, str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(segment_jobs, "get_current_job", lambda: _Job(retries_left=2))

    with pytest.raises(ConnectionError):
        await segment_jobs.run_once(7)

    assert broken_pass == []


# --- жив ли прогон: ответ очереди -----------------------------------------------------------------


@pytest.fixture
def queue_says(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Что очередь ответит о задаче прогона: жива ли, почему упала, сколько воркеров."""
    said: dict[str, Any] = {"alive": True, "failure": None, "workers": 1, "job": "started"}
    monkeypatch.setattr(segment_watch, "job_alive", lambda _job, **_kw: said["alive"])
    monkeypatch.setattr(segment_watch, "job_failure", lambda _job: said["failure"])
    monkeypatch.setattr(segment_watch, "workers_alive", lambda **_kw: said["workers"])
    monkeypatch.setattr(segment_watch, "job_state", lambda _job: said["job"])
    return said


@pytest.mark.parametrize("stage", [Stage.QUEUED, Stage.SERP, Stage.JUDGE, Stage.LEADS])
def test_a_going_run_whose_job_is_gone_is_stalled_in_words(
    queue_says: dict[str, Any], stage: Stage
) -> None:
    queue_says["alive"] = False

    found = segment_watch.watch(7, stage, workers=1)

    assert (found.going, found.stalled, found.silent) == (False, segment_watch.LOST, False)


def test_a_failed_job_names_its_failure_without_the_class(queue_says: dict[str, Any]) -> None:
    queue_says.update(alive=False, failure="SerpError: Провайдер выдачи ответил 503")

    found = segment_watch.watch(7, Stage.SERP, workers=1)

    assert found.stalled == (
        "задача прогона упала: Провайдер выдачи ответил 503 — сделанное сохранено, "
        "продолжите прогон"
    )


def test_a_live_job_says_whether_a_worker_has_it(queue_says: dict[str, Any]) -> None:
    """Продолжение зависшего прогона стоит в очереди, а стадия у него — «судья» прошлого
    прохода: экран должен сказать «в очереди задач», а не «судит»."""
    queue_says["job"] = "queued"

    found = segment_watch.watch(7, Stage.JUDGE, workers=1)

    assert (found.going, found.job) == (True, "queued")


def test_the_job_state_is_the_value_of_the_rq_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """`str()` у перечисления rq — «JobStatus.QUEUED», и сравнение со строкой молча не
    срабатывало (урок 24.09): состояние — `.value`."""

    class _Fetched:
        @staticmethod
        def fetch(job_id: str, connection: object) -> Any:
            class _Found:
                def get_status(self, refresh: bool = True) -> JobStatus:
                    return JobStatus.SCHEDULED

            return _Found()

    monkeypatch.setattr(segment_watch, "Job", _Fetched)
    monkeypatch.setattr(segment_watch, "connection", object)

    assert segment_watch.job_state("sales-segment-7") == "scheduled"


def test_an_unknown_answer_is_not_death(queue_says: dict[str, Any]) -> None:
    """Redis не ответил: «не знаю» — не «мертва», иначе экран звал бы продолжить идущий
    прогон, а это вторая задача и второй счёт."""
    queue_says["alive"] = None

    found = segment_watch.watch(7, Stage.JUDGE, workers=None)

    assert (found.going, found.stalled, found.silent) == (True, None, True)


def test_a_finished_run_without_a_live_job_is_just_finished(queue_says: dict[str, Any]) -> None:
    queue_says["alive"] = False

    found = segment_watch.watch(7, Stage.DONE, workers=1)

    assert (found.going, found.stalled) == (False, None)


# --- экран: прогон и список ---------------------------------------------------------------------


async def test_a_going_run_without_its_job_reads_as_stopped_with_words(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], queue_says: dict[str, Any]
) -> None:
    run = await make_run(session, await hypothesis(session))
    run.stage = Stage.JUDGE
    await session.commit()
    queue_says["alive"] = False

    found = (await client.get(f"{RUNS}/{run.id}", headers=headers)).json()["run"]

    assert (found["stage"], found["active"]) == ("judge", False)
    assert found["watch"]["stalled"] == segment_watch.LOST
    # Строку прогона чтение не правит: вывод — из очереди, запись — свидетельство.
    await session.refresh(run)
    assert (run.stage, run.error) == (Stage.JUDGE, None)


async def test_a_queued_run_nobody_can_take_says_so(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], queue_says: dict[str, Any]
) -> None:
    owner = await hypothesis(session)
    run = await make_run(session, owner)
    queue_says["workers"] = 0

    home = (await client.get(f"{SEGMENT_URL}?hypothesis={owner.id}", headers=headers)).json()

    [row] = home["runs"]
    assert row["id"] == run.id
    assert (row["active"], row["watch"]["workers"], row["watch"]["stalled"]) == (True, 0, None)
    assert row["watch"]["job"] == "started"


async def test_a_finished_run_is_not_asked_about_and_a_resumed_one_goes(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    queue_says: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Кончившийся прогон очередь не спрашивает; продолжение — проход без `finished_at`, и
    прогон идёт, пока его задача жива, — без возврата стадии в «очередь»."""
    asked: list[list[tuple[int, str]]] = []
    watch_runs = segment_watch.watch_runs

    def counted(runs: list[tuple[int, str]]) -> dict[int, segment_watch.Watch]:
        asked.append(list(runs))
        return watch_runs(runs)

    monkeypatch.setattr(segment_watch, "watch_runs", counted)
    run = await make_run(session, await hypothesis(session))
    run.stage = Stage.DONE
    run.finished_at = run.created_at
    await session.commit()

    done = (await client.get(f"{RUNS}/{run.id}", headers=headers)).json()["run"]
    run.finished_at = None
    await session.commit()
    resumed = (await client.get(f"{RUNS}/{run.id}", headers=headers)).json()["run"]

    assert (done["active"], done["watch"]) == (False, None)
    assert (resumed["stage"], resumed["active"]) == ("done", True)
    assert asked == [[(run.id, "done")]]


async def test_the_run_answers_its_pulse_and_live_progress(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str], queue_says: dict[str, Any]
) -> None:
    run = await make_run(session, await hypothesis(session))
    run.stage = Stage.JUDGE
    run.report = {
        "progress": segment_progress.entered(
            Stage.JUDGE, total=0, pass_started=run.created_at, moment=run.created_at
        )
    }
    await session.commit()

    found = (await client.get(f"{RUNS}/{run.id}", headers=headers)).json()["run"]

    assert found["alive_at"] is not None
    assert (found["progress"]["stage"], found["progress"]["done"], found["progress"]["total"]) == (
        "judge",
        0,
        0,
    )
    assert found["hypothesis_id"] == run.hypothesis_id


# --- продолжение ------------------------------------------------------------------------------------


async def test_resume_keeps_the_stage_and_opens_the_pass(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    queue_says: dict[str, Any],
) -> None:
    """Ловушка: «в очереди» значило бы для прохода «купить выдачу». Стадия остаётся, проход
    открыт — пуст `finished_at`, и экран видит прогон идущим по очереди."""
    run = await make_run(session, await hypothesis(session))
    run.stage, run.error, run.finished_at = Stage.STOPPED, "судья остановлен", run.created_at
    await session.commit()

    response = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert response.status_code == 202, response.text
    await session.refresh(run)
    assert (run.stage, run.finished_at) == (Stage.STOPPED, None)
    assert len(jobs.enqueued) == 1


async def test_resume_after_the_worker_died_takes_the_dead_job_away(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    queue_says: dict[str, Any],
) -> None:
    """Воркер умер посреди прохода: rq держит задачу «идёт» до таймаута (час), и продолжение
    отвечало бы «уже идёт». Мёртвая убирается, ставится новая."""
    run = await make_run(session, await hypothesis(session))
    await client.post(f"{RUNS}/{run.id}/resume", headers=headers)
    dead = jobs.known[segment_job_id(run.id)]
    dead.status = JobStatus.STARTED
    queue_says["alive"] = False

    again = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert again.status_code == 202, again.text
    assert dead.deleted
    assert len(jobs.enqueued) == 2


async def test_resume_of_a_job_that_really_runs_is_refused(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    jobs: _Jobs,
    queue_says: dict[str, Any],
) -> None:
    run = await make_run(session, await hypothesis(session))
    await client.post(f"{RUNS}/{run.id}/resume", headers=headers)
    jobs.known[segment_job_id(run.id)].status = JobStatus.STARTED
    queue_says["alive"] = True

    again = await client.post(f"{RUNS}/{run.id}/resume", headers=headers)

    assert (again.status_code, again.json()["detail"]) == (409, segment_api.RUN_GOING)
    assert not jobs.known[segment_job_id(run.id)].deleted


# --- решение человека: повтор ---------------------------------------------------------------------


async def test_the_same_decision_twice_is_one_decision_and_one_journal_record(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Двойной клик по «Принять» отвечал 409, хотя сайт и так принят. Повтор — 200 и та же
    строка; противоположное по решённому — по-прежнему 409 словами."""
    _, candidate = await _review_site(session)
    url = f"/api/sales/segment/candidates/{candidate.id}/decision"

    first = await client.post(url, json={"accept": True}, headers=headers)
    second = await client.post(url, json={"accept": True}, headers=headers)
    opposite = await client.post(url, json={"accept": False}, headers=headers)

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json() == second.json()
    assert (opposite.status_code, opposite.json()["detail"]) == (
        409,
        "сайт не ждёт решения: он уже «принят» — обновите отчёт прогона",
    )
    records = await session.scalar(
        select(func.count()).where(AuditLogModel.action == AuditAction.SITE_REVIEWED)
    )
    assert records == 1


# --- режим выдачи по умолчанию ----------------------------------------------------------------------


async def test_the_tab_names_the_default_serp_mode_of_the_service(
    session: AsyncSession,
    client: AsyncClient,
    headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Форма сбора ставит режим настройки сервиса, а не своё «быстрый»; настройка негодная —
    пусто, а не отказ всей вкладки."""
    owner = await hypothesis(session)
    monkeypatch.setattr("backend.config.serp.MODE", "standard")

    found = (await client.get(f"{SEGMENT_URL}?hypothesis={owner.id}", headers=headers)).json()
    monkeypatch.setattr("backend.config.serp.MODE", "turbo")
    broken = await client.get(f"{SEGMENT_URL}?hypothesis={owner.id}", headers=headers)

    assert found["serp_mode"] == "standard"
    assert (broken.status_code, broken.json()["serp_mode"]) == (200, None)
