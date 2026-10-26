"""Ответы модуля продаж прогнозу рассылки — настоящим модулем через мост (`core/stage_forecast.py`).

Темп автоотправки — в политике почты продаж, только пока автоотправка включена (`mail.policy`);
пояса получателей очереди — тем же правилом пояса лида, что у прохода автоотправки
(`autosend_window.queue_zones`); запас сборки — правилом самой сборки (`queue_plan.supply`). И
прогноз этапа продаж, собранный с ними: окно в поясе очереди, темп, запас. Почему автоотправка
стоит — словами её строки, только то, чего прогноз сам не видит (`forecast_answers.autosend_stop`).
Мир — подключённые продажи (`tests/test_sales_send_world.py`); адреса и тексты выдуманные.
"""

from __future__ import annotations

from datetime import time, timedelta

import pytest
from backend.config import sales as sales_cfg
from backend.features.core import stage_forecast, window
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.core.window import Pace
from backend.features.letters import forecast as mailing
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.unknown_outcome import STUCK_AFTER
from backend.features.sales import (
    auto_lines,
    autosend,
    autosend_window,
    forecast_answers,
    mail,
    queue,
)
from backend.features.sales.autosend import Stop
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


# --- почему автоотправка стоит -------------------------------------------------------------------

JANE = "jane@acme.example.test"
OLGA = "olga@beta.example.test"
#: Подпись «Отправителя» после правки на экране.
RENAMED = "Mira Testova\nA renamed made-up agency"


@pytest.fixture
def switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тумблеры включены, предохранитель почты продаж снят строкой с пустым значением."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "")


async def _built(session: AsyncSession, world: w.World, *emails: str) -> list[MessageModel]:
    """Первые письма лидам — сборкой очереди, с подписью «Отправителя», как в бою."""
    for email in emails:
        await w.lead(session, world.hypothesis_id, email, name=email.split("@")[0].title())
    await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=len(emails)
    )
    rows = await session.scalars(
        select(MessageModel).where(MessageModel.step == FIRST_STEP).order_by(MessageModel.id)
    )
    return list(rows)


async def _unsettled(session: AsyncSession, letter: MessageModel) -> None:
    """Письмо «отправляется» дольше `STUCK_AFTER` — в блоке «Исход неизвестен»."""
    letter.status = MessageStatus.SENDING
    letter.updated_at = w.NOW - STUCK_AFTER
    await session.flush()


@pytest.mark.parametrize("toggle", ["ENABLED", "AUTOSEND"])
async def test_autosend_switched_off_is_no_stop(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    switched_on: None,
    toggle: str,
) -> None:
    """Выключена — письма уходят кнопкой, и прогноз считает их пачкой: остановки нет, даже когда
    включённая стояла бы (исход письма неизвестен)."""
    [letter] = await _built(session, world, JANE)
    await _unsettled(session, letter)
    on = await forecast_answers.autosend_stop(session, w.NOW)
    monkeypatch.setattr(sales_cfg, toggle, False)

    off = await forecast_answers.autosend_stop(session, w.NOW)

    assert on is not None
    assert off is None


async def test_the_guard_stops_autosend_in_the_words_of_its_line(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch, switched_on: None
) -> None:
    """Тумблер включён, а в списке разрешённых адресатов свои адреса — автоотправка не
    включается: письма в окне есть, а не уйдёт ни одно. Слова — те же, что у строки."""
    await _built(session, world, JANE)
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "me@ours.example.test")
    guarded = autosend.guard()

    found = await forecast_answers.autosend_stop(session, w.NOW)
    line = await auto_lines.autosend_line(session, w.NOW)

    assert guarded is not None
    assert found == f"автоотправка не включается: {guarded}"
    assert line.line == f"Автоотправка не включается: {guarded}."


async def test_an_unknown_outcome_stops_autosend_in_the_words_of_the_pass(
    session: AsyncSession, world: w.World, switched_on: None
) -> None:
    """Письмо «отправляется» дольше `STUCK_AFTER` — проход слать вслепую не будет, пока исход не
    решат: стоит, хоть в окне и есть второе письмо. Слова — те же, что после «Стоит:» в строке."""
    letters = await _built(session, world, JANE, OLGA)
    await _unsettled(session, letters[0])

    found = await forecast_answers.autosend_stop(session, w.NOW)
    line = await auto_lines.autosend_line(session, w.NOW)

    assert found is not None
    assert found.startswith(f"исход письма №{letters[0].id} неизвестен")
    assert line.line.endswith(f"Стоит: {found}.")


@pytest.mark.parametrize(
    ("autobuild_on", "advice"),
    [(True, auto_lines.BY_AUTOBUILD), (False, auto_lines.BY_BUTTON)],
)
async def test_letters_to_rebuild_stop_autosend_until_the_queue_is_built_again(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    switched_on: None,
    autobuild_on: bool,
    advice: str,
) -> None:
    """Подпись «Отправителя» поправили — ни одно письмо окна не уйдёт как есть: стоит, словами
    строки и с тем же советом. Очередь собрали заново — снова идёт, остановки нет."""
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", autobuild_on)
    await _built(session, world, JANE, OLGA)
    going = await forecast_answers.autosend_stop(session, w.NOW)
    await w.settings(session, signature=RENAMED)

    stands = await forecast_answers.autosend_stop(session, w.NOW)
    line = await auto_lines.autosend_line(session, w.NOW)
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=2)
    again = await forecast_answers.autosend_stop(session, w.NOW)

    assert going is None
    assert stands == f"{auto_lines.STALE}: {advice}"
    assert line.line.endswith(f"Стоит: {stands}.")
    assert again is None


async def test_an_empty_queue_and_a_queue_outside_the_window_are_no_stop(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch, switched_on: None
) -> None:
    """Очередь пуста, потом у её единственного получателя ночь — проход ждёт по делу, и прогноз
    считает это сам (запас сборки, окно в поясе очереди): остановки нет, хоть строка и «Стоит»."""
    monkeypatch.setattr(sales_cfg, "SEND_OPENS", time(9))
    monkeypatch.setattr(sales_cfg, "SEND_CLOSES", time(17))
    empty = await forecast_answers.autosend_stop(session, w.NOW)
    empty_seen = (await autosend.facts(session, w.NOW)).stop()
    await w.lead(session, world.hypothesis_id, OLGA, name="Olga", timezone="America/Los_Angeles")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=1)

    outside = await forecast_answers.autosend_stop(session, w.NOW)
    outside_seen = (await autosend.facts(session, w.NOW)).stop()

    assert (empty, outside) == (None, None)
    assert empty_seen is not None
    assert outside_seen is not None
    assert (empty_seen[0], outside_seen[0]) == (Stop.EMPTY, Stop.OUTSIDE)
