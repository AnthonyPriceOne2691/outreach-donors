"""Отписка и жалоба на письмо продаж закрывают человека, а не сайт (решение владельца 12.10.2026).

Письмо продаж адресовано человеку. Кнопка отписки на нём (и One-Click — тот же `POST`) ставит
в стоп-лист адрес этого человека без этапа, его переписку продаж — в «отписался», а домен
в стоп-лист не встаёт: переписки и письма доноров и рекламодателей на сайте живут. Жалоба —
то же и ещё запись на домен с этапом продаж: больше никому в компании продажи не пишут.
Письма доноров и рекламодателей — как раньше: сайт целиком. Модуль продаж здесь подставной:
адрес лида почта узнаёт у моста.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from backend.api.unsubscribe import routes as unsubscribe_routes
from backend.config import outreach as outreach_cfg
from backend.features.core import stages
from backend.features.core.domain import (
    ContactSource,
    MessageStatus,
    Stage,
    SuppressionReason,
    ThreadStatus,
)
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.core.stages import SalesNotConnectedError
from backend.features.letters import optout, unsubscribe
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.letters.unsubscribe import Label
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_delivery_events_foreign import LeadMail
from tests.test_sales_stage_bridge import LEAD_EMAIL, FakeSalesMail
from tests.test_sales_stage_mail import LEAD, World, sales_world

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
SECRET = "u" * 32
CLIENT = "127.0.0.1"
BY = "проверка"
CEO = f"ceo@{LEAD}"

Row = tuple[int | None, str | None, Stage | None, SuppressionReason]


@dataclass(frozen=True, slots=True)
class Site:
    """Сайт — и лид продаж, и донор: письмо продаж лиду ушло; донору ушло письмо, торг о
    цене идёт (срок добивки цел); ещё одно письмо донору — на другой адрес — в очереди."""

    world: World
    talk: MessageModel
    queued: MessageModel


@pytest.fixture(autouse=True)
def page_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    # Счётчик частоты — в памяти процесса и общий на все тесты (`tests/test_api_unsubscribe.py`).
    unsubscribe_routes._throttle.clear(CLIENT)


@pytest.fixture
async def site(session: AsyncSession) -> Site:
    world = await sales_world(session, status=MessageStatus.SENT)
    domain_id = world.letter.domain_id
    campaign = CampaignModel(stage=Stage.DONORS, name="Доноры", status="running")
    info = ContactModel(domain_id=domain_id, email=f"info@{LEAD}", source=ContactSource.PAGE)
    session.add_all([campaign, info])
    await session.flush()
    talking = ThreadModel(
        domain_id=domain_id,
        campaign_id=campaign.id,
        contact_id=world.letter.contact_id,
        status=ThreadStatus.REPLIED,
    )
    waiting = ThreadModel(domain_id=domain_id, campaign_id=campaign.id, contact_id=info.id)
    session.add_all([talking, waiting])
    await session.flush()
    talk = MessageModel(
        campaign_id=campaign.id,
        thread_id=talking.id,
        domain_id=domain_id,
        contact_id=world.letter.contact_id,
        step=0,
        status=MessageStatus.SENT,
        subject="Guest article",
        body="Hi",
        sent_at=NOW - timedelta(days=5),
        next_action_at=NOW + timedelta(days=2),
        idempotency_key=f"donors:{LEAD}:talk",
    )
    queued = MessageModel(
        campaign_id=campaign.id,
        thread_id=waiting.id,
        domain_id=domain_id,
        contact_id=info.id,
        step=0,
        status=MessageStatus.QUEUED,
        subject="Guest article",
        body="Hi",
        idempotency_key=f"donors:{LEAD}:queued",
    )
    session.add_all([talk, queued])
    await session.flush()
    return Site(world=world, talk=talk, queued=queued)


def _register(monkeypatch: pytest.MonkeyPatch, found: FakeSalesMail) -> FakeSalesMail:
    monkeypatch.setattr(stages._SALES, "load", None)
    stages.register_sales(lambda: found)
    return found


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, site: Site) -> FakeSalesMail:
    """Модуль продаж подключён: письмо продаж ушло лиду `LEAD_EMAIL`, его переписка — сайта."""
    return _register(monkeypatch, FakeSalesMail(threads=(site.world.thread.id,)))


async def _rows(session: AsyncSession) -> set[Row]:
    await session.flush()
    found = await session.scalars(select(SuppressionModel))
    return {(row.domain_id, row.email, row.stage, row.reason) for row in found.all()}


async def _donors_alive(session: AsyncSession, site: Site) -> None:
    """Переписки и письма доноров сайта не тронуты: торг идёт, письмо в очереди ждёт."""
    await session.flush()
    for letter in (site.talk, site.queued):
        await session.refresh(letter)
    assert site.talk.next_action_at == NOW + timedelta(days=2)
    assert site.queued.status is MessageStatus.QUEUED
    statuses = await session.scalars(
        select(ThreadModel.status).where(
            ThreadModel.id.in_([site.talk.thread_id, site.queued.thread_id])
        )
    )
    assert sorted(statuses.all()) == sorted([ThreadStatus.REPLIED, ThreadStatus.OPEN])


async def _sales_thread(session: AsyncSession, site: Site) -> ThreadStatus:
    await session.flush()
    await session.refresh(site.world.thread)
    return site.world.thread.status


def _link(letter: MessageModel) -> str:
    label = unsubscribe.label_for(letter.domain_id, letter_id=letter.id, secret=SECRET)
    return f"/api/unsubscribe/{label}"


class TestSalesLetter:
    async def test_the_button_closes_the_person_and_leaves_the_site(
        self, client: AsyncClient, session: AsyncSession, site: Site, fake: FakeSalesMail
    ) -> None:
        """Нажатие (и One-Click — тот же `POST`): адрес лида без этапа, домена в стоп-листе нет."""
        await session.commit()

        response = await client.post(_link(site.world.letter))

        assert response.status_code == 200
        assert "We will not email you again." in response.text
        assert await _rows(session) == {(None, LEAD_EMAIL, None, SuppressionReason.UNSUBSCRIBED)}
        assert await _sales_thread(session, site) is ThreadStatus.UNSUBSCRIBED
        await _donors_alive(session, site)
        assert f"recipient {site.world.letter.id}" in fake.asked

    async def test_pressing_twice_adds_nothing(
        self, session: AsyncSession, site: Site, fake: FakeSalesMail
    ) -> None:
        label = Label(site.world.letter.domain_id, site.world.letter.id)

        first = await optout.unsubscribe_label(session, label, source=BY)
        again = await optout.unsubscribe_label(session, label, source=BY)

        assert first is not None
        assert again is not None
        assert (first.first_time, again.first_time, again.host) == (True, False, LEAD)
        assert len(await _rows(session)) == 1

    async def test_a_complaint_closes_the_person_and_the_company_for_sales(
        self, session: AsyncSession, site: Site, fake: FakeSalesMail
    ) -> None:
        report = await apply_events(
            session, [DeliveryEvent("spamreport", site.world.letter.id, LEAD_EMAIL)], now=NOW
        )

        assert report.complained == 1
        assert await _rows(session) == {
            (None, LEAD_EMAIL, None, SuppressionReason.COMPLAINED),
            (site.world.letter.domain_id, None, Stage.SALES, SuppressionReason.COMPLAINED),
        }
        assert await _sales_thread(session, site) is ThreadStatus.UNSUBSCRIBED
        await _donors_alive(session, site)

    async def test_without_the_bridge_the_lead_address_is_found_among_the_site_addresses(
        self, session: AsyncSession, site: Site, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Мост не назвал адресата (продажи не подключены), но адрес лида — один из адресов
        сайта (так у лида сбора): переписку письма называет модуль продаж по адресу."""
        refused = SalesNotConnectedError("Отписка", "нет выдуманной учётки")
        _register(monkeypatch, LeadMail(threads=(site.world.thread.id,), odd=refused))
        session.add(
            ContactModel(
                domain_id=site.world.letter.domain_id, email=LEAD_EMAIL, source=ContactSource.PAGE
            )
        )

        label = Label(site.world.letter.domain_id, site.world.letter.id)
        await optout.unsubscribe_label(session, label, source=BY)

        assert await _rows(session) == {(None, LEAD_EMAIL, None, SuppressionReason.UNSUBSCRIBED)}
        await _donors_alive(session, site)

    async def test_an_unknown_person_closes_the_company_for_sales_only(
        self,
        session: AsyncSession,
        site: Site,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Адреса не узнать вовсе — его нет среди адресов сайта, и доноры до него не дойдут:
        компания закрыта для продаж, доноры и рекламодатели живут, журнал говорит громко."""
        refused = SalesNotConnectedError("Отписка", "нет выдуманной учётки")
        _register(monkeypatch, FakeSalesMail(threads=(), odd=refused))

        label = Label(site.world.letter.domain_id, site.world.letter.id)
        with caplog.at_level(logging.WARNING, logger=optout.__name__):
            await optout.unsubscribe_label(session, label, source=BY)

        assert await _rows(session) == {
            (site.world.letter.domain_id, None, Stage.SALES, SuppressionReason.UNSUBSCRIBED)
        }
        assert await _sales_thread(session, site) is ThreadStatus.UNSUBSCRIBED
        await _donors_alive(session, site)
        assert "адрес человека не узнать" in caplog.text


class TestDonorLetterAsBefore:
    @pytest.mark.parametrize("with_letter", [False, True], ids=["метка донора", "метка письма"])
    async def test_the_button_closes_the_whole_site(
        self, session: AsyncSession, site: Site, with_letter: bool
    ) -> None:
        label = Label(site.talk.domain_id, site.talk.id if with_letter else None)

        outcome = await optout.unsubscribe_label(session, label, source=BY)

        assert outcome is not None
        assert outcome.stopped == 2
        assert await _rows(session) == {
            (site.talk.domain_id, None, None, SuppressionReason.UNSUBSCRIBED)
        }
        await session.refresh(site.queued)
        assert site.queued.status is MessageStatus.STOPPED

    async def test_a_complaint_closes_the_whole_site(
        self, session: AsyncSession, site: Site
    ) -> None:
        report = await apply_events(
            session, [DeliveryEvent("spamreport", site.talk.id, CEO)], now=NOW
        )

        assert report.complained == 1
        assert await _rows(session) == {
            (site.talk.domain_id, None, None, SuppressionReason.COMPLAINED)
        }
