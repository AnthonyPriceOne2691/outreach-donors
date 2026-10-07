"""Когда пишется черновик: задачей очереди и по кнопке; черновики в переписке.

Из заготовки соседней сессии (cc68357), на шве этапов: переписка показывает
черновики со статусом и пишет ли агент на этапе; «написать заново» — под
правом send, отказ словами (409/404), «всё же написать» поверх пропуска
брифа; задача ставится одна на ответ и только там, где агент пишет; сбой
очереди — не сбой разбора; потолок и отказ ключа — итог задачи, а не повтор.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.features.agent import drafting
from backend.features.agent.stages import Brief, Skip, SkipKind
from backend.features.agent.writer import DraftUnavailableError
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.core.usage import LlmCapExceededError
from backend.workers import agent_jobs
from httpx import AsyncClient
from redis.exceptions import RedisError
from rq.exceptions import DuplicateJobError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_agent_drafting import FakeWriter, agent_on, briefed, donors_with, reply
from tests.test_replies_inbox import sent

__all__ = ["reply", "sent"]  # фикстуры — отсюда их видит pytest

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


class _Closing(FakeWriter):
    """Агент для маршрута: тот ещё и закрывает клиента после работы."""

    async def aclose(self) -> None:
        return None


class TestScreen:
    async def test_thread_shows_the_draft_and_that_the_agent_writes(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        reply: ReplyModel,
    ) -> None:
        await agent_on(session)
        await drafting.draft_answer(session, FakeWriter(), reply.id)
        await session.commit()
        await make_user("смотрит@site.com", role=UserRole.OPERATOR)
        token = await sign_in("смотрит@site.com")

        shown = await client.get(f"/api/threads/{reply.thread_id}", headers=bearer(token))

        assert shown.status_code == 200, shown.text
        view = shown.json()
        assert view["agent_writes"] is True
        [draft] = view["drafts"]
        assert (draft["reply_id"], draft["thread_id"]) == (reply.id, reply.thread_id)
        assert (draft["status"], draft["settings_version"]) == ("drafted", 1)

    async def test_redraft_needs_the_send_right(
        self, client: AsyncClient, make_user: MakeUser, sign_in: SignIn, reply: ReplyModel
    ) -> None:
        await make_user("смотрит@site.com", role=UserRole.OPERATOR)
        token = await sign_in("смотрит@site.com")

        refused = await client.post(
            f"/api/threads/{reply.thread_id}/replies/{reply.id}/draft", headers=bearer(token)
        )

        assert refused.status_code == 403

    async def test_redraft_writes_now_and_refusal_says_why(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        reply: ReplyModel,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("backend.api.threads.routes.AgentWriter", _Closing)
        await make_user("админ@site.com", role=UserRole.ADMIN)
        token = await sign_in("админ@site.com")
        path = f"/api/threads/{reply.thread_id}/replies/{reply.id}/draft"

        off = await client.post(path, headers=bearer(token))
        await agent_on(session)
        written = await client.post(path, headers=bearer(token))
        elsewhere = await client.post(
            f"/api/threads/999999/replies/{reply.id}/draft", headers=bearer(token)
        )

        assert off.status_code == 409
        assert "не настроен" in off.json()["detail"]
        assert written.status_code == 200, written.text
        assert written.json()["body"] == "Thanks! A guide on home repair works."
        assert elsewhere.status_code == 404

    async def test_force_writes_over_the_skip_of_the_brief(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        session: AsyncSession,
        reply: ReplyModel,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("backend.api.threads.routes.AgentWriter", _Closing)
        donors_with(monkeypatch, brief=briefed(Brief(skip=Skip(SkipKind.NO_REPLY, "спасибо"))))
        await agent_on(session)
        await make_user("админ@site.com", role=UserRole.ADMIN)
        token = await sign_in("админ@site.com")
        path = f"/api/threads/{reply.thread_id}/replies/{reply.id}/draft"

        skipped = await client.post(path, headers=bearer(token))
        forced = await client.post(f"{path}?force=true", headers=bearer(token))

        assert (skipped.json()["status"], skipped.json()["body"]) == ("skipped", "")
        assert (forced.json()["status"], forced.json()["body"]) == (
            "drafted",
            "Thanks! A guide on home repair works.",
        )


class TestQueue:
    async def test_draft_is_queued_only_where_the_agent_writes(
        self, session: AsyncSession, reply: ReplyModel
    ) -> None:
        """Без агента на этапе задача не ставится: очередь не копит пустых задач."""
        assert await drafting.wants_draft(session, reply.id) is False
        await agent_on(session)
        assert await drafting.wants_draft(session, reply.id) is True

    def test_one_job_per_reply_and_queue_trouble_is_not_the_parse_failing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        put: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

        class Queue:
            def __init__(self, error: Exception | None = None) -> None:
                self.error = error

            def enqueue(self, job: str, *args: object, **kwargs: object) -> None:
                if self.error is not None:
                    raise self.error
                put.append((job, args, kwargs))

        monkeypatch.setattr(agent_jobs, "runs_queue", Queue)
        agent_jobs.queue_draft(42)
        for trouble in (DuplicateJobError("есть"), RedisError("нет связи")):
            monkeypatch.setattr(agent_jobs, "runs_queue", lambda t=trouble: Queue(t))
            agent_jobs.queue_draft(42)  # не бросает: черновик попросят кнопкой

        [(job, args, kwargs)] = put
        assert job == agent_jobs.DRAFT_JOB == "backend.workers.agent_jobs.draft_answer"
        assert args == (42,)
        assert (kwargs["job_id"], kwargs["unique"]) == ("draft-reply-42", True)

    @pytest.mark.parametrize(
        ("trouble", "settled"),
        [
            (LlmCapExceededError("потолок"), True),
            (DraftUnavailableError("ключа нет", permanent=True), True),
            (DraftUnavailableError("сеть", permanent=False), False),
        ],
    )
    def test_cap_and_key_are_the_job_outcome_and_network_is_a_retry(
        self, monkeypatch: pytest.MonkeyPatch, trouble: Exception, settled: bool
    ) -> None:
        async def failing(_reply_id: int) -> dict[str, object]:
            raise trouble

        monkeypatch.setattr(agent_jobs, "_draft_answer", failing)
        monkeypatch.setattr(agent_jobs, "check_storage", lambda: None)

        if settled:
            outcome = agent_jobs.draft_answer(7)
            assert outcome == {"reply": 7, "error": str(trouble), "permanent": True}
        else:
            with pytest.raises(DraftUnavailableError):
                agent_jobs.draft_answer(7)
