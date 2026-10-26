"""Ответы модуля продаж прогнозу рассылки — настоящим модулем через мост (`core/stage_forecast.py`).

Темп автоотправки — в политике почты продаж, только пока автоотправка включена (`mail.policy`);
пояса получателей очереди — тем же правилом пояса лида, что у прохода автоотправки
(`autosend_window.queue_zones`); запас сборки — правилом самой сборки (`queue_plan.supply`). И
прогноз этапа продаж, собранный с ними: окно в поясе очереди, темп, запас. Мир — подключённые
продажи (`tests/test_sales_send_world.py`); адреса и тексты выдуманные.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from backend.config import sales as sales_cfg
from backend.features.core import stage_forecast, window
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.core.window import Pace
from backend.features.letters import forecast as mailing
from backend.features.sales import autosend, autosend_window, forecast_answers, mail
from backend.features.sales.models import SalesLeadModel, SalesThreadModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def _letter(
    session: AsyncSession,
    lead: SalesLeadModel,
    *,
    status: MessageStatus = MessageStatus.QUEUED,
    followup: MessageStatus | None = None,
) -> None:
    """Первое письмо продаж лиду — с перепиской и связью с лидом, как их заводит сборка; `followup` —
    ещё и добивка шага 1 в той же переписке."""
    campaign = await session.scalar(select(CampaignModel).where(CampaignModel.stage == Stage.SALES))
    if campaign is None:
        campaign = CampaignModel(stage=Stage.SALES, name="Выдуманная рассылка", status="running")
        session.add(campaign)
        await session.flush()
    thread = ThreadModel(domain_id=lead.domain_id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    session.add(
        SalesThreadModel(thread_id=thread.id, lead_id=lead.id, language="en", chain_version="v")
    )
    host = await session.scalar(select(DomainModel.host).where(DomainModel.id == lead.domain_id))
    steps = [(0, status)] + ([] if followup is None else [(1, followup)])
    session.add_all(
        MessageModel(
            campaign_id=campaign.id,
            thread_id=thread.id,
            domain_id=lead.domain_id,
            step=step,
            status=state,
            idempotency_key=f"sales:{host}:{lead.email}:{step}",
        )
        for step, state in steps
    )
    await session.flush()


async def test_pace_is_in_the_policy_only_while_autosend_is_on(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Выключена — пусто: письма уходят пачкой по кнопке. Включена — пауза прохода и разброс."""
    assert (await mail.policy(session)).pace is None

    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)

    assert (await mail.policy(session)).pace == Pace(autosend.gap(), autosend.SPREAD)


async def test_queue_zones_follow_the_lead_zone_rule_of_the_pass(
    session: AsyncSession, world: w.World
) -> None:
    """Пояс лида, затем его страны; не известен никому или нет в базе поясов — `None`. Только
    первые письма в очереди: ушедшее письмо и добивка — не очередь."""
    one = world.hypothesis_id
    for email, fields in [
        ("anna@one.example.test", {}),
        ("ben@two.example.test", {}),
        ("carl@three.example.test", {"timezone": None, "country": "de"}),
        ("dana@four.example.test", {"timezone": "America/New_York"}),
        ("emil@five.example.test", {"timezone": None}),
        ("fred@six.example.test", {"timezone": "Made/Up"}),
    ]:
        await _letter(session, await w.lead(session, one, email, **fields))
    gone = await w.lead(session, one, "gone@seven.example.test", timezone="Asia/Tokyo")
    await _letter(session, gone, status=MessageStatus.SENT, followup=MessageStatus.QUEUED)

    found = await autosend_window.queue_zones(session)

    assert found == {"Europe/Berlin": 3, "America/New_York": 1, None: 2}
    assert await stage_forecast.sales_zones(session) == found


async def test_stuck_letters_are_counted_by_the_rule_of_the_pass(
    session: AsyncSession, world: w.World
) -> None:
    """Письмо, чей адрес держит проверка, и письмо без пояса получателя не уйдут, пока их не
    поправят, — тем же счётом, что у прохода автоотправки; остальные письма очереди уйдут."""
    one = world.hypothesis_id
    held = await w.lead(
        session, one, "held@one.example.test", verification_status="made-up:unknown"
    )
    zoneless = await w.lead(session, one, "nozone@two.example.test", timezone=None)
    going = await w.lead(session, one, "going@three.example.test")
    for lead in (held, zoneless, going):
        await _letter(session, lead)

    found = await autosend_window.queue_stuck(session)

    assert found == {autosend_window.HELD.format(1): 1, window.NO_ZONE: 1}
    assert await forecast_answers.queue_stuck(session) == found
    assert await stage_forecast.sales_stuck(session) == found


async def test_the_sales_forecast_is_built_from_the_module_answers(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Очередь в Нью-Йорке, автоотправка включена, лид без письма — запас сборки."""
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    one = world.hypothesis_id
    await w.lead(session, one, "jane@acme.example.test")
    queued = await w.lead(session, one, "dana@four.example.test", timezone="America/New_York")
    await _letter(session, queued)

    found = await mailing.forecast(session, Stage.SALES, days=2, back=0, now=w.NOW)

    assert (found.queued, found.extra, found.blocked, found.stuck) == (1, 1, None, {})
    assert found.clock.zone is not None
    assert found.clock.zone.key == "America/New_York"
    assert found.clock.pace == Pace(timedelta(minutes=sales_cfg.AUTOSEND_GAP_MIN), autosend.SPREAD)
    assert found.eta.day == w.NOW.date()
