"""Ящик судит окно его последних писем — у всех этапов (`outreach/health.py`, 02.11.2026).

У доноров и рекламодателей стояла доля отказов за всю жизнь ящика от 50 писем: при их объёме до 50
не дошёл ни один из двадцати ящиков, и защиты не было при десяти процентах отказов. Здесь — то,
что правило теперь одно: те же пороги, тот же журнал, снижение лимита с учётом разгона, причина
на стороне получателя не в счёт. Модуль продаж здесь не подключён: этапы доноров и рекламодателей
в нём не нуждаются.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel, SenderModel
from backend.features.core.box_rules import SoftSignals
from backend.features.core.stages import CURRENT, MailPolicy
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.outreach import health, limits, senders
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_soft_signals import NOW, _box, _events, _journal

OUTAGE = (
    "unable to get mx info: failed to get IPs from PTR record: lookup <nil>: unrecognized address"
)
ALL_BUT_SALES = [Stage.DONORS, Stage.ADVERTISERS]


@pytest.mark.parametrize("stage", ALL_BUT_SALES)
async def test_the_policy_is_one_with_the_defaults(session: AsyncSession, stage: Stage) -> None:
    policy = await stages.mail_policy(session, stage, "Политика")

    assert policy is CURRENT
    assert policy.soft == SoftSignals()
    assert (policy.window, policy.pace, policy.watch) == (None, None, False)


def test_the_name_stays_where_the_sales_module_takes_it() -> None:
    """Модуль продаж берёт пороги из `outreach/health.py`, а объявлены они в ядре: мост о политике
    живёт в ядре и держит их значением, а не только типом."""
    assert health.SoftSignals is SoftSignals
    assert isinstance(MailPolicy(soft=SoftSignals()).soft, SoftSignals)


@pytest.mark.parametrize("stage", ALL_BUT_SALES)
async def test_the_third_refusal_in_the_window_pauses_the_box(
    session: AsyncSession, stage: Stage
) -> None:
    """Двадцать писем: прежнее правило их не судило вовсе, пока ящик не набрал пятидесяти."""
    box = await _box(session, stage, total=20, bounced=2)

    said = await _events(session, box, "bounce")

    assert said == [box.email]
    assert not box.enabled
    assert box.pause_reason == "отказов 3 в окне 50 писем — порог 5.0%: пауза"
    assert await _journal(session, box) == ["paused"]


@pytest.mark.parametrize("stage", ALL_BUT_SALES)
async def test_two_refusals_do_not(session: AsyncSession, stage: Stage) -> None:
    box = await _box(session, stage, total=20, bounced=1)

    assert await _events(session, box, "bounce") == []
    assert box.enabled


async def test_refusals_older_than_the_window_do_not_count(session: AsyncSession) -> None:
    """Прежние отказы вытесняются новыми письмами: доля за жизнь этого не умела."""
    box = await _box(session, Stage.DONORS, total=60, bounced=3)

    assert await _events(session, box, "bounce") == []
    assert box.enabled


@pytest.mark.parametrize("stage", ALL_BUT_SALES)
async def test_one_complaint_pauses_the_box(session: AsyncSession, stage: Stage) -> None:
    box = await _box(session, stage, total=30)

    said = await _events(session, box, "spamreport")

    assert said == [box.email]
    assert box.pause_reason == "жалоб 1 в окне 50 писем — порог 0.1%: пауза"


@pytest.mark.parametrize("stage", ALL_BUT_SALES)
async def test_refusals_on_the_recipient_side_do_not_count(
    session: AsyncSession, stage: Stage
) -> None:
    """Защита не должна вставать от чужого сбоя: тот же текст, тот же итог, что у продаж."""
    box = await _box(session, stage, total=20)
    letters = (
        await session.scalars(
            select(MessageModel.id)
            .where(MessageModel.sender_id == box.id)
            .order_by(MessageModel.sent_at.desc())
        )
    ).all()
    at = NOW - timedelta(hours=1)
    events = [
        DeliveryEvent("bounce", letter, "x@to.example.test", reason=OUTAGE, soft=True, at=at)
        for letter in letters[:4]
    ]

    report = await apply_events(session, events, now=NOW)

    assert (report.bounced, report.paused_domains, box.enabled) == (4, [], True)
    assert await _journal(session, box) == []


@pytest.mark.parametrize(
    ("days", "allowance"), [(1, 5), (3, 10), (5, 15), (7, 20)], ids=["день 1", "3", "5", "7"]
)
async def test_the_cut_halves_what_the_warmup_allows_today(
    session: AsyncSession, days: int, allowance: int
) -> None:
    """Снижение — от сегодняшней ступени разгона, а не от дневного потолка: ящик на первой ступени
    сверх неё не получит ни письма."""
    box = await _box(session, Stage.DONORS, total=1)
    await _warmed(box, days)

    await _events(session, box, "deferred", "deferred", "deferred")

    cut = await health.cuts(session, Stage.DONORS, NOW)

    assert senders.warmup_state(box, now=NOW).allowance == allowance
    assert cut == {box.id: int(allowance * 0.5)}
    assert await _journal(session, box) == ["deferred", "deferred", "deferred", "limit_cut"]
    screened = limits.screen(
        [box],
        stage=Stage.DONORS,
        sent_today={box.id: int(allowance * 0.5)},
        domains={},
        direction_limit=None,
        now=NOW,
        cuts=cut,
    )
    assert limits.BOX_CUT in screened.why()


async def _warmed(box: SenderModel, days: int) -> None:
    """Первое письмо разгона — `days` дней назад, считая сегодняшний."""
    start = NOW - timedelta(days=days - 1)
    box.warmup_started_at = start - timedelta(hours=1)
    box.warmup_first_sent_at = start


async def test_a_letter_of_another_box_does_not_judge_this_one(session: AsyncSession) -> None:
    """Окно — писем этого ящика: чужие отказы в нём не считаются."""
    one = await _box(session, Stage.DONORS, total=20, bounced=2)
    other = await _box(session, Stage.ADVERTISERS, total=20)
    letter = await session.scalar(
        select(MessageModel.id).where(
            MessageModel.sender_id == other.id, MessageModel.status == MessageStatus.DELIVERED
        )
    )
    assert letter is not None

    report = await apply_events(
        session, [DeliveryEvent("bounce", letter, "x@to.example.test", at=NOW)], now=NOW
    )

    assert (report.paused_domains, one.enabled, other.enabled) == ([], True, True)
