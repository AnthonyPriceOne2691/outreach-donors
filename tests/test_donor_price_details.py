"""Цена донора целиком: белая и серая, оплата, срок, тип ссылки и ответ, из которого она.

Сверка ТЗ 12.10.2026: «разбирает его на поля — белая цена, серая цена, способы оплаты —
и кладёт в ту же базу». До неё на донора ложилась одна цена; серая и оплата оставались
только у ответа, а срок публикации и тип ссылки модель возвращала, и не сохранял их никто.
Подтверждение разбора к тому же стирало оплату у ответа: форма её не показывает и слала
пустой список.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.features.core.domain import ContactSource, PriceSource, ReplyKind, Stage, UserRole
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel, ThreadModel
from backend.features.donors.manual_price import (
    MAX_PAYMENT_METHOD,
    ManualPriceError,
    manual_price,
    price_donor,
)
from backend.features.donors.price import PRICE_COLUMNS
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Parser
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.migration_helpers import columns_down_and_up

DONOR = "donor.example.test"
WHO = "anna@ours.example.test"
DAY = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)

#: Ответ со всем, что донор может сказать о цене.
FULL = Extracted(
    price_white=Decimal("150"),
    price_grey=Decimal("200"),
    currency="USD",
    payment_methods=("PayPal", "Wise"),
    placement_days=5,
    link_type="dofollow",
    placement="sells",
    confidence=0.95,
)
#: Одна цена, больше ничего.
BARE = Extracted(price_white=Decimal("120"), currency="USD", placement="sells", confidence=0.95)
DECLINES = Extracted(placement="declines", placement_quote="we do not sell", confidence=0.9)


class Said:
    """Модель разбора: отдаёт заданный разбор."""

    def __init__(self, found: Extracted) -> None:
        self.found = found

    async def extract(self, incoming: Incoming) -> Extracted:
        return self.found


@pytest.fixture
async def token(make_user: Any, sign_in: Any) -> str:
    await make_user(WHO, role=UserRole.OPERATOR)
    return str(await sign_in(WHO))


async def _thread(session: AsyncSession) -> ThreadModel:
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == DONOR))
    if domain is None:
        domain = await make_donor(session, DONOR)
    campaign = CampaignModel(stage=Stage.DONORS, name="Цены", status="running")
    contact = ContactModel(domain_id=domain.id, email=f"editor@{DONOR}", source=ContactSource.PAGE)
    session.add_all([campaign, contact])
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add(thread)
    await session.flush()
    return thread


async def _answer(
    session: AsyncSession, thread: ThreadModel, days: int, found: Extracted
) -> ReplyModel:
    """Ответ донора, пришедший на `days`-й день и разобранный очередью."""
    reply = ReplyModel(
        thread_id=thread.id,
        kind=ReplyKind.HUMAN,
        raw_body=f"Ответ на {days}-й день.",
        from_email=f"editor@{DONOR}",
        inbound_message_id=f"<{thread.id}-{days}@{DONOR}>",
        created_at=DAY + timedelta(days=days),
    )
    session.add(reply)
    await session.flush()
    await Parser(session, Said(found)).parse(reply.id)  # type: ignore[arg-type]
    await session.flush()
    return reply


async def _donor(session: AsyncSession) -> DonorModel:
    donor = await session.scalar(
        select(DonorModel).join(DomainModel).where(DomainModel.host == DONOR)
    )
    assert donor is not None
    await session.refresh(donor)
    return donor


def _details(donor: DonorModel) -> tuple[Any, ...]:
    """Что легло рядом с ценой: белая, серая, оплата, срок, тип ссылки, ответ."""
    return (
        donor.last_price_white,
        donor.last_price_grey,
        donor.last_payment_methods,
        donor.last_placement_days,
        donor.last_link_type,
        donor.last_price_reply_id,
    )


class TestFromTheReply:
    async def test_the_reply_keeps_days_and_link_and_the_snapshot_has_them(
        self, session: AsyncSession
    ) -> None:
        reply = await _answer(session, await _thread(session), 1, FULL)

        await session.refresh(reply)
        assert (reply.placement_days, reply.link_type) == (5, "dofollow")
        assert reply.payment_methods == ["PayPal", "Wise"]
        assert reply.model_parse is not None
        snapshot = reply.model_parse
        assert (snapshot["placement_days"], snapshot["link_type"]) == (5, "dofollow")

    async def test_the_donor_gets_everything_said_next_to_the_price(
        self, session: AsyncSession
    ) -> None:
        reply = await _answer(session, await _thread(session), 1, FULL)

        donor = await _donor(session)
        assert (donor.last_price, donor.last_price_currency) == (Decimal("150.00"), "USD")
        assert _details(donor) == (
            Decimal("150.00"),
            Decimal("200.00"),
            ["PayPal", "Wise"],
            5,
            "dofollow",
            reply.id,
        )

    async def test_a_grey_only_price_is_the_price_and_stays_grey(
        self, session: AsyncSession
    ) -> None:
        """Главная цена — белая, а без неё серая (`last_price` читают обход и офферы);
        белой донор не называл — её и нет."""
        grey = Extracted(price_grey=Decimal("90"), currency="EUR", confidence=0.95)
        await _answer(session, await _thread(session), 1, grey)

        donor = await _donor(session)
        assert donor.last_price == Decimal("90.00")
        assert (donor.last_price_white, donor.last_price_grey) == (None, Decimal("90.00"))

    async def test_a_newer_reply_brings_its_own_details_and_drops_the_old(
        self, session: AsyncSession
    ) -> None:
        """Оплата и срок прежнего ответа рядом с новой ценой врали бы: все поля разом."""
        thread = await _thread(session)
        await _answer(session, thread, 1, FULL)
        newer = await _answer(session, thread, 3, BARE)

        donor = await _donor(session)
        assert donor.last_price == Decimal("120.00")
        assert _details(donor) == (Decimal("120.00"), None, None, None, None, newer.id)

    async def test_a_decline_takes_every_price_column(self, session: AsyncSession) -> None:
        thread = await _thread(session)
        await _answer(session, thread, 1, FULL)

        await _answer(session, thread, 3, DECLINES)

        donor = await _donor(session)
        assert {column: getattr(donor, column) for column in PRICE_COLUMNS} == dict.fromkeys(
            PRICE_COLUMNS
        )


def test_every_price_column_is_written_and_dropped() -> None:
    """Колонка цены, заведённая без записи в `PRICE_COLUMNS`, осталась бы после «не продаём»
    словами ответа, который последним уже не был."""
    columns = {column.key for column in DonorModel.__table__.columns}
    assert {column for column in columns if column.startswith("last_")} == set(PRICE_COLUMNS)


class TestConfirmed:
    async def test_confirming_keeps_the_payment_the_model_found(
        self, session: AsyncSession, client: AsyncClient, token: str
    ) -> None:
        """Форма подтверждения оплату не показывает и не шлёт: до 12.10.2026 она приходила
        пустым списком и стирала то, что нашла модель."""
        unsure = Extracted(
            price_white=Decimal("250"),
            currency="USD",
            payment_methods=("PayPal",),
            placement_days=7,
            link_type="nofollow",
            placement="sells",
            confidence=0.4,
        )
        reply = await _answer(session, await _thread(session), 1, unsure)
        await session.commit()

        response = await client.patch(
            f"/api/replies/{reply.id}",
            json={"price_white": "250", "price_grey": "300", "currency": "USD"},
            headers=bearer(token),
        )

        assert response.status_code == 200, response.text
        assert response.json()["stored_price"] is True
        await session.refresh(reply)
        assert reply.payment_methods == ["PayPal"]
        assert _details(await _donor(session)) == (
            Decimal("250.00"),
            Decimal("300.00"),
            ["PayPal"],
            7,
            "nofollow",
            reply.id,
        )

    async def test_a_payment_list_from_a_person_is_the_decision(
        self, session: AsyncSession, client: AsyncClient, token: str
    ) -> None:
        unsure = Extracted(
            price_white=Decimal("250"),
            currency="USD",
            payment_methods=("PayPal",),
            confidence=0.4,
        )
        reply = await _answer(session, await _thread(session), 1, unsure)
        await session.commit()

        response = await client.patch(
            f"/api/replies/{reply.id}",
            json={"price_white": "250", "currency": "USD", "payment_methods": ["Wise"]},
            headers=bearer(token),
        )

        assert response.status_code == 200, response.text
        await session.refresh(reply)
        assert reply.payment_methods == ["Wise"]
        assert (await _donor(session)).last_payment_methods == ["Wise"]


class TestConfirmedPayment:
    """Оплата, присланная с подтверждением разбора, — правилами цены руками."""

    async def _confirm(
        self, session: AsyncSession, client: AsyncClient, token: str, methods: Any
    ) -> Any:
        unsure = Extracted(price_white=Decimal("250"), currency="USD", confidence=0.4)
        reply = await _answer(session, await _thread(session), 1, unsure)
        await session.commit()
        response = await client.patch(
            f"/api/replies/{reply.id}",
            json={"price_white": "250", "currency": "USD", "payment_methods": methods},
            headers=bearer(token),
        )
        return response, reply

    async def test_a_long_method_is_refused_and_nothing_is_written(
        self, session: AsyncSession, client: AsyncClient, token: str
    ) -> None:
        response, reply = await self._confirm(
            session, client, token, ["x" * (MAX_PAYMENT_METHOD + 1)]
        )

        assert response.status_code == 400, response.text
        assert "не способ оплаты" in response.json()["detail"]
        await session.refresh(reply)
        assert reply.reviewed_at is None

    async def test_too_many_methods_are_refused(
        self, session: AsyncSession, client: AsyncClient, token: str
    ) -> None:
        response, _ = await self._confirm(session, client, token, [f"way {n}" for n in range(11)])

        assert response.status_code == 400, response.text
        assert "не больше 10" in response.json()["detail"]

    async def test_methods_come_tidy_and_a_bracket_keeps_its_comma(
        self, session: AsyncSession, client: AsyncClient, token: str
    ) -> None:
        response, reply = await self._confirm(
            session, client, token, [" Bank transfer (SWIFT, SEPA), PayPal ", "PayPal"]
        )

        assert response.status_code == 200, response.text
        await session.refresh(reply)
        assert reply.payment_methods == ["Bank transfer (SWIFT, SEPA)", "PayPal"]


class TestByHand:
    async def test_grey_and_payment_land_with_the_price(self, session: AsyncSession) -> None:
        await make_donor(session, DONOR)
        donor = await _donor(session)

        price = manual_price("90", "USD", by=WHO, grey="120", payment="PayPal, Wise; карта")
        await price_donor(session, donor.id, price, now=DAY)

        donor = await _donor(session)
        assert (donor.last_price, donor.last_price_source) == (Decimal("90.00"), PriceSource.MANUAL)
        assert _details(donor) == (
            Decimal("90.00"),
            Decimal("120.00"),
            ["PayPal", "Wise", "карта"],
            None,
            None,
            None,
        )

    async def test_a_price_by_hand_drops_what_the_reply_said(self, session: AsyncSession) -> None:
        """Цену руками человек знает не из письма: срок и ссылка ответа к ней не относятся."""
        await _answer(session, await _thread(session), 1, FULL)
        donor = await _donor(session)

        await price_donor(session, donor.id, manual_price("90", by=WHO), now=DAY + timedelta(2))

        donor = await _donor(session)
        assert _details(donor) == (Decimal("90.00"), None, None, None, None, None)

    def test_grey_is_checked_like_the_price(self) -> None:
        with pytest.raises(ManualPriceError, match="Серая цена — «сто» — не цена"):
            manual_price("90", by=WHO, grey="сто")

    def test_payment_methods_come_tidy(self) -> None:
        price = manual_price("90", by=WHO, payment=[" PayPal ", "PayPal", "Wise,  card", ""])

        assert price.payment_methods == ("PayPal", "Wise", "card")
        assert manual_price("90", by=WHO).payment_methods == ()

    def test_a_comma_in_brackets_does_not_split_a_method(self) -> None:
        price = manual_price("90", by=WHO, payment="Bank transfer (SWIFT, SEPA), PayPal")

        assert price.payment_methods == ("Bank transfer (SWIFT, SEPA)", "PayPal")

    def test_a_long_payment_method_is_refused_in_words(self) -> None:
        with pytest.raises(ManualPriceError, match="не способ оплаты"):
            manual_price("90", by=WHO, payment="x" * (MAX_PAYMENT_METHOD + 1))

    async def test_the_card_route_takes_grey_and_payment(
        self, session: AsyncSession, client: AsyncClient, token: str
    ) -> None:
        await make_donor(session, DONOR)
        donor = await _donor(session)
        await session.commit()

        response = await client.post(
            f"/api/donors/{donor.id}/price",
            json={"price": "90", "price_grey": "120", "payment_methods": ["PayPal", "Wise"]},
            headers=bearer(token),
        )

        assert response.status_code == 200, response.text
        donor = await _donor(session)
        assert (donor.last_price_grey, donor.last_payment_methods) == (
            Decimal("120.00"),
            ["PayPal", "Wise"],
        )

    async def test_a_bad_grey_is_a_refusal_in_words(
        self, session: AsyncSession, client: AsyncClient, token: str
    ) -> None:
        await make_donor(session, DONOR)
        donor = await _donor(session)
        await session.commit()

        response = await client.post(
            f"/api/donors/{donor.id}/price",
            json={"price": "90", "price_grey": "-5"},
            headers=bearer(token),
        )

        assert response.status_code == 400, response.text
        assert response.json()["detail"].startswith("Серая цена — «-5» — не цена")
        assert (await _donor(session)).last_price is None


async def test_migration_goes_down_and_up(session: AsyncSession) -> None:
    """Ревизия, которую выкатка применит к проду, — вниз и вверх на тестовой базе."""
    connection = await session.connection()
    donors = {
        "last_price_white",
        "last_price_grey",
        "last_payment_methods",
        "last_placement_days",
        "last_link_type",
        "last_price_reply_id",
    }
    replies = {"placement_days", "link_type", "from_name"}
    revision = "7c3e9a1f5b24_donor_price_terms.py"

    found_donors = await connection.run_sync(columns_down_and_up, revision, "donors", donors)
    found_replies = await connection.run_sync(columns_down_and_up, revision, "replies", replies)

    assert found_donors == (set(), donors)
    assert found_replies == (set(), replies)
