"""Ревью стыков (E1): стоп-листы продаж и общий — после очистки, на сборке и на отправке.

Очистка закрывает лида до первого письма (`test_sales_cleaning.py`), но стоп-лист пополняют и
после неё: человек отписался в другом направлении, домен внесли руками, клиента — в стоп-лист
продаж. Тогда держат сборка (лид ждёт с причиной, модель не зовётся) и отправка (письмо не
уходит). До ревью эти правила `mail.stopped_by` на сборке и отправке держала только часть
тестов: общая проверка отправки (`sending._check_suppression`) маскировала строки без этапа и
этапа продаж, а домен самого адреса лида не проверял никто — мутанты выживали.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from datetime import timedelta

import pytest
from backend.features.core.domain import MessageStatus, Stage, SuppressionReason
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import followups
from backend.features.letters.sending import Sending, SuppressedError
from backend.features.sales import queue
from backend.features.sales.models import SalesLeadModel, SalesStoplistModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests import test_sales_stage_bridge as bridge
from tests.test_sales_send import _seen, _transports
from tests.test_sales_stage_mail import NOW, _donor_chain

#: Лид пишет с личного домена, а его компания — другой сайт: домен адреса — свой.
EMAIL = "ivan@gamma.example.test"
COMPANY = "acme.example.test"
#: Донор соседней цепочки прохода (`test_sales_stage_mail._donor_chain`).
DONOR_HOST = "donor-b.example.test"
DONOR_EMAIL = f"editor@{DONOR_HOST}"

Row = Callable[[SalesLeadModel], object]

#: Что внесли в стоп-лист после очистки — и держит ли это продажи.
AFTER_CLEANING: dict[str, tuple[Row, bool]] = {
    "общий без этапа, домен компании": (
        lambda lead: SuppressionModel(domain_id=lead.domain_id, reason=SuppressionReason.MANUAL),
        True,
    ),
    "общий этапа продаж, адрес": (
        lambda lead: SuppressionModel(
            email=lead.email, reason=SuppressionReason.MANUAL, stage=Stage.SALES
        ),
        True,
    ),
    "отписка у доноров, адрес": (
        lambda lead: SuppressionModel(
            email=lead.email, reason=SuppressionReason.UNSUBSCRIBED, stage=Stage.DONORS
        ),
        True,
    ),
    "стоп-лист продаж, домен адреса": (
        lambda _lead: SalesStoplistModel(host="gamma.example.test", created_by="тест"),
        True,
    ),
    "общий доноров, ручной — чужое правило": (
        lambda lead: SuppressionModel(
            email=lead.email, reason=SuppressionReason.MANUAL, stage=Stage.DONORS
        ),
        False,
    ),
}


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.mark.parametrize("what", sorted(AFTER_CLEANING))
async def test_assembly_holds_a_lead_stopped_after_cleaning(
    session: AsyncSession, world: w.World, what: str
) -> None:
    """Лид прошёл очистку, затем его закрыли: сборка не зовёт модель и называет причину;
    правило чужого направления (ручная строка доноров) лида не держит — письмо собрано."""
    row, stops = AFTER_CLEANING[what]
    lead = await w.lead(session, world.hypothesis_id, EMAIL, host=COMPANY)
    session.add(row(lead))
    await session.flush()

    rewriter = w.NoRewrite() if stops else w.CorridorRewriter()
    report = await queue.build(session, rewriter, hypothesis_id=world.hypothesis_id, limit=10)

    if stops:
        assert (report.prepared, report.waiting) == (0, Counter({queue.STOPLIST: 1}))
    else:
        assert (report.prepared, report.waiting) == (1, Counter())


STOPS = sorted(name for name, (_, stops) in AFTER_CLEANING.items() if stops)


@pytest.mark.parametrize("what", STOPS)
async def test_sending_holds_a_letter_whose_lead_was_stopped_after_assembly(
    session: AsyncSession, world: w.World, what: str
) -> None:
    """Письмо собрано, затем лида закрыли: отправка отказывает словами стоп-листа (общего —
    проверкой почты, продаж и отписки другого направления — проверкой модуля), письмо в
    очереди, ничего не ушло."""
    row, _ = AFTER_CLEANING[what]
    lead = await w.lead(session, world.hypothesis_id, EMAIL, host=COMPANY)
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=10)
    letter = await session.scalar(select(MessageModel))
    assert letter is not None
    session.add(row(lead))
    await session.flush()
    source = _transports()

    with pytest.raises(SuppressedError):
        await Sending(session, source, now=w.NOW).send(letter.id)

    assert _seen(source) == []
    await session.refresh(letter)
    assert letter.status is MessageStatus.QUEUED


async def test_sales_stop_rows_do_not_hold_the_donor_chain(
    session: AsyncSession, filled_legal: None
) -> None:
    """«Отписка доноров не тронута» и обратно: строка общего стоп-листа этапа продаж и стоп-лист
    продаж на адрес и домен донора — правила продаж; добивка донору уходит (мутант «строка
    любого этапа держит» в общем `sending._check_suppression` убит)."""
    donor = await _donor_chain(session, due=NOW - timedelta(days=1))
    session.add_all(
        [
            SuppressionModel(email=DONOR_EMAIL, reason=SuppressionReason.MANUAL, stage=Stage.SALES),
            SalesStoplistModel(host=DONOR_HOST, created_by="тест"),
        ]
    )
    await session.flush()

    report = await followups.send_due(session, transport=bridge._transports(), limit=5, now=NOW)

    assert (report.sent, report.stopped) == (1, 0)
    await session.refresh(donor)
    assert donor.next_action_at is None
