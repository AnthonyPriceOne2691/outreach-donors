"""Метрика в тексте добивки кончает её цепочку, а не проход добивок (ревью стыков 12.10.2026).

Подпись «Отправителя» продаж — настройка, и метрика Ahrefs в ней («our DR 45 team») делала
текст добивки запрещённым (`guards.assert_no_metrics`). Исключение уходило из прохода
целиком: погашенная захватом добивка пропадала молча, а остальные подошедшие ждали следующего
прохода — и падали на ней же. Теперь цепочка этого письма кончается с причиной словами,
журнал говорит громко, а проход идёт к следующей добивке. Модуль продаж здесь подставной.
"""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from backend.features.core import stages
from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import followups
from backend.features.letters.building import followup_key
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_stage_bridge import FakeSalesMail, _transports
from tests.test_sales_stage_mail import NOW, _donor_chain, sales_world

#: Текст добивки продаж с метрикой — так её собрал бы модуль с подписью «Отправителя».
WITH_METRIC = "Just following up.\n\nBest regards, the DR 45 team"
SAID = "добивка шага 1 не ушла: В письме метрики Ahrefs (значение DR: «DR 45»)"


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Подставной модуль продаж подключён к мосту и собирает добивку с метрикой."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(body=WITH_METRIC)
    stages.register_sales(lambda: found)
    return found


async def _steps(session: AsyncSession, thread_id: int | None) -> list[tuple[int, MessageStatus]]:
    rows = await session.execute(
        select(MessageModel.step, MessageModel.status)
        .where(MessageModel.thread_id == thread_id)
        .order_by(MessageModel.step)
    )
    return list(rows.tuples().all())


async def test_the_chain_stops_with_words_and_the_pass_goes_on(
    session: AsyncSession,
    filled_legal: None,
    fake: FakeSalesMail,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Добивка продаж подошла раньше донорской: её цепочка кончается с причиной у письма,
    после которого пришёл срок, а добивка донору уходит тем же проходом."""
    world = await sales_world(session, status=MessageStatus.SENT, due=NOW - timedelta(days=2))
    donor = await _donor_chain(session, due=NOW - timedelta(days=1))

    with caplog.at_level(logging.WARNING, logger=followups.__name__):
        report = await followups.send_due(session, transport=_transports(), limit=5, now=NOW)

    assert (report.sent, report.stopped, report.postponed) == (1, 1, 0)
    await session.refresh(world.letter)
    assert world.letter.next_action_at is None
    assert (world.letter.failure_reason or "").startswith(SAID)
    assert await _steps(session, world.thread.id) == [(0, MessageStatus.SENT)]
    assert await _steps(session, donor.thread_id) == [
        (0, MessageStatus.SENT),
        (1, MessageStatus.SENT),
    ]
    assert "цепочка остановлена: В письме метрики Ahrefs" in caplog.text


async def test_a_step_left_queued_by_a_past_attempt_is_stopped_with_the_same_words(
    session: AsyncSession, filled_legal: None, fake: FakeSalesMail
) -> None:
    """Прошлая попытка завела добивку и упёрлась в ящик; теперь в тексте метрика: добивка
    шага не висит в очереди без хозяина — она «остановлено» с той же причиной."""
    world = await sales_world(session, status=MessageStatus.SENT, due=NOW - timedelta(days=1))
    session.add(
        MessageModel(
            campaign_id=world.letter.campaign_id,
            thread_id=world.thread.id,
            domain_id=world.letter.domain_id,
            step=1,
            status=MessageStatus.QUEUED,
            subject="Re: A question about your team",
            body="Just following up.",
            idempotency_key=followup_key(world.letter.idempotency_key, 1),
        )
    )
    await session.flush()

    report = await followups.send_due(session, transport=_transports(), limit=5, now=NOW)

    assert (report.sent, report.stopped) == (0, 1)
    stuck = await session.scalar(
        select(MessageModel).where(
            MessageModel.thread_id == world.thread.id, MessageModel.step == 1
        )
    )
    assert stuck is not None
    await session.refresh(stuck)
    assert stuck.status is MessageStatus.STOPPED
    assert (stuck.failure_reason or "").startswith(SAID)
