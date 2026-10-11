"""Агент сам понял «хочет говорить» — лид передаётся телемаркетологу, черновика нет.

Вид ответа назвал «вопрос» или «интерес», ответ ушёл агенту, а ситуация письма —
`wants_to_talk` (ход «лид»). Раньше это была пометка в `meta` черновика, а черновик звал в
Telegram; теперь бриф пропускает письмо словами «агент понял: хочет говорить», крючок пропуска
этапа ставит задачу, задача передаёт лида тем же путём, что вид ответа (`handoff.start`), и
снимает ответ с ожидания. Обратный прогон: ситуация «вопрос» — черновик, как был.

Шов агента — настоящий (`drafting.draft_answer`, `announce`), модель ситуации и судьи —
подставной HTTP, писатель — подставной, очередь — список. База — настоящая; адреса выдуманные.
"""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

import pytest
from backend.features.agent import drafting
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.agent.stages import DraftNotice, SkipKind
from backend.features.core.domain import DraftStatus, Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.outreach.threads import review_of
from backend.features.sales import handoff
from backend.features.sales.agent import brief, handover, moves, parts
from backend.features.sales.models import LeadStatus, SalesHandoffModel
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_agent_brief import OFFER, world
from tests.test_sales_agent_situation import Plug, llm, talk
from tests.test_sales_agent_stage import GOOD, Writer, sales_on
from tests.test_sales_handoff import NOW
from tests.test_sales_handoff_rows import Dialog, sales_dialog
from tests.test_sales_switch import sales_switched_on

__all__ = ["llm", "sales_on", "sales_switched_on"]  # фикстуры — отсюда их видит pytest

ALLOW = {"claims": [], "promises": [], "tone": {"ok": True, "problem": ""}}
TALK = {"situation": "wants_to_talk", "confidence": 0.91}
INFORM = {"situation": "asks_info", "confidence": 0.9}


class Queue:
    """Очередь продаж на месте Redis: помнит пути задач и доводы."""

    def __init__(self, *, down: bool = False) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []
        self.down = down

    def enqueue(self, path: str, *args: Any, **options: Any) -> None:
        if self.down:
            raise RedisConnectionError("очередь не отвечает")
        self.jobs.append((path, args, options))


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> Queue:
    placed = Queue()
    monkeypatch.setattr(handover, "sales_queue", lambda: placed)
    return placed


async def asked(session: AsyncSession) -> Dialog:
    """Лид задал вопрос; разбор вида уже записан: путь агента, ответ ждёт человека."""
    await world(session)
    dialog = await sales_dialog(session, text="Интересно. Давайте созвонимся во вторник.")
    await AgentSettingsRepository(session).save(Stage.SALES, parts.DEFAULTS, author="тест")
    dialog.reply.model_parse = {
        "stage": Stage.SALES.value,
        "kind": "question",
        "route": "agent",
        "waits": True,
        "reason": "задал вопрос: ответит агент; пока — человек",
    }
    await session.flush()
    return dialog


async def reread(session: AsyncSession, reply_id: int) -> ReplyModel:
    reply = await session.get(ReplyModel, reply_id, populate_existing=True)
    assert reply is not None
    return reply


# --- бриф: «хочет говорить» — пропуск со словами, а не письмо со ссылкой -------------------


@pytest.mark.parametrize("links", [True, False], ids=["with-links", "without-links"])
async def test_wants_to_talk_is_a_handover_skip_not_a_letter(
    session: AsyncSession, llm: Plug, links: bool
) -> None:
    await world(session, links=links)
    model = llm(situation=[TALK])

    found = await brief.brief(session, talk((True, OFFER), (False, "Давайте созвонимся.")))

    assert found.skip is not None
    assert (found.skip.kind, found.skip.reason) == (SkipKind.NO_REPLY, moves.LEAD_SKIP)
    assert found.skip.reason.startswith("агент понял: хочет говорить")
    assert (found.meta["move"], found.meta["lead"], found.facts) == ("talk", True, ())
    assert len(model.sent["situation"]) == 1


async def test_question_situation_is_still_a_letter(session: AsyncSession, llm: Plug) -> None:
    """Обратный прогон: ситуация «вопрос» — черновик, как был."""
    await world(session)
    llm(situation=[INFORM])

    found = await brief.brief(session, talk((True, OFFER), (False, "Расскажите подробнее.")))

    assert found.skip is None
    assert found.facts, "факты под ход — писателю"
    assert found.meta["lead"] is False


# --- шов: пропуск записан, крючок ставит передачу — не сообщение о черновике ---------------


@pytest.mark.usefixtures("sales_on")
async def test_skip_is_stored_and_the_hook_queues_the_handover(
    session: AsyncSession, llm: Plug, queue: Queue
) -> None:
    dialog = await asked(session)
    llm(situation=[TALK], judge=[ALLOW])
    writer = Writer(GOOD)

    outcome = await drafting.draft_answer(session, writer, dialog.reply.id)
    await session.commit()
    await drafting.announce(outcome)

    assert outcome.status is DraftStatus.SKIPPED, "черновика нет"
    assert writer.seen == [], "писатель не звался"
    assert outcome.notice is not None
    assert outcome.notice.reason == moves.LEAD_SKIP
    [(path, args, options)] = queue.jobs
    assert (path, args) == (handover.HANDOVER_JOB, (dialog.reply.id,))
    assert options["retry"] is not None, "сбой базы — повтор очереди"


@pytest.mark.usefixtures("sales_on")
async def test_question_situation_gets_the_draft_and_no_handover(
    session: AsyncSession, llm: Plug, queue: Queue
) -> None:
    dialog = await asked(session)
    llm(situation=[INFORM], judge=[ALLOW])
    writer = Writer(GOOD)

    outcome = await drafting.draft_answer(session, writer, dialog.reply.id)
    await session.commit()
    await drafting.announce(outcome)

    assert writer.seen, "писатель писал черновик"
    assert outcome.status is not DraftStatus.SKIPPED
    assert queue.jobs == [], "передачи нет"


@pytest.mark.parametrize("reason", ["ответ не нужен: собеседник благодарит", None])
def test_other_skips_queue_nothing(queue: Queue, reason: str | None) -> None:
    notice = DraftNotice(
        draft_id=3,
        reply_id=5,
        thread_id=7,
        stage=Stage.SALES,
        status=DraftStatus.SKIPPED,
        reason=reason,
    )

    handover.queue_hand_over(notice)

    assert queue.jobs == []


def test_queue_down_is_a_log_line_not_a_failure(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(handover, "sales_queue", lambda: Queue(down=True))
    notice = DraftNotice(
        draft_id=3,
        reply_id=5,
        thread_id=7,
        stage=Stage.SALES,
        status=DraftStatus.SKIPPED,
        reason=moves.LEAD_SKIP,
    )

    with caplog.at_level(logging.WARNING, logger=handover.__name__):
        handover.queue_hand_over(notice)

    assert "агент понял: хочет говорить, а передача не поставлена" in caplog.text


# --- задача: передача тем же путём, ответ не ждёт человека ---------------------------------


async def test_handover_job_hands_the_lead_over_and_settles_the_answer(
    session: AsyncSession, caplog: pytest.LogCaptureFixture
) -> None:
    dialog = await asked(session)
    queued: list[int] = []
    start = partial(handoff.start, enqueue=queued.append, now=lambda: NOW)

    with caplog.at_level(logging.INFO, logger=handover.__name__):
        report = await handover.hand_over(session, dialog.reply.id, start=start)

    row = await session.scalar(
        select(SalesHandoffModel).where(SalesHandoffModel.thread_id == dialog.thread.id)
    )
    assert row is not None, "передача заведена — той же строкой, что по виду ответа"
    assert queued == [row.id], "и её задача Kommo и Telegram поставлена"
    reason = "агент понял: хочет говорить — передан телемаркетологу"
    assert report == {"reply": dialog.reply.id, "waits": False, "reason": reason}
    reply = await reread(session, dialog.reply.id)
    snap = reply.model_parse or {}
    assert (snap["kind"], snap["route"], snap["waits"], snap["reason"]) == (
        "question",
        "agent",
        False,
        reason,
    )
    assert review_of(reply, Stage.SALES).waiting is False
    assert "агент понял: хочет говорить — лид передан телемаркетологу" in caplog.text


async def test_handover_refused_leaves_the_answer_waiting_with_the_reason(
    session: AsyncSession,
) -> None:
    dialog = await asked(session)
    dialog.lead.status = LeadStatus.REJECTED  # лида у диалога нет — передавать некого
    await session.flush()

    report = await handover.hand_over(
        session, dialog.reply.id, start=partial(handoff.start, enqueue=lambda _id: None)
    )

    assert report["waits"] is True
    reason = str(report["reason"])
    assert reason.startswith("агент понял: хочет говорить — передать лида не вышло — ")
    assert reason.endswith("; решает человек")
    snap = (await reread(session, dialog.reply.id)).model_parse or {}
    assert snap["waits"] is True
    assert "нет лида продаж" in snap["handoff_error"]


async def test_retry_after_the_fix_settles_the_answer_and_forgets_the_old_refusal(
    session: AsyncSession,
) -> None:
    """Лида-дубль разобрали, черновик попросили заново — передача прошла: прежний отказ из
    снимка уходит, ответ человека не ждёт."""
    dialog = await asked(session)
    dialog.lead.status = LeadStatus.REJECTED
    await session.flush()
    start = partial(handoff.start, enqueue=lambda _id: None)
    await handover.hand_over(session, dialog.reply.id, start=start)
    dialog.lead.status = LeadStatus.READY
    await session.flush()

    report = await handover.hand_over(session, dialog.reply.id, start=start)

    assert report["waits"] is False
    snap = (await reread(session, dialog.reply.id)).model_parse or {}
    assert "handoff_error" not in snap
    assert snap["reason"] == "агент понял: хочет говорить — передан телемаркетологу"


async def test_handover_job_of_a_deleted_answer_says_so(session: AsyncSession) -> None:
    assert await handover.hand_over(session, 987654) == {
        "reply": 987654,
        "skipped": "ответа нет: удалён до передачи",
    }
