"""Кому сборка очереди продаж напишет первое письмо — одно решение у сборки и у счёта
(`sales/queue_plan.py`).

Счёт «сколько первых писем сборка ещё напишет» (прогноз рассылки) обязан сказать то же, что
сборка говорит о каждом лиде (`queue._plan`), — на смешанной партии: писать можно; передан
телемаркетологу; стоп-лист продаж адресом и доменом, общий стоп-лист; «кому не пишем»; адрес держит
очистка; сайт в работе у доноров; язык без цепочки; своя цепочка гипотезы задана не целиком; нет
значения подстановки; ключ письма длиннее колонки. И счёт — партией: запросов к базе не больше, чем
у партии втрое меньше. Мир — подключённые продажи (`tests/test_sales_send_world.py`); адреса,
домены и тексты выдуманные.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from backend.features.core import stage_forecast
from backend.features.core.domain import MessageStatus, Stage, SuppressionReason
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.sales import (
    chain,
    chain_text,
    elsewhere,
    forecast_answers,
    hypotheses,
    queue,
    queue_plan,
)
from backend.features.sales.models import SalesHandoffModel, SalesStoplistModel
from backend.features.sales.unconfirmed import FIRST_LETTER
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w

#: Сайт длиннее, чем помещается в ключ письма вместе с адресом (`letter.KEY_LENGTH`).
LONG_HOST = ".".join(["made-up-long-label"] * 8) + ".example.test"


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def _campaign(session: AsyncSession, stage: Stage) -> CampaignModel:
    campaign = CampaignModel(
        stage=stage, name=f"Выдуманная рассылка {stage.value}", status="running"
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def _mixed(session: AsyncSession, world: w.World) -> int:
    """Смешанная партия лидов двух гипотез; номер второй — у неё своя цепочка на английском, и
    она задана не целиком (один шаг из трёх)."""
    own = (await hypotheses.add(session, "Вторая выдуманная гипотеза", None)).id
    first_step = chain_text.step_template(
        step=1, language="en", subject=w.SUBJECT, body=w.FIRST_BODY
    )
    await chain.save(session, first_step, hypothesis_id=own, author="тест", author_id=None)

    one = world.hypothesis_id
    await w.lead(session, one, "jane@acme.example.test")
    await w.lead(session, one, "olga@beta.example.test", language="ru", name="Ольга")
    await w.lead(session, one, "jonas@fahrrad.example.test", language="de")
    await w.lead(session, one, "noname@three.example.test", name=None)
    await w.lead(session, one, "info@bureau.gov.zz")
    await w.lead(session, one, "held@delta.example.test", verification_status="made-up:unknown")
    await w.lead(session, one, f"someone.with.a.long.address@{LONG_HOST}")
    handed = await w.lead(session, one, "handed@one.example.test")
    await w.lead(session, one, "stop@two.example.test")
    await w.lead(session, one, "anyone@blocked-host.example.test")
    unsubscribed = await w.lead(session, one, "unsub@gamma.example.test")
    busy = await w.lead(session, one, "owner@busy-site.example.test")
    await w.lead(session, own, "kim@epsilon.example.test")
    await w.lead(session, own, "ivan@zeta.example.test", language="ru", name="Иван")

    sales = await _campaign(session, Stage.SALES)
    thread = ThreadModel(domain_id=handed.domain_id, campaign_id=sales.id)
    donors = await _campaign(session, Stage.DONORS)
    session.add(thread)
    await session.flush()
    session.add_all(
        [
            SalesHandoffModel(thread_id=thread.id, lead_id=handed.id),
            SalesStoplistModel(email="stop@two.example.test"),
            SalesStoplistModel(host="blocked-host.example.test"),
            SuppressionModel(email=unsubscribed.email, reason=SuppressionReason.UNSUBSCRIBED),
            # Сайт лида в работе у доноров: их письмо в очереди — одна роль на сайт.
            MessageModel(
                campaign_id=donors.id,
                domain_id=busy.domain_id,
                step=0,
                status=MessageStatus.QUEUED,
                idempotency_key="donors:busy-site.example.test:0",
            ),
        ]
    )
    await session.flush()
    return own


async def _builder(session: AsyncSession, hypothesis_id: int) -> list[queue_plan.Plan | str]:
    """Что сборка скажет о каждом лиде гипотезы, которого она взяла бы (`queue._leads`)."""
    rows = await queue._leads(session, hypothesis_id, 0)
    return [await queue._plan(session, row, hypothesis_id) for row in rows]


def _written(found: list[queue_plan.Plan | str]) -> int:
    return sum(isinstance(item, queue_plan.Plan) for item in found)


async def test_the_counter_and_the_builder_agree_lead_by_lead(
    session: AsyncSession, world: w.World
) -> None:
    own = await _mixed(session, world)

    first, second = await _builder(session, world.hypothesis_id), await _builder(session, own)
    counted = await queue_plan.writable_counts(session)

    assert counted == {world.hypothesis_id: _written(first), own: _written(second)}
    assert counted == {world.hypothesis_id: 2, own: 1}
    waiting = Counter(item for item in [*first, *second] if isinstance(item, str))
    assert waiting == Counter(
        {
            queue.HANDED_OFF: 1,
            queue.STOPLIST: 3,
            queue.LONG_KEY: 1,
            FIRST_LETTER: 1,
            "кому не пишем: гос. и учебным сайтам не пишем": 1,
            "у лида нет значения для {{name}}": 1,
            "язык лида — немецкий (de): цепочки на нём нет, есть ru, en": 1,
            "цепочка на английском задана не целиком": 1,
            elsewhere.waiting(Stage.DONORS): 1,
        }
    )


async def test_the_bridge_and_the_module_answer_the_sum_of_hypotheses(
    session: AsyncSession, world: w.World
) -> None:
    await _mixed(session, world)

    assert await forecast_answers.supply(session) == 3
    assert await stage_forecast.sales_supply(session) == 3


@contextmanager
def _statements(session: AsyncSession) -> Iterator[list[str]]:
    """Запросы к базе внутри блока — их текстом."""
    seen: list[str] = []
    engine = session.bind.sync_engine  # type: ignore[union-attr]

    def note(*args: Any) -> None:
        seen.append(str(args[2]))

    event.listen(engine, "before_cursor_execute", note)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", note)


async def _ready(session: AsyncSession, world: w.World, count: int, start: int) -> None:
    for number in range(start, start + count):
        await w.lead(session, world.hypothesis_id, f"lead{number}@site-{number}.example.test")


async def test_the_counter_reads_the_batch_not_each_lead(
    session: AsyncSession, world: w.World
) -> None:
    """Втрое больше лидов — столько же запросов: прогноз спрашивает счёт раз в минуту."""
    await _ready(session, world, 3, 0)
    await session.flush()
    with _statements(session) as few:
        assert await queue_plan.writable_counts(session) == {world.hypothesis_id: 3}

    await _ready(session, world, 6, 3)
    await session.flush()
    with _statements(session) as many:
        assert await queue_plan.writable_counts(session) == {world.hypothesis_id: 9}

    assert len(many) == len(few)
