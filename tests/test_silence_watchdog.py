"""Сторож тишины: поломки, которые выглядят как «ничего не происходит».

Каждая тревога — это «при X не может быть Y». Тишина сама по себе
не поломка: писем не было — событий и не ждём. Поломка — тишина там,
где обязано быть шумно.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.api.settings import routes as settings_routes
from backend.config import serp as serp_cfg
from backend.features.core import usage
from backend.features.core.domain import (
    CrawlOutcome,
    MessageStatus,
    ReplyKind,
    RunStatus,
    Stage,
    StopReason,
)
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ReplyModel
from backend.features.core.models.run import RunModel
from backend.features.donors.verdict import Thresholds
from backend.features.ops import silence as silence_module
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


class TestCrawlBlocked:
    """Требование просит при доле отказов выше 30% «паузу и алерт».

    Паузу обход ставит себе сам; тревоги не было — останов был виден
    только в логе, а сторож про краул не знал вовсе.
    """

    @staticmethod
    async def _crawl(
        session: AsyncSession,
        *,
        outcome: CrawlOutcome = CrawlOutcome.OK,
        stop: StopReason = StopReason.EXHAUSTED,
        number: int = 1,
    ) -> None:
        for index in range(number):
            session.add(
                CrawlRunModel(
                    host=f"donor{index}.example.test",
                    outcome=outcome,
                    stop_reason=stop,
                    pages_opened=0 if outcome is CrawlOutcome.BLOCKED else 10,
                    articles=0,
                )
            )
        await session.flush()

    async def test_a_stopped_crawl_raises_the_alarm(self, session: AsyncSession) -> None:
        await self._crawl(session, outcome=CrawlOutcome.BLOCKED, stop=StopReason.UNHEALTHY)

        codes = {alarm.code for alarm in await alarms(session)}

        assert "crawl-blocked" in codes

    async def test_most_donors_closing_raises_it_too(self, session: AsyncSession) -> None:
        await self._crawl(session, outcome=CrawlOutcome.BLOCKED, stop=StopReason.NO_START, number=3)
        await self._crawl(session, number=1)

        codes = {alarm.code for alarm in await alarms(session)}

        assert "crawl-blocked" in codes

    async def test_a_healthy_crawl_is_silent(self, session: AsyncSession) -> None:
        await self._crawl(session, number=5)

        codes = {alarm.code for alarm in await alarms(session)}

        assert "crawl-blocked" not in codes

    async def test_no_crawls_at_all_is_not_an_alarm(self, session: AsyncSession) -> None:
        """Сторож говорит про то, что сломалось, а не про то,
        что ещё не начинали."""
        codes = {alarm.code for alarm in await alarms(session)}

        assert "crawl-blocked" not in codes

    async def test_the_alarm_says_what_to_do(self, session: AsyncSession) -> None:
        """«Обход упирается» без продолжения — полсообщения."""
        await self._crawl(session, outcome=CrawlOutcome.BLOCKED, stop=StopReason.UNHEALTHY)

        found = next(a for a in await alarms(session) if a.code == "crawl-blocked")

        assert "каскада" in found.detail


class TestProvidersNotConfigured:
    """Ключей выдачи ещё нет — сервис уже на сервере. Это настройка,
    а не авария: ни пятисотки, ни трассировки каждые десять минут."""

    async def test_probe_names_it_instead_of_crashing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        class QuietAhrefs:
            async def limits_and_usage(self) -> None:
                return None

            async def aclose(self) -> None:
                return None

        monkeypatch.setattr(silence_module, "AhrefsClient", QuietAhrefs)
        monkeypatch.setattr(serp_cfg, "PROVIDER", "dataforseo")
        monkeypatch.setattr(serp_cfg, "LOGIN", "")

        alarm = await silence_module.probe_providers()

        assert alarm is not None
        assert "не настроен" in alarm.detail

    async def test_usage_screen_opens_without_serp_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:

        class QuietAhrefs:
            async def aclose(self) -> None:
                return None

        monkeypatch.setattr(settings_routes, "AhrefsClient", QuietAhrefs)
        monkeypatch.setattr(serp_cfg, "PROVIDER", "dataforseo")
        monkeypatch.setattr(serp_cfg, "LOGIN", "")

        left, error = await settings_routes._serp_balance()

        assert left is None
        assert error is not None
        assert "не подключён" in error
