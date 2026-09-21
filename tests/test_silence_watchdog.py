"""Сторож тишины: поломки, которые выглядят как «ничего не происходит».

Каждая тревога — это «при X не может быть Y». Тишина сама по себе
не поломка: писем не было — событий и не ждём. Поломка — тишина там,
где обязано быть шумно.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from backend.features.core import usage
from backend.features.core.domain import MessageStatus, ReplyKind, RunStatus, Stage
from backend.features.core.models.outreach import CampaignModel, MessageModel, ReplyModel
from backend.features.core.models.run import RunModel
from backend.features.donors.verdict import Thresholds
from backend.features.ops.silence import alarms
from backend.features.runs.repository import RunRepository
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
HOST = "donor.example.test"
T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)


async def _campaign(session: AsyncSession) -> CampaignModel:
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add(campaign)
    await session.flush()
    return campaign


async def _letter(
    session: AsyncSession,
    *,
    status: MessageStatus,
    sent_at: datetime | None = None,
    next_action_at: datetime | None = None,
    provider_message_id: str | None = "sg-1",
    number: int = 0,
) -> MessageModel:
    domain = await make_donor(session, f"d{number}-{HOST}")
    campaign = await _campaign(session)
    message = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain.id,
        step=0,
        status=status,
        subject="Hi",
        body="Hi",
        sent_at=sent_at,
        next_action_at=next_action_at,
        provider_message_id=provider_message_id,
        idempotency_key=f"donors:d{number}:0",
    )
    session.add(message)
    await session.flush()
    return message


def _codes(found: list) -> set[str]:
    return {alarm.code for alarm in found}


class TestQuietIsNotAlwaysBroken:
    async def test_empty_base_is_silent_for_a_reason(self, session: AsyncSession) -> None:
        assert await alarms(session, now=NOW) == []

    async def test_fresh_letter_waits_without_alarm(self, session: AsyncSession) -> None:
        """Событие доставки идёт минутами — час ожидания не поломка."""
        await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=1))

        assert "delivery-silence" not in _codes(await alarms(session, now=NOW))

    async def test_null_transport_does_not_raise_the_alarm(self, session: AsyncSession) -> None:
        """Нулевой транспорт событий не порождает: молчание при нём —
        его устройство, а не поломка."""
        await _letter(
            session,
            status=MessageStatus.SENT,
            sent_at=NOW - timedelta(days=2),
            provider_message_id="null-7",
        )

        assert "delivery-silence" not in _codes(await alarms(session, now=NOW))


class TestSilenceThatMeansBroken:
    async def test_platform_says_nothing_about_delivery(self, session: AsyncSession) -> None:
        await _letter(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=8))

        found = await alarms(session, now=NOW)

        assert "delivery-silence" in _codes(found)
        assert "вебхук" in next(a.detail for a in found if a.code == "delivery-silence")

    async def test_no_replies_at_all_on_a_delivered_batch(self, session: AsyncSession) -> None:
        for number in range(20):
            await _letter(session, status=MessageStatus.DELIVERED, number=number)

        assert "reply-silence" in _codes(await alarms(session, now=NOW))

    async def test_one_recent_reply_is_enough_to_keep_quiet(self, session: AsyncSession) -> None:
        for number in range(20):
            await _letter(session, status=MessageStatus.DELIVERED, number=number)
        session.add(ReplyModel(kind=ReplyKind.HUMAN, from_email="a@b.c", raw_body="Hi"))
        await session.flush()

        assert "reply-silence" not in _codes(await alarms(session, now=NOW))

    async def test_followups_are_standing_still(self, session: AsyncSession) -> None:
        await _letter(
            session,
            status=MessageStatus.SENT,
            sent_at=NOW - timedelta(days=8),
            next_action_at=NOW - timedelta(hours=3),
        )

        assert "followups-stuck" in _codes(await alarms(session, now=NOW))

    async def test_run_is_running_but_silent(self, session: AsyncSession) -> None:
        settings = await RunRepository(session).create_settings(
            T,
            geo_top_n=5,
            geo_min_share=0.2,
            metrics_ttl_days=90,
            price_ttl_days=150,
            units_cap=100_000,
        )
        run = RunModel(
            stage=Stage.DONORS,
            settings_id=settings.id,
            status=RunStatus.RUNNING,
            keywords=["a"],
            country="us",
        )
        session.add(run)
        await session.flush()
        run.updated_at = NOW - timedelta(hours=2)
        await session.flush()

        assert "runs-stuck" in _codes(await alarms(session, now=NOW))

    async def test_cap_is_spent(self, session: AsyncSession) -> None:
        """Прогоны не идут не потому, что сломались, — деньги кончились.
        Выглядит это одинаково, поэтому названо словами."""
        usage.record(session, operation="batch_metrics", units=100_000)
        await session.flush()

        found = await alarms(session, now=NOW)

        assert "cap-reached" in _codes(found)
