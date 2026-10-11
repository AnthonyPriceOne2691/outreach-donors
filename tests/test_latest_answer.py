"""Последнее слово донора побеждает (решение Anthony 11.10.2026) — на настоящей базе.

«Не продаём» снимает цену донора, и с ней домен уходит из офферов Этапа 2: они стоят
на свежей цене донора (`letters/recipients._fresh_price`, `niche_recipients._example_host`).
«Бесплатно» ставит вместо цены отметку. Последнее — по тому, когда донор ответил, а не
когда мы ответ разобрали: у ответа — когда он пришёл, у цены руками — когда её вписали.

До 11.10.2026 ответ без цены цену не трогал: донор, сказавший «больше не продаём»,
оставался с прежней ценой в карточке, в выгрузке и в офферах Этапа 2. А «последним» было
последнее записанное: старый ответ с ценой, подтверждённый человеком позже, возвращал
«продаёт» поверх «не продаём».
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.features.core.domain import ContactSource, ReplyKind, RunStatus, Stage, UserRole
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel, ThreadModel
from backend.features.core.models.run import RunModel
from backend.features.crawl.niche import NICHE
from backend.features.donors.manual_price import DonorRefusedError, manual_price, price_donor
from backend.features.donors.verdict import Thresholds
from backend.features.letters.niche_recipients import NicheRecipients
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Parser
from backend.features.runs.repository import RunRepository
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.test_letters_advertisers import make_advertiser

DONOR = "donor.example.test"
BRAND = "brand.example.test"
BOOKIE = "bookie.example.test"
WHO = "anna@ours.example.test"
#: Когда донор отвечал: дни после письма. Офферы считаются «сейчас» — `NOW`.
DAY = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)

PRICE = Extracted(price_white=Decimal("150"), currency="USD", placement="sells", confidence=0.95)
UNSURE = Extracted(price_white=Decimal("250"), currency="USD", placement="sells", confidence=0.4)
DECLINES = Extracted(placement="declines", placement_quote="we do not sell", confidence=0.9)
FREE = Extracted(placement="free", placement_quote="for free", confidence=0.9)


class Said:
    """Модель разбора: отдаёт заданный разбор."""

    def __init__(self, found: Extracted) -> None:
        self.found = found

    async def extract(self, incoming: Incoming) -> Extracted:
        return self.found


async def _thread(session: AsyncSession, email: str = f"editor@{DONOR}") -> ThreadModel:
    """Переписка с донором — своя на каждый адрес, как в жизни."""
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == DONOR))
    if domain is None:
        domain = await make_donor(session, DONOR)
    campaign = CampaignModel(stage=Stage.DONORS, name=f"Цены: {email}", status="running")
    contact = ContactModel(domain_id=domain.id, email=email, source=ContactSource.PAGE)
    session.add_all([campaign, contact])
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add(thread)
    await session.flush()
    return thread


async def _answer(
    session: AsyncSession, thread: ThreadModel, days: int, found: Extracted | None = None
) -> ReplyModel:
    """Ответ донора, пришедший на `days`-й день; с `found` — сразу разобранный очередью."""
    at = DAY + timedelta(days=days)
    reply = ReplyModel(
        thread_id=thread.id,
        kind=ReplyKind.HUMAN,
        raw_body=f"Ответ на {days}-й день.",
        from_email=f"editor@{DONOR}",
        inbound_message_id=f"<{thread.id}-{days}@{DONOR}>",
        created_at=at,
    )
    session.add(reply)
    await session.flush()
    if found is not None:
        await _parse(session, reply, found)
    return reply


async def _parse(session: AsyncSession, reply: ReplyModel, found: Extracted) -> None:
    await Parser(session, Said(found)).parse(reply.id)  # type: ignore[arg-type]
    await session.flush()


async def _state(session: AsyncSession) -> tuple[DonorModel, DomainModel]:
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == DONOR))
    assert domain is not None
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    await session.refresh(domain)
    await session.refresh(donor)
    return donor, domain


def _price(donor: DonorModel) -> tuple[Any, ...]:
    """Цена донора со всеми полями, что пишутся с ней."""
    return (
        donor.last_price,
        donor.last_price_currency,
        donor.last_price_at,
        donor.last_offers,
        donor.last_price_source,
        donor.last_price_note,
        donor.last_price_by,
    )


NO_PRICE = (None,) * 7


async def _niche_business(session: AsyncSession) -> DomainModel:
    """Бизнес ниши из прогона, где донор — пример нашей площадки (`_example_host`)."""
    settings = await RunRepository(session).create_settings(
        Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = RunModel(
        stage=Stage.DONORS,
        settings_id=settings.id,
        status=RunStatus.DONE,
        keywords=["betting tips"],
        country="de",
        candidates={"hosts": [BOOKIE, DONOR]},
    )
    bookie = DomainModel(host=BOOKIE)
    session.add_all([run, bookie])
    await session.flush()
    session.add(
        AdvertiserModel(
            domain_id=bookie.id, source=NICHE, found_run_id=run.id, points=0, donors=0, links=0
        )
    )
    await session.flush()
    return bookie


@pytest.fixture
async def token(make_user: Any, sign_in: Any) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return str(await sign_in("оператор@site.com"))


class TestAnswerWithoutAPrice:
    async def test_a_decline_after_a_price_takes_it_and_stage_two_drops_the_domain(
        self, session: AsyncSession
    ) -> None:
        thread = await _thread(session)
        await make_advertiser(session, BRAND)
        bookie = await _niche_business(session)
        await _answer(session, thread, 1, PRICE)
        stage_two = NicheRecipients(session)
        assert (await stage_two.advertiser_funnel(now=NOW)).fresh_price == 1
        assert await stage_two.offer_of(bookie.id, NOW) is not None

        await _answer(session, thread, 3, DECLINES)

        donor, domain = await _state(session)
        assert _price(donor) == NO_PRICE
        assert domain.seller_answer == "declines"
        # Оффер «мы дешевле» стоял на цене этого донора — цены нет, нет и оффера.
        assert (await stage_two.advertiser_funnel(now=NOW)).fresh_price == 0
        assert await stage_two.advertiser_candidates(limit=10, now=NOW) == []
        assert await stage_two.offer_of(bookie.id, NOW) is None

    async def test_free_after_a_price_takes_it(self, session: AsyncSession) -> None:
        thread = await _thread(session)
        await _answer(session, thread, 1, PRICE)

        await _answer(session, thread, 3, FREE)

        donor, domain = await _state(session)
        assert _price(donor) == NO_PRICE
        assert domain.seller_answer == "free"

    async def test_a_price_named_after_a_decline_is_the_price_again(
        self, session: AsyncSession
    ) -> None:
        thread = await _thread(session)
        await _answer(session, thread, 1, PRICE)
        await _answer(session, thread, 2, DECLINES)
        assert _price((await _state(session))[0]) == NO_PRICE

        await _answer(session, thread, 3, Extracted(price_white=Decimal("120"), confidence=0.95))

        donor, domain = await _state(session)
        assert (donor.last_price, donor.last_price_at) == (Decimal("120.00"), DAY + timedelta(3))
        assert domain.seller_answer == "sells"


class TestLatestIsWhenTheDonorSaidIt:
    """Порядок — по тому, когда ответ пришёл: разбор опаздывает (очередь, потолок
    модели), а человек подтверждает неуверенный ответ через дни."""

    async def test_an_older_price_parsed_late_does_not_override_a_newer_decline(
        self, session: AsyncSession
    ) -> None:
        thread = await _thread(session)
        priced = await _answer(session, thread, 1)
        await _answer(session, thread, 3, DECLINES)

        await _parse(session, priced, PRICE)

        donor, domain = await _state(session)
        assert _price(donor) == NO_PRICE
        assert domain.seller_answer == "declines"

    async def test_an_older_decline_parsed_late_leaves_the_newer_price(
        self, session: AsyncSession
    ) -> None:
        thread = await _thread(session)
        declined = await _answer(session, thread, 1)
        await _answer(session, thread, 3, PRICE)

        await _parse(session, declined, DECLINES)

        donor, domain = await _state(session)
        assert (donor.last_price, donor.last_price_at) == (Decimal("150.00"), DAY + timedelta(3))
        assert (domain.seller_answer, domain.seller_answer_at) == ("sells", DAY + timedelta(3))

    async def test_answer_and_price_carry_the_time_the_donor_answered(
        self, session: AsyncSession
    ) -> None:
        """Срок цены (150 дней) и год вне отбора после «не продаём» — от ответа донора,
        а не от минуты, когда его разобрали."""
        thread = await _thread(session)
        await _answer(session, thread, 2, PRICE)

        donor, domain = await _state(session)
        assert donor.last_price_at == domain.seller_answer_at == DAY + timedelta(days=2)


class TestConfirmedLater:
    async def test_an_older_price_confirmed_after_a_newer_decline_stays_declined(
        self, session: AsyncSession, client: AsyncClient, token: str
    ) -> None:
        thread = await _thread(session)
        unsure = await _answer(session, thread, 1, UNSURE)
        declined = await _answer(session, thread, 3, DECLINES)
        await session.commit()

        card = (await client.get(f"/api/threads/{thread.id}", headers=bearer(token))).json()
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "USD"},
            headers=bearer(token),
        )

        # Перекрыт: разбирать его не зовут, подтвердить — отказ словами.
        old = next(one for one in card["incoming"] if one["id"] == unsure.id)
        assert (old["needs_review"], old["superseded_by"]) == (False, declined.id)
        assert card["card"]["state"] == "declined"
        assert response.status_code == 409, response.text
        assert "позже донор ответил «не продаёт»" in response.json()["detail"]
        donor, domain = await _state(session)
        assert _price(donor) == NO_PRICE
        assert domain.seller_answer == "declines"

    async def test_an_older_price_from_another_thread_does_not_reach_the_card(
        self, session: AsyncSession, client: AsyncClient, token: str
    ) -> None:
        """Переписка — на адрес, их у донора бывает несколько: «не продаём» пришло
        в одну, неуверенная цена ждала человека в другой."""
        unsure = await _answer(session, await _thread(session, f"info@{DONOR}"), 1, UNSURE)
        await _answer(session, await _thread(session), 3, DECLINES)
        await session.commit()

        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "USD"},
            headers=bearer(token),
        )

        assert response.status_code == 200, response.text
        assert (response.json()["stored_price"], response.json()["seller_answer"]) == (False, None)
        donor, domain = await _state(session)
        assert _price(donor) == NO_PRICE
        assert domain.seller_answer == "declines"

    async def test_an_older_price_confirmed_after_a_manual_one_leaves_the_manual(
        self, session: AsyncSession, client: AsyncClient, token: str
    ) -> None:
        unsure = await _answer(session, await _thread(session), 1, UNSURE)
        donor, _ = await _state(session)
        await price_donor(
            session, donor.id, manual_price("90", "USD", by=WHO), now=DAY + timedelta(5)
        )
        await session.commit()

        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "USD"},
            headers=bearer(token),
        )

        assert response.json()["stored_price"] is False
        donor, domain = await _state(session)
        assert (donor.last_price, donor.last_price_source) == (Decimal("90.00"), "manual")
        # «Продаёт» — слово самого донора, и оно тоже его: ответ ничего новее не перебивает.
        assert (domain.seller_answer, domain.seller_answer_at) == ("sells", DAY + timedelta(1))


class TestManualPrice:
    async def test_a_manual_price_after_a_decline_is_the_price(self, session: AsyncSession) -> None:
        """Цену человек знает не из письма: вписанная позже «не продаём» — цена донора.
        Ответ донора на домене остаётся его ответом."""
        await _answer(session, await _thread(session), 1, DECLINES)
        donor, _ = await _state(session)

        await price_donor(session, donor.id, manual_price("90", "USD", by=WHO), now=NOW)

        donor, domain = await _state(session)
        assert (donor.last_price, donor.last_price_at) == (Decimal("90.00"), NOW)
        assert domain.seller_answer == "declines"

    async def test_a_manual_price_older_than_the_answer_is_refused_in_words(
        self, session: AsyncSession
    ) -> None:
        """Правило одно у всех путей записи: слово новее этой минуты цену не пускает —
        отказом словами, а не записью, которой не было."""
        await _answer(session, await _thread(session), 3, DECLINES)
        donor, _ = await _state(session)

        with pytest.raises(DonorRefusedError, match="новее"):
            await price_donor(session, donor.id, manual_price("90", "USD", by=WHO), now=DAY)

        assert _price((await _state(session))[0]) == NO_PRICE
