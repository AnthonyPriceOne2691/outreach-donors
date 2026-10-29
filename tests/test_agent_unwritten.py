"""Черновик, который модель не написала, — человеку словами, а не тишина (ревью стыков с продажами).

Задача черновика (`workers/agent_jobs._draft_answer`) кончалась на отказе модели итогом и строкой
журнала: черновика нет, крючок этапа не зовётся — группа продаж не узнаёт, а ответ говорит
«ответит агент». Теперь, когда повтора не будет — ключ или права модели, выбранный потолок,
временный отказ на последней попытке очереди, — ответ получает черновик `escalated` без текста
с причиной «черновик не написан: …», фиксацию и крючок этапа. Временный отказ с повторами
впереди — по-прежнему повтор очереди, черновика нет. Черновик, написанный кнопкой, пока модель
отказывала, отметка не затирает.

База настоящая; модель — подставной писатель, крючок этапа — запись того, что он узнал.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from backend.config import llm as llm_cfg
from backend.features.agent import drafting
from backend.features.agent.stages import DraftNotice
from backend.features.agent.writer import DraftUnavailableError, Request, Written
from backend.features.core import usage
from backend.features.core.domain import DraftStatus
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import ReplyModel
from backend.features.core.usage import LlmCapExceededError
from backend.workers import agent_jobs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_agent_drafting import FakeWriter, agent_on, donors_with, reply, stored
from tests.test_parse_requeue import _Closable
from tests.test_replies_inbox import sent

__all__ = ["reply", "sent"]  # фикстуры — отсюда их видит pytest

#: Постоянный отказ модели — словами `shared/llm.Refusal`.
KEY_REFUSED = DraftUnavailableError(
    "модель, отказ (чинить): ключ модели отклонён (HTTP 401)", permanent=True
)
#: Временный отказ — его повторяет очередь.
TIMEOUT = DraftUnavailableError("модель, сеть (можно повторить): таймаут", permanent=False)


class Refusing:
    """Писатель задачи на месте модели: отказывает, как модель без ключа или без сети."""

    def __init__(self, trouble: Exception) -> None:
        self.trouble = trouble
        self.seen: list[Request] = []

    async def write(self, request: Request) -> Written:
        self.seen.append(request)
        raise self.trouble

    async def aclose(self) -> None:
        return None


@pytest.fixture
def told(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> list[DraftNotice]:
    """Задача черновика — на базе теста; что узнал крючок этапа доноров (`on_draft`)."""
    monkeypatch.setattr(agent_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        agent_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    notices: list[DraftNotice] = []

    async def hook(notice: DraftNotice) -> None:
        notices.append(notice)

    donors_with(monkeypatch, on_draft=hook)
    return notices


def _writer(monkeypatch: pytest.MonkeyPatch, trouble: Exception) -> Refusing:
    writer = Refusing(trouble)
    monkeypatch.setattr(agent_jobs, "AgentWriter", lambda: writer)
    return writer


def _job(monkeypatch: pytest.MonkeyPatch, *, retries_left: int) -> None:
    """Задача — из очереди, и у неё впереди `retries_left` повторов."""
    monkeypatch.setattr(
        agent_jobs, "get_current_job", lambda: SimpleNamespace(retries_left=retries_left)
    )


async def _draft(session: AsyncSession, reply_id: int) -> AgentDraftModel | None:
    return await session.scalar(select(AgentDraftModel).where(AgentDraftModel.reply_id == reply_id))


def _said(notices: list[DraftNotice]) -> list[tuple[int, DraftStatus, str | None]]:
    return [(notice.draft_id, notice.status, notice.reason) for notice in notices]


async def test_a_refused_key_leaves_a_draft_for_the_human_in_words(
    session: AsyncSession,
    reply: ReplyModel,
    told: list[DraftNotice],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Повтор ключа не починит — черновик человеку без текста с причиной, и крючок этапа
    узнаёт о нём после фиксации; итог задачи — прежний отказ модели."""
    await agent_on(session)
    _writer(monkeypatch, KEY_REFUSED)

    with pytest.raises(DraftUnavailableError):
        await agent_jobs._draft_answer(reply.id)

    draft = await stored(session, reply.id)
    why = f"черновик не написан: {KEY_REFUSED}"
    assert (draft.status, draft.body, draft.reason) == (DraftStatus.ESCALATED, "", why)
    assert _said(told) == [(draft.id, DraftStatus.ESCALATED, why)]


async def test_a_spent_cap_leaves_a_draft_for_the_human_without_calling_the_model(
    session: AsyncSession,
    reply: ReplyModel,
    told: list[DraftNotice],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(llm_cfg, "AGENT_DAILY_TOKEN_CAP", 83)
    await agent_on(session)
    usage.record(session, operation="agent_draft", units=83)
    await session.commit()
    writer = _writer(monkeypatch, KEY_REFUSED)

    with pytest.raises(LlmCapExceededError):
        await agent_jobs._draft_answer(reply.id)

    draft = await stored(session, reply.id)
    assert writer.seen == []
    assert draft.reason is not None
    assert draft.reason.startswith(
        "черновик не написан: дневной потолок черновиков агента выбран: 83 из 83 токенов — "
        "попросите администратора поднять потолок на экране «Расход» или дождитесь сброса"
    )
    assert _said(told) == [(draft.id, DraftStatus.ESCALATED, draft.reason)]


@pytest.mark.parametrize(("retries_left", "last"), [(2, False), (0, True)], ids=["retry", "last"])
async def test_a_temporary_refusal_waits_for_the_retry_and_on_the_last_one_reaches_the_human(
    session: AsyncSession,
    reply: ReplyModel,
    told: list[DraftNotice],
    monkeypatch: pytest.MonkeyPatch,
    retries_left: int,
    last: bool,
) -> None:
    """Сеть — повтор очереди: черновика нет, крючок молчит. Повторов не осталось — черновик
    человеку, и слова говорят, что повторов больше не будет."""
    await agent_on(session)
    _writer(monkeypatch, TIMEOUT)
    _job(monkeypatch, retries_left=retries_left)

    with pytest.raises(DraftUnavailableError):
        await agent_jobs._draft_answer(reply.id)

    draft = await _draft(session, reply.id)
    if not last:
        assert (draft, told) == (None, [])
        return
    assert draft is not None
    why = f"черновик не написан: {TIMEOUT} — повторы задачи кончились"
    assert (draft.status, draft.body, draft.reason) == (DraftStatus.ESCALATED, "", why)
    assert _said(told) == [(draft.id, DraftStatus.ESCALATED, why)]


async def test_the_mark_does_not_overwrite_a_draft_written_meanwhile(
    session: AsyncSession, reply: ReplyModel
) -> None:
    """Кнопка «написать заново» успела, пока задача ждала модель: её черновик цел."""
    await agent_on(session)
    await drafting.draft_answer(session, FakeWriter(), reply.id)

    outcome = await drafting.unwritten(session, reply.id, str(KEY_REFUSED))

    draft = await stored(session, reply.id)
    assert (outcome.notice, outcome.skipped) == (None, "черновик уже написан")
    assert (draft.status, draft.body) == (DraftStatus.DRAFTED, FakeWriter().written.body)
