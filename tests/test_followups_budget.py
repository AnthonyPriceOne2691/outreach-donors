"""Потолок прохода добивок — письма, отданные почте, а не захваченные сроки (17.11.2026).

Отложить добивку — окно получателя, потолки ящика — запрос к базе, а не к почте: такие в потолок
прохода не идут, и подошедшие вне окна откладываются одним проходом, а не по `limit` в минуту, — до
`deferred` за проход. Ушедшие, «исход неизвестен» и отказ почты — в потолке. Отказ почты ящику
оставляет след у строки добивки (`letters/followup_refusal.py`), отказ этому письму — нет; новая
попытка шага начинается без следа. Сторож по следу — `tests/test_mail_watch_quiet.py`. Ящики и
адреса — выдуманные, почта — нулевая или подставная.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import followup_refusal
from backend.features.letters.followups import send_due
from backend.features.letters.transport import NullTransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_followups import _chain_start
from tests.test_followups_pace import DUE, _on_one_box
from tests.test_followups_window import (  # noqa: F401 — `_office_hours` — фикстура окна
    SATURDAY,
    _monday_nine,
    _office_hours,
)
from tests.test_mail_watch_quiet import FOREVER, Mail

pytestmark = pytest.mark.usefixtures("_office_hours", "filled_legal")

HOSTS = ("one.example.test", "two.example.test", "three.example.test", "four.example.test")


async def _step_one(session: AsyncSession) -> list[MessageModel]:
    """Строки добивок шага 1 — по порядку заведения."""
    found = await session.scalars(
        select(MessageModel).where(MessageModel.step == 1).order_by(MessageModel.id)
    )
    return list(found)


async def test_followups_outside_the_window_do_not_eat_the_batch(session: AsyncSession) -> None:
    """Суббота: четыре добивки ящика вне окна. Потолок прохода — одна добивка почте, а отложены
    все четыре одним проходом: следующий не найдёт подошедших, и сторожу нечего принять за тишину."""
    letters = await _on_one_box(session, *HOSTS)

    report = await send_due(session, transport=NullTransport(), limit=1, now=SATURDAY)

    assert (report.handed, report.postponed, report.outside) == (0, 4, 4)
    for letter in letters:
        await session.refresh(letter)
        assert letter.next_action_at is not None
        assert letter.next_action_at >= _monday_nine("Europe/Berlin")
    again = await send_due(session, transport=NullTransport(), limit=1, now=SATURDAY)
    assert again.postponed == 0


async def test_deferred_bounds_one_pass(session: AsyncSession) -> None:
    """Отложенных за проход — не больше `deferred` сверх потолка: проход не держит процесс без
    конца, а остальные откладывает следующий."""
    await _on_one_box(session, *HOSTS)

    passes = [
        await send_due(session, transport=NullTransport(), limit=1, now=SATURDAY, deferred=2)
        for _ in range(3)
    ]

    assert [found.postponed for found in passes] == [3, 1, 0]


async def test_mail_refusals_count_in_the_batch_and_leave_a_trace(session: AsyncSession) -> None:
    """Почта отказывает ящикам: отказ — письмо, отданное почте, и он в потолке прохода — третью
    добивку проход не берёт. У отказанных — след с причиной и минутой отказа."""
    starts = [await _chain_start(session, host=host, followup_days=[1, 1]) for host in HOSTS[:3]]

    report = await send_due(session, transport=Mail(FOREVER), limit=2, now=DUE)

    assert (report.handed, report.refused, report.postponed, report.sent) == (2, 2, 2, 0)
    assert "отказала почта — 2" in report.as_report
    refused = await _step_one(session)
    assert [letter.status for letter in refused] == [MessageStatus.QUEUED] * 2
    for letter in refused:
        assert (letter.failure_reason or "").startswith(f"{followup_refusal.MARK}: ")
        assert letter.updated_at == DUE
    third, _ = starts[2]
    await session.refresh(third)
    assert third.next_action_at is not None
    assert third.next_action_at <= DUE  # не тронута: подошла и ждёт следующего прохода


async def test_a_refusal_of_the_letter_counts_but_leaves_no_trace(session: AsyncSession) -> None:
    """Почта отказала этому письму — адресату или содержимому: в потолке прохода, но о ящике это
    ничего не говорит, и следа нет."""
    await _chain_start(session, followup_days=[1, 1])

    report = await send_due(session, transport=Mail(FOREVER, letter=True), limit=5, now=DUE)

    assert (report.handed, report.refused) == (1, 1)
    [refused] = await _step_one(session)
    assert (refused.status, refused.failure_reason) == (MessageStatus.QUEUED, None)


async def test_the_next_attempt_starts_without_the_trace(session: AsyncSession) -> None:
    """След — «последняя попытка кончилась отказом почты»: следующий захват срока снимает его, даже
    если добивка снова ждёт — здесь окна получателя, и ждёт она уже его, а не ящик."""
    first, _ = await _chain_start(session, followup_days=[1, 1])
    await send_due(session, transport=Mail(FOREVER), limit=5, now=DUE)
    [refused] = await _step_one(session)
    marked = refused.failure_reason

    report = await send_due(
        session, transport=NullTransport(), limit=5, now=SATURDAY + timedelta(hours=1)
    )

    assert report.outside == 1
    await session.refresh(refused)
    await session.refresh(first)
    assert (marked or "").startswith(followup_refusal.MARK)
    assert (refused.status, refused.failure_reason) == (MessageStatus.QUEUED, None)
    assert first.next_action_at is not None
    assert first.next_action_at >= _monday_nine("Europe/Berlin")
