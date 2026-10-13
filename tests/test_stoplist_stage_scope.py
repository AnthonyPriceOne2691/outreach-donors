"""Запись стоп-листа с этапом снимает письма только своего этапа (ревью стыков 12.10.2026).

Проверка перед отправкой держит запись этапа только у писем этого этапа (`stoplist.holding`),
а снятие с очереди снимало письма всех этапов: запись продаж на домен останавливала и
переписку доноров с торгом о цене. Запись без этапа — как прежде, все этапы.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage, SuppressionReason
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import stoplist
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.thread_letters import first_letter

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
HOST = "scope.example.test"
EDITOR = f"editor@{HOST}"
BY = "проверка"


@dataclass(frozen=True, slots=True)
class Site:
    """Сайт в трёх ролях: первое письмо донору в очереди, рекламодателю — ушло, срок
    добивки цел (торг идёт), письмо продаж — в очереди."""

    donor: MessageModel
    offer: MessageModel
    sales: MessageModel


async def _letter(
    session: AsyncSession, donor: MessageModel, stage: Stage, **values: object
) -> MessageModel:
    campaign = CampaignModel(stage=stage, name=f"Роль {stage.value}", status="running")
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=donor.domain_id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=donor.domain_id,
        step=0,
        subject="Hello",
        body="Hello",
        idempotency_key=f"{stage.value}:{HOST}:0",
        **values,
    )
    session.add(letter)
    await session.flush()
    return letter


@pytest.fixture
async def site(session: AsyncSession) -> Site:
    donor = await first_letter(session, host=HOST)
    offer = await _letter(
        session,
        donor,
        Stage.ADVERTISERS,
        contact_id=donor.contact_id,
        status=MessageStatus.SENT,
        sent_at=NOW - timedelta(days=3),
        next_action_at=NOW + timedelta(days=4),
    )
    sales = await _letter(session, donor, Stage.SALES, status=MessageStatus.QUEUED)
    return Site(donor=donor, offer=offer, sales=sales)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, site: Site) -> FakeSalesMail:
    """Модуль продаж называет переписку письма продаж переписками адреса редактора."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(threads=(site.sales.thread_id or 0,))
    stages.register_sales(lambda: found)
    return found


async def _states(session: AsyncSession, site: Site) -> dict[str, tuple[MessageStatus, bool]]:
    """Состояние писем сайта в базе: статус и цел ли срок добивки. Записанное стоп-листом
    сначала уходит в базу — маршрут его фиксирует, а `refresh` затёр бы несброшенное."""
    await session.flush()
    found = {}
    for name, letter in (("donor", site.donor), ("offer", site.offer), ("sales", site.sales)):
        await session.refresh(letter)
        found[name] = (letter.status, letter.next_action_at is not None)
    return found


async def test_a_sales_record_on_the_site_stops_only_sales_letters(
    session: AsyncSession, site: Site
) -> None:
    added = await stoplist.add(
        session, HOST, reason=SuppressionReason.MANUAL, stage=Stage.SALES, author=BY
    )

    assert added.stopped == 1
    assert await _states(session, site) == {
        "donor": (MessageStatus.QUEUED, False),
        "offer": (MessageStatus.SENT, True),
        "sales": (MessageStatus.STOPPED, False),
    }


async def test_a_donor_record_on_an_address_leaves_the_offer_and_sales_letters(
    session: AsyncSession, site: Site, fake: FakeSalesMail
) -> None:
    """Адрес редактора — у письма донору и у оффера, а модуль продаж называет переписку
    продаж того же адреса: запись этапа доноров снимает только письмо донору."""
    added = await stoplist.add(
        session, EDITOR, reason=SuppressionReason.MANUAL, stage=Stage.DONORS, author=BY
    )

    assert added.stopped == 1
    assert fake.asked == [f"threads_to {EDITOR}"]
    assert await _states(session, site) == {
        "donor": (MessageStatus.STOPPED, False),
        "offer": (MessageStatus.SENT, True),
        "sales": (MessageStatus.QUEUED, False),
    }


async def test_a_record_without_a_stage_stops_every_stage_as_before(
    session: AsyncSession, site: Site
) -> None:
    added = await stoplist.add(session, HOST, reason=SuppressionReason.MANUAL, author=BY)

    assert added.stopped == 3
    assert await _states(session, site) == {
        "donor": (MessageStatus.STOPPED, False),
        "offer": (MessageStatus.SENT, False),
        "sales": (MessageStatus.STOPPED, False),
    }
