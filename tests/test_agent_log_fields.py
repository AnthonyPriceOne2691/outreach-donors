"""Журнал шва агента — по полям: ответ (`reply`), черновик (`draft`), правка судьи (`attempt`) и
причина (`why`) лежат полями записи, а не только в тексте. Журнал прода ищется по полю: «всё по
ответу №N» — запрос, а не поиск подстроки. Текст сообщений прежний.

Номера ответа у судьи нет (`GuardInput` без `reply_id`): у его записей — `attempt` и `why`.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from backend.features.agent import autopilot, drafting, guarding, writer
from backend.features.agent.drafting import DraftOutcome
from backend.features.agent.settings import defaults
from backend.features.agent.stages import AgentStage, DraftNotice, GuardInput, PriceSide
from backend.features.agent.writer import DraftUnavailableError, Written
from backend.features.core import stages as core_stages
from backend.features.core.domain import DraftStatus, Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.letters.sending import Sending
from backend.workers import agent_jobs
from redis.exceptions import RedisError
from rq.exceptions import DuplicateJobError
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_agent_autopilot import Recording, _drafted, _pilot, allowed
from tests.test_agent_drafting import reply
from tests.test_agent_guarding import _broken, _silent
from tests.test_replies_inbox import sent
from tests.test_sales_stage_bridge import FakeSalesMail, fake, unregistered

__all__ = ["allowed", "fake", "reply", "sent", "unregistered"]  # фикстуры — отсюда их видит pytest

FIELDS = ("reply", "draft", "attempt", "why")


def _fields(caplog: pytest.LogCaptureFixture, logger: str) -> list[dict[str, Any]]:
    """Поля шва у записей логгера — то, что печатает журнал из `extra`."""
    return [
        {key: vars(record)[key] for key in FIELDS if key in vars(record)}
        for record in caplog.records
        if record.name == logger
    ]


@pytest.mark.parametrize(
    ("guard", "why"),
    [(_silent, "судья не ответил за 0.01 с"), (_broken, "судья не смог проверить: RuntimeError")],
    ids=["timeout", "crash"],
)
async def test_judge_failure_carries_the_attempt_and_why(
    caplog: pytest.LogCaptureFixture, guard: Any, why: str
) -> None:
    settings = defaults(Stage.DONORS)
    stage = AgentStage(defaults=settings, price=PriceSide.BUY, guard=guard, guard_timeout_s=0.01)
    check = GuardInput(
        stage=Stage.DONORS, draft="Hello", incoming="Hi", facts=(), settings=settings, attempt=2
    )

    with caplog.at_level(logging.WARNING, logger=guarding.__name__):
        verdict = await guarding.judged(stage, check)

    assert verdict.reasons == (why,)
    assert _fields(caplog, guarding.__name__) == [{"attempt": 2, "why": why}]


def test_writer_says_why_the_model_answer_is_not_taken(caplog: pytest.LogCaptureFixture) -> None:
    invented = Written(body="Write to [address 9], please.", needs_human=False, reason=None)

    with caplog.at_level(logging.WARNING, logger=writer.__name__):
        assert writer.parse_form("не JSON") is None
        assert writer.checked(invented, {}, tokens=0).needs_human

    parsed, restored = _fields(caplog, writer.__name__)
    assert parsed["why"].startswith("Expecting value")
    assert restored["why"].startswith("Модель вернула метки, которых мы не выдавали: [address 9]")


class _Refused:
    def __init__(self, trouble: Exception) -> None:
        self.trouble = trouble

    def enqueue(self, *_args: object, **_kwargs: object) -> object:
        raise self.trouble


@pytest.mark.parametrize(
    ("trouble", "said"),
    [
        (DuplicateJobError("есть"), {"reply": 42}),
        (RedisError("нет связи"), {"reply": 42, "why": "нет связи"}),
    ],
    ids=["duplicate", "queue-down"],
)
def test_draft_job_not_queued_names_the_reply(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    trouble: Exception,
    said: dict[str, Any],
) -> None:
    monkeypatch.setattr(agent_jobs, "runs_queue", lambda: _Refused(trouble))

    with caplog.at_level(logging.INFO, logger=agent_jobs.__name__):
        agent_jobs.queue_draft(42)

    assert _fields(caplog, agent_jobs.__name__) == [said]


async def test_draft_not_queued_after_the_parse_names_the_reply(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(_session: AsyncSession, _reply_id: int) -> bool:
        raise RuntimeError("база не ответила")

    monkeypatch.setattr(drafting, "wants_draft", broken)

    with caplog.at_level(logging.WARNING, logger=agent_jobs.__name__):
        await agent_jobs.after_parse(session, 42)

    assert _fields(caplog, agent_jobs.__name__) == [{"reply": 42, "why": "база не ответила"}]


def test_unwritten_draft_names_the_reply_and_why(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def unavailable(_reply_id: int) -> dict[str, object]:
        raise DraftUnavailableError("ключа нет", permanent=True)

    monkeypatch.setattr(agent_jobs, "_draft_answer", unavailable)
    monkeypatch.setattr(agent_jobs, "check_storage", lambda: None)
    monkeypatch.setattr(agent_jobs, "setup_logging", lambda: None)  # журнал — в caplog

    with caplog.at_level(logging.WARNING, logger=agent_jobs.__name__):
        agent_jobs.draft_answer(7)

    assert _fields(caplog, agent_jobs.__name__) == [{"reply": 7, "why": "ключа нет"}]


async def test_lost_notice_names_the_draft_and_the_reply(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def broken(_notice: DraftNotice) -> None:
        raise RuntimeError("бот не ответил")

    stage = AgentStage(defaults=defaults(Stage.DONORS), price=PriceSide.BUY, on_draft=broken)
    monkeypatch.setattr(drafting, "AGENT_STAGES", {Stage.DONORS: stage})
    notice = DraftNotice(
        draft_id=5,
        reply_id=9,
        thread_id=3,
        stage=Stage.DONORS,
        status=DraftStatus.DRAFTED,
        reason=None,
    )

    with caplog.at_level(logging.WARNING, logger=drafting.__name__):
        await drafting.announce(DraftOutcome(reply_id=9, notice=notice))

    assert _fields(caplog, drafting.__name__) == [{"draft": 5, "reply": 9}]


@pytest.mark.usefixtures("allowed")
async def test_autopilot_refusal_names_the_draft_and_why(
    session: AsyncSession, reply: ReplyModel, caplog: pytest.LogCaptureFixture
) -> None:
    """Донор не принят человеком — путь отправки отказывает; запись — по черновику."""
    await _pilot(session)
    draft_id = await _drafted(session, reply, "Thanks! Which topics do you accept?")

    with caplog.at_level(logging.WARNING, logger=autopilot.__name__):
        flown = await autopilot.run(session, Sending(session, Recording()), draft_id)

    [said] = _fields(caplog, autopilot.__name__)
    assert said["draft"] == draft_id
    assert flown.held == f"отправка отказала: {said['why']}"


async def test_broken_sales_module_on_the_bridge_says_why(
    session: AsyncSession, fake: FakeSalesMail, caplog: pytest.LogCaptureFixture
) -> None:
    fake.broken = "connected"

    with caplog.at_level(logging.INFO, logger=core_stages.__name__):
        assert await core_stages.sales_connected(session) is False

    broken, without = _fields(caplog, core_stages.__name__)
    assert broken == {"why": "выдуманная поломка модуля: connected"}
    assert "выдуманная поломка модуля: connected" in without["why"]
