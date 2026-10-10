"""Сборка и пачка писем — одна задача на этап и аудиторию за раз (проверка QA 10.10.2026).

Двойное «Собрать 50» ставило две сборки: вторая писала следующие 50 писем и второй раз
тратила модель. Двойное «Отправить N» ставило две пачки: вторая брала письма сверх тех N,
что человек подтвердил. Теперь вторая задача, пока первая стоит, идёт или ждёт повтора, —
409 словами; кончившаяся задачу следующей не держит.

Очередь здесь — подставная, с правилами rq 2.12, на которых стоит постановка (проверены
на настоящем Redis): занятый номер при `unique=True` — `DuplicateJobError`, и законченная
задача номер тоже занимает, пока её след не убран; итог rq хранит отдельно от задачи.
"""

from __future__ import annotations

import threading
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.api.letters import once
from backend.features.core.domain import Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.shared.queue import RESULT_TTL, SEND_QUEUE_JOB
from httpx import AsyncClient, Response
from rq.exceptions import DuplicateJobError
from rq.job import JobStatus
from rq.results import Result
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_letters_send_queue import _queue

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


class _Redis:
    """Что постановка стирает в Redis сама, мимо задачи: итог прежней."""

    def __init__(self) -> None:
        self.deleted: list[str] = []

    def delete(self, *keys: str) -> None:
        self.deleted.extend(keys)


class _Job:
    def __init__(self, jobs: _Jobs, job_id: str) -> None:
        self.id = job_id
        self.status = JobStatus.QUEUED
        self.connection = jobs.redis
        self._jobs = jobs

    def get_status(self) -> JobStatus:
        return self.status

    def delete(self) -> None:
        self._jobs.known.pop(self.id, None)


class _Jobs:
    """Очередь задач rq 2.12 в том, что нужно постановке: задача по номеру, `unique=True`."""

    def __init__(self) -> None:
        self.redis = _Redis()
        self.known: dict[str, _Job] = {}
        self.enqueued: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
        self.threads: list[int] = []

    def fetch_job(self, job_id: str) -> _Job | None:
        return self.known.get(job_id)

    def enqueue(self, *args: Any, **kwargs: Any) -> _Job:
        self.threads.append(threading.get_ident())
        job_id = str(kwargs["job_id"])
        if kwargs.get("unique") and job_id in self.known:
            raise DuplicateJobError(f"Job with ID '{job_id}' already exists")
        self.enqueued.append((args, kwargs))
        self.known[job_id] = _Job(self, job_id)
        return self.known[job_id]


@pytest.fixture
def jobs(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    found = _Jobs()
    monkeypatch.setattr("backend.api.letters.routes.runs_queue", lambda: found)
    return found


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


async def _build(client: AsyncClient, token: str, **body: Any) -> Response:
    return await client.post(
        "/api/letters/build", json={"campaign": "Май", **body}, headers=bearer(token)
    )


async def _send(client: AsyncClient, token: str) -> Response:
    return await client.post(
        "/api/letters/send-queue", json={"stage": "donors"}, headers=bearer(token)
    )


class TestBuildOnce:
    async def test_second_build_while_the_first_runs_is_refused_in_words(
        self, client: AsyncClient, token: str, jobs: _Jobs
    ) -> None:
        first = await _build(client, token)
        second = await _build(client, token)

        assert first.status_code == 200, first.text
        assert first.json()["job_id"] == once.build_job_id(Stage.DONORS, "links")
        assert (second.status_code, second.json()["detail"]) == (409, once.BUILD_RUNNING)
        assert len(jobs.enqueued) == 1

    @pytest.mark.parametrize(
        "ended", [JobStatus.FINISHED, JobStatus.FAILED, JobStatus.STOPPED, JobStatus.CANCELED]
    )
    async def test_an_ended_build_does_not_hold_the_next_one(
        self, client: AsyncClient, token: str, jobs: _Jobs, ended: JobStatus
    ) -> None:
        """След готовой задачи лежит неделю, упавшей — год: одним `unique=True` новая сборка
        всё это время получала бы 409. Кончившаяся убирается — вместе с итогом: rq хранит
        его отдельно, и строка новой задачи показала бы отчёт прежней."""
        await _build(client, token)
        job_id = once.build_job_id(Stage.DONORS, "links")
        jobs.known[job_id].status = ended

        again = await _build(client, token)

        assert again.status_code == 200, again.text
        assert len(jobs.enqueued) == 2
        assert jobs.known[job_id].status is JobStatus.QUEUED
        assert jobs.redis.deleted == [Result.get_key(job_id)]

    @pytest.mark.parametrize("waits", [JobStatus.STARTED, JobStatus.SCHEDULED, JobStatus.DEFERRED])
    async def test_a_build_that_goes_or_waits_for_a_retry_still_holds(
        self, client: AsyncClient, token: str, jobs: _Jobs, waits: JobStatus
    ) -> None:
        """Идёт или ждёт повтора после сбоя — сборка ещё не кончилась."""
        await _build(client, token)
        jobs.known[once.build_job_id(Stage.DONORS, "links")].status = waits

        second = await _build(client, token)

        assert second.status_code == 409
        assert len(jobs.enqueued) == 1

    async def test_two_clicks_racing_past_the_check_build_once(
        self, client: AsyncClient, token: str, jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Прежняя сборка кончилась, оба нажатия её увидели; пока одно убирало её след,
        другое поставило свою. Второй постановке `unique=True` отвечает «занято» — 409."""
        await _build(client, token)
        job_id = once.build_job_id(Stage.DONORS, "links")
        ended = jobs.known[job_id]
        ended.status = JobStatus.FINISHED

        def removed_and_taken_meanwhile() -> None:
            jobs.known[job_id] = _Job(jobs, job_id)

        monkeypatch.setattr(ended, "delete", removed_and_taken_meanwhile)

        late = await _build(client, token)

        assert (late.status_code, late.json()["detail"]) == (409, once.BUILD_RUNNING)
        assert len(jobs.enqueued) == 1

    async def test_each_tab_builds_its_own_queue(
        self, client: AsyncClient, token: str, jobs: _Jobs
    ) -> None:
        """Доноры, рекламодатели и бизнесы ниши — разные очереди: сборка одной другую
        не держит."""
        donors = await _build(client, token)
        links = await _build(client, token, stage="advertisers")
        niche = await _build(client, token, stage="advertisers", audience="niche")

        assert [r.status_code for r in (donors, links, niche)] == [200, 200, 200]
        assert {kwargs["job_id"] for _, kwargs in jobs.enqueued} == {
            "letters-build-donors-links",
            "letters-build-advertisers-links",
            "letters-build-advertisers-niche",
        }

    async def test_redis_is_asked_off_the_event_loop(
        self, client: AsyncClient, token: str, jobs: _Jobs
    ) -> None:
        """Клиент Redis синхронный: задумавшийся Redis не держит остальные запросы сервиса."""
        await _build(client, token)

        assert jobs.threads != [threading.get_ident()]


class TestSendOnce:
    async def test_second_batch_while_the_first_sends_is_refused_in_words(
        self,
        client: AsyncClient,
        token: str,
        jobs: _Jobs,
        session: AsyncSession,
        filled_legal: None,
    ) -> None:
        await _queue(session, 2)

        first = await _send(client, token)
        second = await _send(client, token)

        assert first.status_code == 200, first.text
        assert (second.status_code, second.json()["detail"]) == (409, once.SEND_RUNNING)
        [(args, kwargs)] = jobs.enqueued
        assert args[0] == SEND_QUEUE_JOB
        assert kwargs["job_id"] == once.send_job_id(Stage.DONORS, "links")

    async def test_the_next_batch_goes_after_the_first_ended(
        self,
        client: AsyncClient,
        token: str,
        jobs: _Jobs,
        session: AsyncSession,
        filled_legal: None,
    ) -> None:
        await _queue(session, 2)
        await _send(client, token)
        jobs.known[once.send_job_id(Stage.DONORS, "links")].status = JobStatus.FINISHED

        again = await _send(client, token)

        assert again.status_code == 200, again.text
        assert len(jobs.enqueued) == 2

    async def test_the_batch_result_is_kept_for_a_week(
        self,
        client: AsyncClient,
        token: str,
        jobs: _Jobs,
        session: AsyncSession,
        filled_legal: None,
    ) -> None:
        """Аудит 10.10.2026: без `result_ttl` итог пачки жил восемь минут по умолчанию rq,
        и строка пачки отвечала «задачи нет» — причина остановки пропадала с экрана."""
        await _queue(session, 1)

        await _send(client, token)

        [(_, kwargs)] = jobs.enqueued
        assert kwargs["result_ttl"] == RESULT_TTL == 7 * 24 * 60 * 60
