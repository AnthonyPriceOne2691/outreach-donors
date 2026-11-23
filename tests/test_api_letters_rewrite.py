"""«Переписать шаблонные · N» — маршрут, задача и консоль (`api/letters/rewrite_routes.py`).

Проверяется то, что видит человек у кнопки: число шаблонных писем в ответе экрана, правка руками
выводит письмо из этого числа, нажатие ставит задачу с доводами, которые она принимает, и пишет
строку журнала с тем, кто нажал; пустая очередь и второе нажатие — отказ словами, а очередь,
которая уходит сама, нажатию не мешает: её проход шаблонные не шлёт. Задача и консоль
переписывают тем же ядром (`letters/rewrite_queue.py`).
"""

from __future__ import annotations

import argparse
import inspect
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.api.letters import once
from backend.cli import letters_queue
from backend.config import storage
from backend.features.core.domain import AuditAction, Stage, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.letters import autosend, rewrite_queue
from backend.features.letters.rewrite import NO_KEY
from backend.features.letters.rewrite_queue import RewriteReport, RewriteRequest
from backend.features.ops import job_outcome
from backend.shared.queue import REWRITE_JOB
from backend.workers import jobs
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.committed_db import committed_sessions
from tests.conftest import TEST_DSN, bearer
from tests.test_letters_one_job import _Jobs
from tests.test_letters_rewrite_queue import HOSTS, Model, _build, _donors, _letters

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
def jobs_queue(monkeypatch: pytest.MonkeyPatch) -> _Jobs:
    found = _Jobs()
    monkeypatch.setattr("backend.api.letters.rewrite_routes.runs_queue", lambda: found)
    return found


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


async def _templated(session: AsyncSession, count: int = 2) -> None:
    """Очередь доноров, собранная, пока у модели не было денег: письма шаблоном."""
    await _donors(session, HOSTS[:count])
    await _build(session, Model(refuse=HOSTS))


async def _press(client: AsyncClient, token: str, **body: Any) -> Response:
    return await client.post(
        "/api/letters/rewrite", json={"stage": "donors", **body}, headers=bearer(token)
    )


@pytest.mark.usefixtures("filled_legal")
class TestTheButton:
    async def test_press_queues_the_job_and_writes_the_journal(
        self, client: AsyncClient, session: AsyncSession, token: str, jobs_queue: _Jobs
    ) -> None:
        await _templated(session)

        shown = await client.get("/api/letters", headers=bearer(token))
        pressed = await _press(client, token)

        assert shown.json()["templated"] == 2
        assert pressed.status_code == 200, pressed.text
        assert pressed.json() == {"job_id": "letters-rewrite-donors-links", "letters": 2}
        [(args, options)] = jobs_queue.enqueued
        assert args[0] == REWRITE_JOB
        bound = inspect.signature(jobs.rewrite_letter_queue).bind(*args[1:])
        assert bound.arguments == {"stage": "donors", "audience": "links"}
        assert options["job_id"] == "letters-rewrite-donors-links"
        row = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
        )
        assert row is not None
        assert row.target == "job:letters-rewrite-donors-links"
        assert row.details == {
            "действие": "переписывание шаблонных писем",
            "этап": "доноры",
            "писем": "2",
        }

    async def test_letter_edited_by_hand_leaves_the_count(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        """Правка руками — даже слово в слово — решение человека: переписывать его нельзя."""
        await _templated(session)
        letter = (await _letters(session))[HOSTS[0]]

        edited = await client.patch(
            f"/api/letters/{letter.id}",
            json={"subject": letter.subject, "body": letter.body},
            headers=bearer(token),
        )
        shown = await client.get("/api/letters", headers=bearer(token))

        assert edited.status_code == 200, edited.text
        assert edited.json()["uniqueness"] == 0
        assert shown.json()["templated"] == 1

    async def test_nothing_to_rewrite_is_refused_in_words(
        self, client: AsyncClient, token: str, jobs_queue: _Jobs
    ) -> None:
        pressed = await _press(client, token)

        assert (pressed.status_code, pressed.json()["detail"]) == (409, rewrite_queue.NOTHING)
        assert jobs_queue.enqueued == []

    async def test_queue_sending_itself_does_not_hold_the_press(
        self, client: AsyncClient, session: AsyncSession, token: str, jobs_queue: _Jobs
    ) -> None:
        """Проход автоотправки шаблонное письмо не шлёт (`batch._sendable`): переписывать можно,
        не останавливая очередь, — тревога о шаблонных это и советует."""
        await _templated(session)
        await autosend.start(session, Stage.DONORS, "links", by=None, now=datetime.now(UTC))
        await session.commit()

        pressed = await _press(client, token)

        assert pressed.status_code == 200, pressed.text
        assert len(jobs_queue.enqueued) == 1

    async def test_second_press_while_the_first_runs_is_refused(
        self, client: AsyncClient, session: AsyncSession, token: str, jobs_queue: _Jobs
    ) -> None:
        await _templated(session)

        first = await _press(client, token)
        second = await _press(client, token)

        assert first.status_code == 200, first.text
        assert (second.status_code, second.json()["detail"]) == (409, once.REWRITE_RUNNING)
        assert len(jobs_queue.enqueued) == 1

    async def test_sales_letters_are_refused_in_words(
        self, client: AsyncClient, token: str, jobs_queue: _Jobs
    ) -> None:
        pressed = await _press(client, token, stage="sales")

        assert pressed.status_code == 409
        assert "модуль продаж" in pressed.json()["detail"]
        assert jobs_queue.enqueued == []


def test_job_outcome_names_the_job_and_closes_it_by_stage() -> None:
    assert job_outcome.KINDS[REWRITE_JOB] == "переписывание шаблонных писем"
    assert job_outcome.advertisers_job("letters-rewrite-advertisers-niche", REWRITE_JOB, None, ())
    assert not job_outcome.advertisers_job("letters-rewrite-donors-links", REWRITE_JOB, None, ())


class _Closable:
    async def aclose(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


class _ClosableModel(Model):
    async def aclose(self) -> None:
        return None


@pytest.mark.usefixtures("filled_legal")
class TestTheJob:
    async def test_job_rewrites_with_the_same_core(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _templated(session)
        monkeypatch.setattr(jobs, "RewriteClient", _ClosableModel)
        monkeypatch.setattr(jobs, "create_async_engine", lambda dsn: _Closable())
        monkeypatch.setattr(
            jobs,
            "async_sessionmaker",
            lambda engine, **kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
        )

        report = await jobs._rewrite_letters(RewriteRequest(stage=Stage.DONORS))

        assert (report["found"], report["rewritten"], report["remaining"]) == (2, 2, 0)
        assert report["left"] == {}

    async def test_without_a_model_key_letters_stay_and_the_reason_is_said(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Настоящий клиент без ключа модель не зовёт: письма как были, причина — словами."""
        await _templated(session, 1)
        monkeypatch.setattr(jobs, "create_async_engine", lambda dsn: _Closable())
        monkeypatch.setattr(
            jobs,
            "async_sessionmaker",
            lambda engine, **kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
        )

        report = await jobs._rewrite_letters(RewriteRequest(stage=Stage.DONORS))

        assert (report["rewritten"], report["left"], report["remaining"]) == (0, {NO_KEY: 1}, 1)

    def test_stage_and_audience_arrive_as_strings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[RewriteRequest] = []

        async def rewrite(request: RewriteRequest) -> dict[str, Any]:
            seen.append(request)
            return {}

        monkeypatch.setattr(jobs, "_rewrite_letters", rewrite)
        monkeypatch.setattr(jobs, "check_storage", lambda: None)

        jobs.rewrite_letter_queue("donors")
        jobs.rewrite_letter_queue("advertisers", "niche")

        assert [(r.stage, r.audience) for r in seen] == [
            (Stage.DONORS, "links"),
            (Stage.ADVERTISERS, "niche"),
        ]


class TestTheConsole:
    def test_command_and_its_arguments(self) -> None:
        parser = argparse.ArgumentParser()
        letters_queue.add_parser(parser.add_subparsers(dest="command"))

        args = parser.parse_args(["letters-rewrite", "--stage", "advertisers"])

        assert (args.stage, args.audience, args.country) == ("advertisers", "links", "us")
        assert letters_queue.COMMANDS["letters-rewrite"] is letters_queue.cmd_letters_rewrite

    def test_report_is_said_in_words(self, capsys: pytest.CaptureFixture[str]) -> None:
        letters_queue._print_rewrite(
            RewriteReport(
                found=68,
                rewritten=60,
                tokens_spent=7200,
                off_corridor=2,
                left={rewrite_queue.DIVERGED: 8},
                stopped="у модели нет кредитов — пополните счёт OpenAI",
                remaining=8,
            )
        )

        said = capsys.readouterr().out
        assert "Шаблонных писем взято 68, переписано 60, токенов 7200." in said
        assert "Вне коридора отличия: 2" in said
        assert f"Не переписано, {rewrite_queue.DIVERGED}: 8" in said
        assert "Остановлено: у модели нет кредитов" in said
        assert "Шаблонных в очереди осталось: 8." in said


@pytest.mark.usefixtures("filled_legal")
class TestTheConsoleOnItsOwnBase:
    """Команда открывает свою базу (`letters_queue._sessions`) — настоящие фиксации и чистка."""

    @pytest.fixture(autouse=True)
    def _test_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(storage, "DSN", TEST_DSN)

    async def test_letters_stay_without_a_key_and_the_reason_is_said(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = argparse.Namespace(stage="donors", audience="links", country="us")
        async with committed_sessions() as factory:
            async with factory() as session:
                await _templated(session, 1)

            code = await letters_queue.cmd_letters_rewrite(args)

        said = capsys.readouterr().out
        assert code == letters_queue.EXIT_OK
        assert "Шаблонных писем взято 1, переписано 0, токенов 0." in said
        assert f"Не переписано, {NO_KEY}: 1" in said

    async def test_queue_sending_itself_does_not_hold_the_command(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        args = argparse.Namespace(stage="donors", audience="links", country="us")
        async with committed_sessions() as factory:
            async with factory() as session:
                await _templated(session, 1)
                await autosend.start(session, Stage.DONORS, "links", by=None, now=datetime.now(UTC))
                await session.commit()

            code = await letters_queue.cmd_letters_rewrite(args)

        assert code == letters_queue.EXIT_OK
        assert "Шаблонных писем взято 1" in capsys.readouterr().out
