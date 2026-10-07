"""Черновик агента — задачей сразу после разбора ответа, и только где агент пишет.

Тело задачи разбора — на настоящей базе, платное подменено: после разбора
черновик ставится в очередь одной задачей, если агент на этапе настроен и
включён, и не ставится вовсе, если нет. Экран задач знает задачу черновика
по имени, а не по пути функции.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from backend.features.ops import job_outcome
from backend.workers import agent_jobs, jobs
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_agent_drafting import agent_on
from tests.test_parse_requeue import CountingExtractor, _accepted, _Closable, secret

__all__ = ["secret"]  # фикстура ключа приёма — отсюда её видит pytest


@pytest.fixture
def queued(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Задача разбора — на базе теста; постановка черновика записывается."""
    put: list[int] = []
    monkeypatch.setattr(jobs, "ExtractClient", CountingExtractor)
    monkeypatch.setattr(jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    monkeypatch.setattr(agent_jobs, "queue_draft", put.append)
    return put


@pytest.mark.usefixtures("secret")
@pytest.mark.parametrize("writes", [True, False])
async def test_draft_is_queued_after_the_parse_only_where_the_agent_writes(
    session: AsyncSession, queued: list[int], writes: bool
) -> None:
    reply = await _accepted(session)
    await session.commit()
    if writes:
        await agent_on(session)

    await jobs._parse_reply(reply.id)

    assert queued == ([reply.id] if writes else [])


def test_the_screen_names_the_draft_job() -> None:
    job = SimpleNamespace(func_name=agent_jobs.DRAFT_JOB)

    assert job_outcome.KINDS[job.func_name] == "черновик ответа"
