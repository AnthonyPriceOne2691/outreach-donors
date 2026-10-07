"""Окно получателя в отправке и добивках — срез 4.3 (Ф4); модуль продаж подставной.

Политику и пояса получателя отдаёт модуль продаж мостом `core/stages.py`. Письмо продаж
вне окна не уходит и не теряется, ответ в переписке окна не ждёт, доноры и рекламодатели
пишут как раньше. Мир — тот же, что у моста 4.6b (`tests/test_sales_stage_bridge.py`).
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from backend.config import sales as sales_cfg
from backend.features.core import stages, window
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.core.stages import CURRENT, MailPolicy
from backend.features.core.window import SendWindow
from backend.features.letters import batch, followups
from backend.features.letters.chain import ANSWER_STEP
from backend.features.letters.sending import OutsideWindowError, Sending, UnknownZoneError
from backend.features.letters.transport import NullTransport
from backend.features.sales import policy
from backend.features.sales.models import SalesLeadModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_stage_bridge import (
    LEAD_EMAIL,
    SALES_BOX,
    FakeSalesMail,
    _first_letter_sent,
    _seen,
    _transports,
)
from tests.test_sales_stage_mail import LEAD, _donor_chain, sales_world

BERLIN = ZoneInfo("Europe/Berlin")
WEEKDAYS_9_17 = SendWindow(days=frozenset(range(5)), start=time(9), end=time(17))

#: Суббота 10:00 и понедельник 09:00 по часам получателя в Берлине.
SATURDAY = datetime(2026, 10, 10, 10, 0, tzinfo=BERLIN).astimezone(UTC)
MONDAY_NINE = datetime(2026, 10, 12, 9, 0, tzinfo=BERLIN).astimezone(UTC)
#: Понедельник 14:00 в Берлине — окно открыто.
MONDAY = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def sales(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Модуль продаж подключён: окно пн–пт 9–17, получатель в Берлине, сдвиг с зерном."""
    monkeypatch.setattr(stages._SALES, "load", None)
    monkeypatch.setattr(window, "JITTER", random.Random(5))
    found = FakeSalesMail(rules=MailPolicy(window=WEEKDAYS_9_17), zones=(None, "Europe/Berlin"))
    stages.register_sales(lambda: found)
    return found


async def test_a1_saturday_followup_waits_for_monday_nine_to_half_past(
    session: AsyncSession, filled_legal: None, sales: FakeSalesMail
) -> None:
    first, _ = await _first_letter_sent(session)
    first.next_action_at = SATURDAY - timedelta(minutes=1)
    await session.flush()
    source = _transports()

    report = await followups.send_due(session, transport=source, limit=5, now=SATURDAY)

    assert (report.sent, report.postponed) == (0, 1)
    assert _seen(source) == []
    await session.refresh(first)
    shift = timedelta(minutes=30) * random.Random(5).random()
    assert first.next_action_at == MONDAY_NINE + shift
    local = first.next_action_at.astimezone(BERLIN)
    assert (local.weekday(), time(9) <= local.time() < time(9, 30)) == (0, True)

    later = await followups.send_due(
        session, transport=source, limit=5, now=MONDAY_NINE + timedelta(minutes=30)
    )

    assert later.sent == 1
    [outgoing] = _seen(source)
    assert (outgoing.to, outgoing.from_email) == (LEAD_EMAIL, SALES_BOX)


async def test_first_letter_outside_the_window_stays_queued_and_is_named(
    session: AsyncSession, filled_legal: None, sales: FakeSalesMail
) -> None:
    world = await sales_world(session)
    source = _transports()

    with pytest.raises(OutsideWindowError) as refused:
        await Sending(session, source, now=SATURDAY).send(world.letter.id)

    assert "вне окна получателя (пн–пт 09:00–17:00 по его часам)" in str(refused.value)
    assert "пн 12.10 09:" in str(refused.value)
    assert batch.why(refused.value) == "вне окна получателя"
    assert source.asked == ["sales"]
    assert _seen(source) == []
    await session.refresh(world.letter)
    assert (world.letter.status, world.letter.sender_id) == (MessageStatus.QUEUED, None)

    await Sending(session, source, now=MONDAY).send(world.letter.id)

    await session.refresh(world.letter)
    assert world.letter.status is MessageStatus.SENT


async def test_without_any_zone_the_first_letter_waits_and_the_refusal_says_why(
    session: AsyncSession, filled_legal: None, sales: FakeSalesMail
) -> None:
    sales.zones = (None, None, None)
    world = await sales_world(session)

    with pytest.raises(UnknownZoneError, match="пояс получателя неизвестен") as refused:
        await Sending(session, _transports(), now=MONDAY).send(world.letter.id)

    assert batch.why(refused.value) == "пояс получателя неизвестен"
    await session.refresh(world.letter)
    assert world.letter.status is MessageStatus.QUEUED


async def test_an_answer_in_the_thread_does_not_wait_for_the_window(
    session: AsyncSession, filled_legal: None, sales: FakeSalesMail
) -> None:
    """Собеседник написал сам: ответ ему — сейчас, а не в понедельник, и без вопроса о политике."""
    sales.broken = "policy"
    first, box = await _first_letter_sent(session)
    answer = MessageModel(
        campaign_id=first.campaign_id,
        thread_id=first.thread_id,
        domain_id=first.domain_id,
        contact_id=first.contact_id,
        step=ANSWER_STEP,
        status=MessageStatus.QUEUED,
        subject="Re: A question about your team",
        body="Thanks, here is more.",
        idempotency_key=f"sales:{LEAD}:reply:1",
    )
    session.add(answer)
    await session.flush()
    source = _transports()

    await Sending(session, source, now=SATURDAY).send(answer.id, from_sender_id=box.id)

    assert [outgoing.body for outgoing in _seen(source)] == ["Thanks, here is more."]


async def test_a3_donor_followup_at_night_goes_as_before(
    session: AsyncSession, filled_legal: None, sales: FakeSalesMail
) -> None:
    night = datetime(2026, 10, 10, 1, 0, tzinfo=UTC)  # суббота, 03:00 в Берлине
    donor = await _donor_chain(session, due=night - timedelta(minutes=5))

    report = await followups.send_due(session, transport=NullTransport(), limit=5, now=night)

    assert (report.sent, report.postponed) == (1, 0)
    await session.refresh(donor)
    assert donor.next_action_at is None


async def test_donors_and_advertisers_keep_the_policy_they_had(
    session: AsyncSession, sales: FakeSalesMail, monkeypatch: pytest.MonkeyPatch
) -> None:
    policy_of = [await stages.mail_policy(session, stage, "Политика") for stage in Stage]
    assert policy_of == [CURRENT, CURRENT, MailPolicy(window=WEEKDAYS_9_17)]
    assert CURRENT.window is None
    # Модуль не подключён — писем продаж нет: отправка откажет им раньше окна.
    monkeypatch.setattr(stages._SALES, "load", None)
    assert await stages.mail_policy(session, Stage.SALES, "Политика") is CURRENT


async def test_a4_sales_chain_goes_three_then_five_days_after_the_previous_letter(
    session: AsyncSession, filled_legal: None, sales: FakeSalesMail
) -> None:
    world = await sales_world(session)
    campaign = await session.get(CampaignModel, world.letter.campaign_id)
    assert campaign is not None
    campaign.followup_days = [3, 5]
    await session.flush()

    await Sending(session, _transports(), now=MONDAY).send(world.letter.id)
    await session.refresh(world.letter)
    assert world.letter.next_action_at == MONDAY + timedelta(days=3)

    thursday = MONDAY + timedelta(days=3)
    assert (await followups.send_due(session, transport=_transports(), limit=5, now=thursday)).sent
    second = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
    assert second is not None
    assert (second.sent_at, second.next_action_at) == (thursday, thursday + timedelta(days=5))

    tuesday = thursday + timedelta(days=5)
    assert (await followups.send_due(session, transport=_transports(), limit=5, now=tuesday)).sent
    third = await session.scalar(select(MessageModel).where(MessageModel.step == 2))
    assert third is not None
    assert (third.sent_at, third.next_action_at) == (tuesday, None)


def test_the_sales_policy_takes_the_window_from_the_sales_settings() -> None:
    found = policy.sales_policy().window

    assert found is not None
    assert (found.words, found.spread) == ("пн–пт 09:00–17:00", timedelta(minutes=30))


@pytest.mark.parametrize(
    ("timezone", "country", "hypothesis", "expected"),
    [
        # A2: у лида пояса нет, страна DE — окно по Берлину.
        (None, "de", None, "Europe/Berlin"),
        ("Asia/Tokyo", "de", None, "Asia/Tokyo"),
        (None, None, "America/New_York", "America/New_York"),
        (None, "zz", "America/New_York", "America/New_York"),
        (None, None, None, None),
    ],
)
def test_a2_zones_of_a_lead_go_lead_country_hypothesis(
    timezone: str | None, country: str | None, hypothesis: str | None, expected: str | None
) -> None:
    lead = SalesLeadModel(email="jane@lead.example.test", timezone=timezone, country=country)

    found = window.zone_of(policy.zones_of(lead, hypothesis))

    assert (found.key if found else None) == expected


@pytest.mark.parametrize(
    ("days", "hours", "expected"),
    [
        ("1-5", "09:00-17:00", (frozenset(range(5)), time(9), time(17))),
        ("1,3,5", "10:30-16:00", (frozenset({0, 2, 4}), time(10, 30), time(16))),
        ("6-7", "11:00-12:00", (frozenset({5, 6}), time(11), time(12))),
        ("1-8", "09:00-17:00", "SALES_SEND_DAYS"),
        ("пн-пт", "09:00-17:00", "SALES_SEND_DAYS"),
        ("", "09:00-17:00", "SALES_SEND_DAYS"),
        ("1-5", "17:00-09:00", "SALES_SEND_HOURS"),
        ("1-5", "с девяти", "SALES_SEND_HOURS"),
    ],
)
def test_window_settings_are_read_at_start_and_a_typo_is_refused_in_words(
    days: str, hours: str, expected: tuple[frozenset[int], time, time] | str
) -> None:
    if isinstance(expected, str):
        with pytest.raises(ValueError, match=expected):
            sales_cfg._days(days)
            sales_cfg._hours(hours)
        return
    assert (sales_cfg._days(days), *sales_cfg._hours(hours)) == expected
