"""Ревью стыков (E2): чистка (`prune --test-traces`, `prune --probes`) и следы продаж.

Передача лида (`sales_handoffs`) уходит каскадом с перепиской, а в ней — номер сделки в Kommo:
потеряй строку — пробную сделку в Kommo не найти. Оба реестра чистки разбирают каскад как
«уходит» (`test_prune.py`, `test_prune_test_traces.py`). На деле сейчас:

- `--test-traces` пробную переписку продаж не видит вовсе: свои ящики узнаются по адресам
  `contacts`, а сборка продаж заводит переписку и письма без адреса (`contact_id` пуст),
  адрес лида живёт в `sales_leads`. Следы пробы продаж остаются, номер сделки — с ними.
  Брать ли их и что тогда с пробной сделкой в Kommo — решение владельца до пробы 5.5;
  тест ниже закрепляет нынешнее, чтобы расширение чистки не унесло номер молча;
- `--probes` берёт в план липовый домен, который держит лид продаж (`RESTRICT`), и падает
  на удалении — чистка липовых доноров встаёт целиком (общий код, `xfail`).
"""

from __future__ import annotations

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.models.outreach import MessageModel, ThreadModel
from backend.features.letters.sending import Sending
from backend.features.runs.prune import apply_prune, plan_prune
from backend.features.sales.models import SalesHandoffModel, SalesLeadModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_send import _queued, _transports

#: Свой ящик из предохранителя — адрес пробного лида; номер пробной сделки — выдуманный.
MINE = "checker@ours.example.test"
DEAL = 7351


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    found = await w.world(session, monkeypatch)
    monkeypatch.setattr(outreach_cfg, "ALLOWED_RECIPIENTS", (MINE,))
    return found


async def _trial(
    session: AsyncSession, world: w.World, email: str, *, send: bool = True
) -> SalesHandoffModel:
    """Пробный лид: первое письмо собрано (и ушло), лид захотел говорить — сделка в Kommo."""
    [letter] = await _queued(session, world, email)
    if send:
        await Sending(session, _transports(), now=w.NOW).send(letter.id)
    lead = await session.scalar(select(SalesLeadModel).where(SalesLeadModel.email == email))
    assert lead is not None
    assert letter.thread_id is not None
    handoff = SalesHandoffModel(thread_id=letter.thread_id, lead_id=lead.id, kommo_lead_id=DEAL)
    session.add(handoff)
    await session.flush()
    return handoff


async def _left(session: AsyncSession) -> tuple[int, int, list[int | None]]:
    threads = await session.scalar(select(func.count()).select_from(ThreadModel))
    letters = await session.scalar(select(func.count()).select_from(MessageModel))
    deals = list(await session.scalars(select(SalesHandoffModel.kommo_lead_id)))
    return int(threads or 0), int(letters or 0), deals


async def test_trace_cleanup_does_not_see_a_sales_trial_and_its_kommo_deal_stays(
    session: AsyncSession, world: w.World
) -> None:
    """Решение владельца до 5.5 — закреплено нынешнее: проба продаж на свой ящик чисткой следов
    не берётся (план пуст), переписка, письмо и передача с номером сделки остаются."""
    await _trial(session, world, MINE)

    plan = await plan_prune(session, run_ids=[], test_traces=True)
    await apply_prune(session, plan, author="тест")

    assert plan.test_traces is not None
    assert (plan.test_traces.threads, plan.test_traces.letters) == ([], [])
    assert await _left(session) == (1, 1, [DEAL])


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код donors/probe.probe_trace (и правило чистки runs/prune.py): липовый домен, "
        "который держит лид продаж (`sales_leads.domain_id` RESTRICT), идёт в план и роняет "
        "удаление IntegrityError — чистка липовых доноров встаёт целиком; правило — у соседней "
        "сессии через координатора (ревью стыков R1, E2)"
    ),
)
async def test_probe_cleanup_keeps_a_fake_domain_held_by_a_sales_lead(
    session: AsyncSession, world: w.World
) -> None:
    """Лид продаж на липовом домене (зона `.invalid`) держит домен, как у основной чистки
    («держит: лид продаж»): домен не в плане, чистка идёт, передача с номером сделки цела."""
    await _trial(session, world, "jane@acme.invalid", send=False)

    plan = await plan_prune(session, run_ids=[], probes=True)
    await apply_prune(session, plan, author="тест")

    assert plan.probes is not None
    assert plan.probes.domains == []
    assert await _left(session) == (1, 1, [DEAL])
