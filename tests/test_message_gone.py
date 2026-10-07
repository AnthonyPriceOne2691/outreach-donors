"""«Письмо ушло» — одно определение на всех (`core.domain.GONE_STATUSES`).

До 07.10.2026 его держали трое своими списками: главная (`ops/overview.py`),
перепоиск адресов (`contacts/refind.py`) и строка диалога
(`outreach/threads.summarize`). Списки совпадали, но новый статус письма
разошёлся бы между ними молча — каждый посчитал бы его по-своему.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.features.core.domain import GONE_STATUSES, MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.outreach.threads import summarize

#: Не ушло: ждёт очереди, отдаётся почте (исход ещё не известен), остановлено.
NOT_GONE = frozenset({MessageStatus.QUEUED, MessageStatus.SENDING, MessageStatus.STOPPED})


def test_gone_is_what_the_platform_took() -> None:
    """Отказ доставки — тоже ушедшее письмо, просто не дошедшее."""
    assert GONE_STATUSES == (MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED)


def test_every_status_is_decided_gone_or_not() -> None:
    """Новый статус письма молча не пройдёт: тест требует решить, ушло ли
    письмо в нём, и дописать его в одно из двух множеств."""
    assert NOT_GONE.isdisjoint(GONE_STATUSES)
    assert NOT_GONE | set(GONE_STATUSES) == set(MessageStatus)


@pytest.mark.parametrize("status", list(MessageStatus))
def test_thread_counts_by_the_same_definition(status: MessageStatus) -> None:
    letter = MessageModel(
        campaign_id=1,
        domain_id=1,
        step=0,
        status=status,
        idempotency_key="k",
        sent_at=datetime(2026, 10, 7, 12, 0, tzinfo=UTC),
    )

    summary = summarize([letter], [])

    assert summary.messages_sent == (1 if status in GONE_STATUSES else 0)
