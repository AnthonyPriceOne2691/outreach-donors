"""Черновик агента — задачей сразу после разбора ответа, и только где агент пишет.

Тело задачи разбора — на настоящей базе, платное подменено: после разбора
черновик ставится в очередь одной задачей, если агент на этапе настроен и
включён, и не ставится вовсе, если нет. Очередь у доноров прежняя — общая
(`runs`): свою (`sales`) берёт только разбор ответа лида продаж
(`tests/test_sales_reply_draft.py`). Экран задач знает задачу черновика
по имени, а не по пути функции.
"""

from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest
from backend.config import storage
from backend.features.ops import job_outcome
from backend.workers import agent_jobs, jobs
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_agent_drafting import agent_on
from tests.test_parse_requeue import (
    CountingExtractor,
    UniqueQueue,
    _accepted,
    _Closable,
    secret,
)

__all__ = ["secret"]  # фикстура ключа приёма — отсюда её видит pytest


class Drafts(UniqueQueue):
    """Общая очередь (`runs`) на месте настоящей: какие ответы получили задачу черновика."""

    @property
    def replies(self) -> list[int]:
        return [args[0] for job, args, _ in self.jobs if job == agent_jobs.DRAFT_JOB]


@pytest.fixture
def queued(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> Drafts:
    """Задача разбора — на базе теста; черновик встаёт в подставную общую очередь. Redis из
    настроек — закрытый порт: задача мимо подставной очереди — отказ связи, а не задача в
    общем Redis машины чужому воркеру."""
    runs = Drafts()
    monkeypatch.setattr(storage, "REDIS_URL", "redis://127.0.0.1:1/0")
    monkeypatch.setattr(jobs, "ExtractClient", CountingExtractor)
    monkeypatch.setattr(jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    monkeypatch.setattr(agent_jobs, "runs_queue", lambda: runs)
    return runs


@pytest.mark.usefixtures("secret")
@pytest.mark.parametrize("writes", [True, False])
async def test_draft_is_queued_after_the_parse_only_where_the_agent_writes(
    session: AsyncSession, queued: Drafts, writes: bool
) -> None:
    """Разбор цены доноров ставит черновик в общую очередь, как до черновиков продаж."""
    reply = await _accepted(session)
    await session.commit()
    if writes:
        await agent_on(session)

    await jobs._parse_reply(reply.id)

    assert queued.replies == ([reply.id] if writes else [])


@pytest.mark.usefixtures("secret")
async def test_a_failure_to_queue_the_draft_does_not_fail_the_parse(
    session: AsyncSession,
    queued: Drafts,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Разбор уже закоммичен: сбой постановки черновика задачу разбора не роняет — итог
    разбора на месте, черновик не поставлен, причина в журнале."""
    reply = await _accepted(session)
    await session.commit()
    await agent_on(session)

    async def broken(*_: object) -> bool:
        raise RuntimeError("выдуманный сбой выбора черновика")

    monkeypatch.setattr(agent_jobs.drafting, "wants_draft", broken)
    with caplog.at_level(logging.WARNING, logger=agent_jobs.__name__):
        parsed = await jobs._parse_reply(reply.id)

    assert (parsed["stored_price"], parsed["skipped"]) == (True, None)
    assert queued.replies == []
    assert f"ответ №{reply.id} разобран, а черновик не поставлен" in caplog.text


def test_the_screen_names_the_draft_job() -> None:
    job = SimpleNamespace(func_name=agent_jobs.DRAFT_JOB)

    assert job_outcome.KINDS[job.func_name] == "черновик ответа"
