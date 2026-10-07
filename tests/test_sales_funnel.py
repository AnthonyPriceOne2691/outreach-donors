"""Воронка продаж — срез 5.4: лиды на каждом шаге, по гипотезам и периоду (A1–A3 Spec 5.4).

A4 — числа экрана сходятся с запросом к базе — в `test_sales_funnel_api.py`. Строки базы
заводятся прямо (`tests/test_sales_funnel_rows.py`): воронке нужен путь лида — письма,
ответы, передача, — а не сборка и отправка. Адреса выдуманы (`*.example.test`).
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from backend.features.core.domain import MessageStatus, ReplyKind
from backend.features.core.models.outreach import MessageModel
from backend.features.sales import funnel, handoff
from backend.features.sales.funnel import Funnel, Period, Step
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_funnel_rows import NOW, Rows

ALL = Period()


@pytest.fixture
async def rows(session: AsyncSession) -> Rows:
    return Rows(session)


async def _total(session: AsyncSession, period: Period = ALL) -> Funnel:
    return (await funnel.board(session, period)).total


# --- A1: лиды, а не письма ----------------------------------------------------------------------


async def test_a1_three_letters_to_one_lead_are_one_sent_lead(
    session: AsyncSession, rows: Rows
) -> None:
    hypothesis = await rows.hypothesis("цепочка")
    lead = await rows.lead(hypothesis, "jane@acme.example.test")
    for step in range(3):
        await rows.letter(lead, step=step, status=MessageStatus.DELIVERED, days_ago=9 - 3 * step)
    letters = await session.scalar(select(func.count()).select_from(MessageModel))

    total = await _total(session)

    assert (letters, total.sent, total.delivered) == (3, 1, 1)


# --- A2: автоответ — не ответ ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "answered"),
    [
        (ReplyKind.AUTO_REPLY, 0),
        (ReplyKind.BOUNCE, 0),
        (ReplyKind.HUMAN, 1),
        (ReplyKind.UNSUBSCRIBE, 1),
    ],
)
async def test_a2_out_of_office_is_not_an_answer(
    session: AsyncSession, rows: Rows, kind: ReplyKind, answered: int
) -> None:
    hypothesis = await rows.hypothesis("ответы")
    lead = await rows.lead(hypothesis, "olga@beta.example.test")
    await rows.letter(lead, step=0, status=MessageStatus.DELIVERED, days_ago=4)
    await rows.reply(lead, kind, days_ago=3)

    total = await _total(session)

    assert (total.sent, total.answered) == (1, answered)


async def test_a2_out_of_office_then_a_person_is_one_answer(
    session: AsyncSession, rows: Rows
) -> None:
    hypothesis = await rows.hypothesis("ответы")
    lead = await rows.lead(hypothesis, "olga@beta.example.test")
    await rows.letter(lead, step=0, status=MessageStatus.DELIVERED, days_ago=6)
    await rows.reply(lead, ReplyKind.AUTO_REPLY, days_ago=5)
    await rows.reply(lead, ReplyKind.HUMAN, days_ago=2)
    await rows.reply(lead, ReplyKind.HUMAN, days_ago=1)

    assert (await _total(session)).answered == 1


# --- A3: отказ — в «отказ», не в «доставлено» ------------------------------------------------------


async def test_a3_bounced_letter_is_bounced_not_delivered(
    session: AsyncSession, rows: Rows
) -> None:
    hypothesis = await rows.hypothesis("отказы")
    gone = await rows.lead(hypothesis, "nobody@gamma.example.test")
    await rows.letter(gone, step=0, status=MessageStatus.BOUNCED, days_ago=2)
    reached = await rows.lead(hypothesis, "petr@delta.example.test")
    await rows.letter(reached, step=0, status=MessageStatus.DELIVERED, days_ago=2)

    total = await _total(session)

    assert (total.sent, total.delivered, total.bounced) == (2, 1, 1)
    assert await funnel.leads(session, Step.BOUNCED, ALL) == [gone.lead_id]
    assert await funnel.leads(session, Step.DELIVERED, ALL) == [reached.lead_id]


async def test_a3_a_later_bounce_outweighs_an_earlier_delivery(
    session: AsyncSession, rows: Rows
) -> None:
    hypothesis = await rows.hypothesis("отказы")
    lead = await rows.lead(hypothesis, "ivan@epsilon.example.test")
    await rows.letter(lead, step=0, status=MessageStatus.DELIVERED, days_ago=8)
    await rows.letter(lead, step=1, status=MessageStatus.BOUNCED, days_ago=5)

    total = await _total(session)

    assert (total.sent, total.delivered, total.bounced) == (1, 0, 1)


async def test_letter_on_its_way_is_sent_but_neither_delivered_nor_bounced(
    session: AsyncSession, rows: Rows
) -> None:
    hypothesis = await rows.hypothesis("в пути")
    lead = await rows.lead(hypothesis, "anna@zeta.example.test")
    await rows.letter(lead, step=0, status=MessageStatus.SENT, days_ago=1)

    total = await _total(session)

    assert (total.queued, total.sent, total.delivered, total.bounced) == (0, 1, 0, 0)


# --- очередь ---------------------------------------------------------------------------------------


async def test_queued_until_a_letter_of_the_chain_goes(session: AsyncSession, rows: Rows) -> None:
    hypothesis = await rows.hypothesis("очередь")
    waiting = await rows.lead(hypothesis, "first@eta.example.test")
    await rows.letter(waiting, step=0, status=MessageStatus.QUEUED, days_ago=1)
    # Застрявшая в очереди добивка — у лида, которому уже писали: он «отправлен».
    written = await rows.lead(hypothesis, "second@theta.example.test")
    await rows.letter(written, step=0, status=MessageStatus.DELIVERED, days_ago=6)
    await rows.letter(written, step=1, status=MessageStatus.QUEUED, days_ago=1)
    # Снятое письмо не ждёт и не ушло.
    removed = await rows.lead(hypothesis, "third@iota.example.test")
    await rows.letter(removed, step=0, status=MessageStatus.STOPPED, days_ago=3)

    total = await _total(session)

    assert (total.queued, total.sent) == (1, 1)
    assert await funnel.leads(session, Step.QUEUED, ALL) == [waiting.lead_id]
    assert await funnel.leads(session, Step.SENT, ALL) == [written.lead_id]


async def test_queued_lead_belongs_to_the_period_it_was_queued_in(
    session: AsyncSession, rows: Rows
) -> None:
    hypothesis = await rows.hypothesis("очередь")
    old = await rows.lead(hypothesis, "old@kappa.example.test")
    await rows.letter(old, step=0, status=MessageStatus.QUEUED, days_ago=12)
    fresh = await rows.lead(hypothesis, "fresh@lambda.example.test")
    await rows.letter(fresh, step=0, status=MessageStatus.QUEUED, days_ago=2)
    week = Period(since=NOW - timedelta(days=7))

    assert await funnel.leads(session, Step.QUEUED, week) == [fresh.lead_id]
    assert (await _total(session)).queued == 2


# --- период — по первому письму лида -------------------------------------------------------------


async def test_period_is_by_the_first_letter_of_the_lead(session: AsyncSession, rows: Rows) -> None:
    hypothesis = await rows.hypothesis("период")
    earlier = await rows.lead(hypothesis, "early@mu.example.test")
    await rows.letter(earlier, step=0, status=MessageStatus.DELIVERED, days_ago=10)
    await rows.letter(earlier, step=1, status=MessageStatus.DELIVERED, days_ago=3)
    inside = await rows.lead(hypothesis, "inside@nu.example.test")
    await rows.letter(inside, step=0, status=MessageStatus.DELIVERED, days_ago=5)
    # Ответ пришёл уже после конца периода — лид периода всё равно ответил.
    await rows.reply(inside, ReplyKind.HUMAN, days_ago=0)
    week = Period(since=NOW - timedelta(days=7), until=NOW - timedelta(days=1))

    found = await _total(session, week)

    assert (found.sent, found.delivered, found.answered) == (1, 1, 1)
    assert await funnel.leads(session, Step.SENT, week) == [inside.lead_id]
    # Два периода подряд делят лидов, а не письма: лид — в одном из них.
    before = Period(until=NOW - timedelta(days=7))
    assert await funnel.leads(session, Step.SENT, before) == [earlier.lead_id]


async def test_period_takes_its_start_and_leaves_its_end(session: AsyncSession, rows: Rows) -> None:
    """Полуинтервал: первое письмо ровно в начале — в периоде, ровно в конце — уже в следующем."""
    hypothesis = await rows.hypothesis("границы")
    at_start = await rows.lead(hypothesis, "start@alpha.example.test")
    await rows.letter(at_start, step=0, status=MessageStatus.SENT, days_ago=7)
    at_end = await rows.lead(hypothesis, "end@beta.example.test")
    await rows.letter(at_end, step=0, status=MessageStatus.SENT, days_ago=3)
    period = Period(since=NOW - timedelta(days=7), until=NOW - timedelta(days=3))
    after = Period(since=NOW - timedelta(days=3))

    assert await funnel.leads(session, Step.SENT, period) == [at_start.lead_id]
    assert await funnel.leads(session, Step.SENT, after) == [at_end.lead_id]


async def test_gone_letter_without_send_time_counts_by_the_time_it_was_written(
    session: AsyncSession, rows: Rows
) -> None:
    hypothesis = await rows.hypothesis("период")
    lead = await rows.lead(hypothesis, "no-time@xi.example.test")
    await rows.letter(lead, step=0, status=MessageStatus.SENT, days_ago=2, sent=False)
    week = Period(since=NOW - timedelta(days=7))

    assert await funnel.leads(session, Step.SENT, week) == [lead.lead_id]


# --- наш ответ в переписке воронку не двигает -------------------------------------------------------


async def test_our_answer_in_the_dialog_does_not_move_the_funnel(
    session: AsyncSession, rows: Rows
) -> None:
    hypothesis = await rows.hypothesis("переписка")
    lead = await rows.lead(hypothesis, "talk@omicron.example.test")
    await rows.letter(lead, step=0, status=MessageStatus.DELIVERED, days_ago=6)
    reply = await rows.reply(lead, ReplyKind.HUMAN, days_ago=5)
    await rows.letter(lead, step=0, status=MessageStatus.BOUNCED, days_ago=4, answers=reply)
    quiet = await rows.lead(hypothesis, "quiet@pi.example.test")
    await rows.letter(quiet, step=0, status=MessageStatus.DELIVERED, days_ago=6)
    answer_to_quiet = await rows.reply(quiet, ReplyKind.HUMAN, days_ago=5)
    await rows.letter(
        quiet, step=0, status=MessageStatus.QUEUED, days_ago=1, answers=answer_to_quiet
    )

    total = await _total(session)

    assert (total.queued, total.sent, total.delivered, total.bounced, total.answered) == (
        0,
        2,
        2,
        0,
        2,
    )


# --- передан ---------------------------------------------------------------------------------------


async def test_handed_off_by_the_rule_that_stops_the_chain(
    session: AsyncSession, rows: Rows
) -> None:
    hypothesis = await rows.hypothesis("передача")
    warm = await rows.lead(hypothesis, "warm@rho.example.test")
    await rows.letter(warm, step=0, status=MessageStatus.DELIVERED, days_ago=4)
    await rows.reply(warm, ReplyKind.HUMAN, days_ago=3)
    await rows.hand_off(warm)
    cold = await rows.lead(hypothesis, "cold@sigma.example.test")
    await rows.letter(cold, step=0, status=MessageStatus.DELIVERED, days_ago=4)
    await rows.reply(cold, ReplyKind.HUMAN, days_ago=3)

    total = await _total(session)

    assert (total.answered, total.handed_off) == (2, 1)
    assert await funnel.leads(session, Step.HANDED_OFF, ALL) == [warm.lead_id]
    # Тот же ответ у шва цепочки: переданному не пишут — он же «передан».
    assert await handoff.handed_off(session, warm.lead_id) is True
    assert await handoff.handed_off(session, cold.lead_id) is False


# --- гипотезы, итог, чужие письма --------------------------------------------------------------------


async def test_every_hypothesis_has_a_row_and_the_total_sums_them(
    session: AsyncSession, rows: Rows
) -> None:
    first = await rows.hypothesis("первая")
    for n, status in enumerate((MessageStatus.DELIVERED, MessageStatus.BOUNCED)):
        lead = await rows.lead(first, f"lead{n}@tau.example.test")
        await rows.letter(lead, step=0, status=status, days_ago=3)
    second = await rows.hypothesis("вторая")
    lead = await rows.lead(second, "only@upsilon.example.test")
    await rows.letter(lead, step=0, status=MessageStatus.QUEUED, days_ago=1)
    empty = await rows.hypothesis("пустая")

    found = await funnel.board(session, ALL)

    assert [(row.hypothesis_id, row.name) for row in found.rows] == [
        (first, "первая"),
        (second, "вторая"),
        (empty, "пустая"),
    ]
    assert [row.funnel for row in found.rows] == [
        Funnel(sent=2, delivered=1, bounced=1),
        Funnel(queued=1),
        Funnel(),
    ]
    assert found.total == Funnel(queued=1, sent=2, delivered=1, bounced=1)

    narrowed = await funnel.board(session, ALL, hypothesis_id=second)

    assert [row.hypothesis_id for row in narrowed.rows] == [second]
    assert narrowed.total == Funnel(queued=1)


async def test_letters_of_other_directions_are_not_counted(
    session: AsyncSession, rows: Rows
) -> None:
    await rows.donor_letter("donor@phi.example.test")
    hypothesis = await rows.hypothesis("продажи")
    lead = await rows.lead(hypothesis, "lead@chi.example.test")
    await rows.letter(lead, step=0, status=MessageStatus.DELIVERED, days_ago=2)

    assert (await _total(session)).sent == 1


async def test_list_of_a_step_is_its_number(session: AsyncSession, rows: Rows) -> None:
    """Выгрузка и число — одним правилом: список шага той же длины, что его число."""
    hypothesis = await rows.hypothesis("сверка")
    statuses = (
        MessageStatus.QUEUED,
        MessageStatus.SENT,
        MessageStatus.DELIVERED,
        MessageStatus.BOUNCED,
    )
    for n, status in enumerate(statuses):
        lead = await rows.lead(hypothesis, f"check{n}@psi.example.test")
        await rows.letter(lead, step=0, status=status, days_ago=2 + n)
        if status is MessageStatus.DELIVERED:
            await rows.reply(lead, ReplyKind.HUMAN, days_ago=1)
            await rows.hand_off(lead)

    total = await _total(session)

    for step in Step:
        assert len(await funnel.leads(session, step, ALL)) == getattr(total, step.value), step
    assert total == Funnel(queued=1, sent=3, delivered=1, bounced=1, answered=1, handed_off=1)
