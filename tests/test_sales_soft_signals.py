"""Мягкие сигналы ящика продаж (Ф4, срез 4.5b): модуль продаж подставной.

`deferred` и мягкий отказ — строки журнала здоровья, три за сутки — лимит ящика снижен
вдвое; пауза — по окну последних 50 писем (отказы от 5%, жалобы от порога). У доноров
политики нет: следов в журнале нет, парковка — прежним правилом за всю жизнь ящика.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderHealthModel,
    SenderModel,
)
from backend.features.core.stages import MailPolicy
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.outreach import health, limits
from backend.features.outreach.health import SoftSignals
from sqlalchemy import Connection, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_model import ROOT, _migration
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import sender

NOW = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def ruled(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Модуль продаж подключён: мягкие сигналы и сторож — порогами по умолчанию."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(soft=SoftSignals(), watch=True))
    stages.register_sales(lambda: found)
    return found


async def _box(session: AsyncSession, stage: Stage, total: int, bounced: int = 0) -> SenderModel:
    """Ящик этапа и его `total` писем, первые `bounced` (самые старые) — отказ."""
    box = await sender(session, f"box@mail-{stage.value}.example.test", stage)
    domain = DomainModel(host=f"to-{stage.value}.example.test")
    campaign = CampaignModel(stage=stage, name="Мягкие сигналы", status="running")
    session.add_all([domain, campaign])
    await session.flush()
    for n in range(total):
        bounce = n >= total - bounced
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                domain_id=domain.id,
                sender_id=box.id,
                step=0,
                status=MessageStatus.BOUNCED if bounce else MessageStatus.DELIVERED,
                sent_at=NOW - timedelta(hours=n + 1),
                idempotency_key=f"{stage}:soft-{n}:0",
            )
        )
    await session.flush()
    return box


async def _events(
    session: AsyncSession, box: SenderModel, *kinds: str, soft: bool = False
) -> list[str]:
    """События по последнему письму ящика; ответ — адреса ящиков, вставших на паузу."""
    letter = await session.scalar(
        select(MessageModel.id)
        .where(MessageModel.sender_id == box.id)
        .order_by(MessageModel.sent_at.desc())
    )
    at = NOW - timedelta(hours=1)  # время платформы: сутки сигнала — от него, а не от прихода
    found = [DeliveryEvent(kind, letter, "x@to.example.test", soft=soft, at=at) for kind in kinds]
    return (await apply_events(session, found, now=NOW)).paused_domains


async def _journal(session: AsyncSession, box: SenderModel) -> list[str]:
    rows = await session.scalars(
        select(SenderHealthModel.kind)
        .where(SenderHealthModel.sender_id == box.id)
        .order_by(SenderHealthModel.id)
    )
    return list(rows)


async def test_a2_three_soft_signals_cut_the_box_for_a_day(session: AsyncSession) -> None:
    box = await _box(session, Stage.SALES, total=1)

    await _events(session, box, "deferred", "deferred")
    two = await health.cuts(session, Stage.SALES, NOW)
    await _events(session, box, "bounce", soft=True)

    assert two == {}
    assert await _journal(session, box) == ["deferred", "deferred", "blocked", "limit_cut"]
    cut = await health.cuts(session, Stage.SALES, NOW)
    assert cut == {box.id: 10}
    assert await health.cuts(session, Stage.SALES, NOW + timedelta(hours=23, minutes=1)) == {}
    screened = limits.screen(
        [box],
        stage=Stage.SALES,
        sent_today={box.id: 10},
        domains={},
        direction_limit=None,
        now=NOW,
        cuts=cut,
    )
    assert screened.why() == f"{limits.BOX_CUT}: {box.email} — 10 из 10"
    assert box.enabled


async def test_donors_soft_signals_leave_no_trace(session: AsyncSession) -> None:
    box = await _box(session, Stage.DONORS, total=1)

    await _events(session, box, "deferred", "deferred", "deferred")

    assert await _journal(session, box) == []
    assert await health.cuts(session, Stage.DONORS, NOW) == {}


@pytest.mark.parametrize(
    ("stage", "broken", "paused"),
    [(Stage.SALES, None, True), (Stage.DONORS, None, False), (Stage.SALES, "policy", False)],
)
async def test_three_bounces_in_twenty_pause_sales_by_the_window_and_not_donors(
    session: AsyncSession, ruled: FakeSalesMail, stage: Stage, broken: str | None, paused: bool
) -> None:
    """У доноров доля отказов судится после 50 писем за всю жизнь ящика — 3 из 20 ждут; так же
    у продаж, пока модуль не ответил о политике: событие записано, вебхук не упал."""
    ruled.broken = broken
    box = await _box(session, stage, total=20, bounced=2)

    said = await _events(session, box, "bounce")

    assert (said, box.enabled) == (([box.email], False) if paused else ([], True))
    assert await _journal(session, box) == (["paused"] if paused else [])
    if paused:
        assert box.pause_reason == "отказов 3 в окне 50 писем — порог 5.0%: пауза"


async def test_bounces_older_than_the_window_do_not_pause(session: AsyncSession) -> None:
    box = await _box(session, Stage.SALES, total=60, bounced=0)
    for old in (
        await session.scalars(select(MessageModel).order_by(MessageModel.sent_at).limit(5))
    ).all():
        old.status = MessageStatus.BOUNCED

    assert await _events(session, box, "bounce") == []
    assert box.enabled


async def test_one_complaint_in_the_window_pauses_the_sales_box(session: AsyncSession) -> None:
    box = await _box(session, Stage.SALES, total=30)

    said = await _events(session, box, "spamreport")

    assert said == [box.email]
    assert box.pause_reason == "жалоб 1 в окне 50 писем — порог 0.1%: пауза"
    assert await _journal(session, box) == ["complaint", "paused"]


def _journal_cycle(connection: Connection) -> tuple[object, object]:
    """Ревизия журнала ещё раз, в процессе: подъём сьюта идёт подпроцессом, покрытие его не видит."""
    migration = _migration(ROOT / "backend/migrations/versions/3f9663d69a56_sender_health.py")
    there = text("SELECT to_regclass('sender_health') IS NOT NULL")
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        gone = connection.scalar(there)
        migration.upgrade()
    return gone, connection.scalar(there)


async def test_the_journal_revision_goes_down_and_up(session: AsyncSession) -> None:
    assert await (await session.connection()).run_sync(_journal_cycle) == (False, True)
