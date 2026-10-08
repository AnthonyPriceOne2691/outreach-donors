"""Ревью стыков ответов и продаж (B1–B6): то, что прежние тесты не держали.

Каждый тест здесь закрывает пробел, найденный мутантом или чтением стыка: правило было
верным, но его порча проходила зелёной, — или правило неверное, и тогда тест помечен
`xfail(strict=True)` с причиной (правка общего кода — отдельным PR «общее»). Всё, что
уходит в базу, — на настоящей базе дерева; модель, очередь и почтовая платформа —
подставные, сети нет.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.core import stages
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.core.stages import MailPolicy
from backend.features.core.usage import LlmCapExceededError
from backend.features.outreach.health import SoftSignals
from backend.features.outreach.threads import review_of
from backend.features.sales.replies import SalesReplies
from backend.features.sales.reply_kind import Unanswered
from backend.shared import queue
from backend.workers import sales_jobs
from backend.workers.jobs import next_utc_day
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_delivery_events import _keypair, _sign
from tests.test_parse_requeue import UniqueQueue
from tests.test_sales_ooo_unsubscribe import _answer
from tests.test_sales_reply_kind import _job_body_on_test_base, _sales_reply
from tests.test_sales_reply_routing import (
    AUTH,
    NOW,
    URL,
    FakeClassifier,
    _form,
    _reply,
    queues,
    sales_letter,
    secret,
)
from tests.test_sales_soft_signals import _box, _journal
from tests.test_sales_stage_bridge import FakeSalesMail

__all__ = ["queues", "secret"]  # подставные очереди вебхука и подпись адреса ответа — 2.1


# --- B1: вебхук ----------------------------------------------------------------------------


async def test_webhook_repeat_of_an_answer_a_human_decided_queues_nothing(
    client: AsyncClient, session: AsyncSession, queues: dict[str, UniqueQueue]
) -> None:
    """Повтор вебхука ставит разбор вида заново, только если ответ никто не решал: решённый
    человеком ответ модели не уходит, даже когда снимка разбора у него нет.

    Мутант «повтор не смотрит на `reviewed_at`» прежние тесты проходил зелёным."""
    letter = await sales_letter(session)
    await client.post(URL, data=_form(letter, "Tell me more, please."), headers=AUTH)
    reply = await _reply(session)
    reply.reviewed_at = NOW
    await session.commit()
    queues[queue.SALES_QUEUE_NAME].jobs.clear()

    again = await client.post(URL, data=_form(letter, "Tell me more, please."), headers=AUTH)

    assert (again.status_code, again.json()["duplicate"]) == (200, True)
    assert queues[queue.SALES_QUEUE_NAME].jobs == []


def _signed(private: Any, payload: bytes) -> dict[str, str]:
    stamp = str(int(time.time()))
    return {
        "X-Twilio-Email-Event-Webhook-Signature": _sign(private, payload, stamp),
        "X-Twilio-Email-Event-Webhook-Timestamp": stamp,
        "Content-Type": "application/json",
    }


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код: вебхук событий не читает sg_event_id — повтор пачки платформой (доставка "
        "«хотя бы один раз») удваивает строки журнала здоровья ящика продаж и снижает его "
        "лимит раньше времени; правка — PR «общее» (api/events/routes.py, letters/events.py, "
        "outreach/health.py + миграция)"
    ),
)
async def test_events_batch_repeated_by_the_platform_counts_soft_signals_once(
    client: AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(soft=SoftSignals(), watch=True))
    stages.register_sales(lambda: found)
    box = await _box(session, Stage.SALES, total=1)
    letter = await session.scalar(select(MessageModel.id).where(MessageModel.sender_id == box.id))
    await session.commit()
    private, public = _keypair()
    monkeypatch.setattr("backend.config.outreach.EVENTS_PUBLIC_KEY", public)
    batch = [
        {"event": "deferred", "message_id": str(letter), "sg_event_id": f"seam-r2-{n}"}
        for n in (1, 2)
    ]
    payload = json.dumps(batch).encode()

    # Наш 200 до платформы не дошёл — она шлёт ту же пачку второй раз.
    for _ in range(2):
        response = await client.post(
            "/api/events/delivery", content=payload, headers=_signed(private, payload)
        )
        assert response.status_code == 200, response.text

    assert await _journal(session, box) == ["deferred", "deferred"], "те же события — дважды"


# --- B2: вид ответа моделью: потолок и отказ модели ---------------------------------------------


def test_llm_cap_in_the_job_entry_puts_the_answer_on_the_next_utc_day(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Потолок модели — «не сегодня», а не упавшая задача: точка входа очереди ловит его и
    ставит разбор на начало следующих суток UTC. Прежние тесты звали `postpone` напрямую, и
    мутант «точка входа потолок не ловит» проходил зелёным."""
    put: list[tuple[datetime, tuple[Any, ...], dict[str, Any]]] = []

    class Later:
        def enqueue_at(self, when: datetime, *args: Any, **options: Any) -> None:
            put.append((when, args, options))

    async def capped(_reply_id: int) -> dict[str, Any]:
        raise LlmCapExceededError("дневной потолок модели исчерпан")

    monkeypatch.setattr(sales_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(sales_jobs, "check_storage", lambda: None)
    monkeypatch.setattr(sales_jobs, "handle", capped)
    monkeypatch.setattr(sales_jobs, "sales_queue", Later)
    before = datetime.now(UTC)

    report = sales_jobs.sales_reply(4817)

    [(when, args, options)] = put
    assert args == (queue.SALES_REPLY_JOB, 4817)
    assert when in {next_utc_day(before), next_utc_day(datetime.now(UTC))}
    assert options["job_id"] == f"{queue.sales_job_id(4817)}-after-cap-{when:%Y%m%d}"
    assert options["retry"].max == len(queue.RETRY_INTERVALS)
    assert report == {
        "reply": 4817,
        "postponed_until": when.isoformat(),
        "reason": "дневной потолок модели исчерпан",
    }


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код: задача продаж (workers/sales_jobs.py) не говорит модулю, что попытка "
        "последняя, — после исчерпанных повторов очереди ответ навсегда ждёт со словами "
        "«задача попробует ещё раз»; правка — PR «общее» (sales_jobs.py и replies.py)"
    ),
)
async def test_last_retry_of_a_model_refusal_tells_the_human_to_sort_by_hand(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    reply = await _sales_reply(session, "Сколько стоит аудит?")
    await session.commit()
    _job_body_on_test_base(
        monkeypatch, session, FakeClassifier(Unanswered("модель не ответила: сеть", False))
    )
    last = type("Job", (), {"id": "sales-last-try", "retries_left": 0})()
    monkeypatch.setattr(sales_jobs, "get_current_job", lambda: last)
    monkeypatch.setattr(sales_jobs, "remember_job_error", lambda _id, _text: None)

    with pytest.raises(sales_jobs.ModelUnavailableError):
        await sales_jobs.handle(reply.id)

    await session.refresh(reply)
    reason = str(review_of(reply, Stage.SALES).reason)
    assert "попробует ещё раз" not in reason, "повторов больше не будет"
    assert "разберите вручную" in reason


# --- B6: автоответ -------------------------------------------------------------------------


async def test_out_of_office_job_that_runs_again_later_keeps_the_step_where_it_was(
    session: AsyncSession,
) -> None:
    """Срок по умолчанию — от прихода автоответа, а не от минуты задачи: задача, пришедшая
    позже (очередь, повтор), шаг дальше не двигает. Мутант «срок — от `now` задачи» прежние
    тесты проходил зелёным: в них задача шла в минуту ответа."""
    letter = await sales_letter(session)
    letter.next_action_at = NOW + timedelta(days=1)
    reply = await _answer(
        session,
        letter,
        "I am out of the office with limited access to email.",
        headers={"Auto-Submitted": "auto-replied"},
    )
    expected = NOW + timedelta(days=sales_cfg.OOO_DELAY_DAYS)

    for late in (timedelta(hours=5), timedelta(days=2)):
        await SalesReplies(session, FakeClassifier(), now=NOW + late).handle(reply.id)
        await session.flush()
        await session.refresh(letter)
        assert letter.next_action_at == expected, f"задача через {late} сдвинула срок"
