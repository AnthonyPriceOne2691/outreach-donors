"""Цена в таблице доноров: колонка, фильтры «с ценой» и «устарела», решение человека,
порядок не только по DR — и цена целиком в карточке.

Сверка ТЗ 12.10.2026: «Доноры — таблица со всеми сайтами, статусом и объяснением, по какому
именно порогу сайт отсеян. Фильтры, сортировка, выгрузка»; «свежесть цены 150 дней;
старше — сначала перезапрос». До неё у таблицы не было цены вовсе, порядок был один —
по DR, а экран показывал только принятых, и «все сайты» ТЗ было не увидеть.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.config import filters as filters_cfg
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    ReplyKind,
    Stage,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, ReplyModel, ThreadModel
from backend.features.donors.browse import DonorBrowser
from backend.features.donors.price_freshness import PriceState
from backend.features.donors.table_filters import DonorFilters, Review
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Сейчас, а не дата: срок цены маршрут меряет от настоящих часов.
NOW = datetime.now(UTC)
TTL = timedelta(days=filters_cfg.PRICE_TTL_DAYS)


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


async def _donor(
    session: AsyncSession,
    host: str,
    *,
    price: str | None = None,
    priced: datetime | None = None,
    currency: str = "USD",
    dr: int = 30,
    traffic: int | None = None,
    review: str | None = "accepted",
) -> DonorModel:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=DonorStatus.SUITABLE,
        dr=dr,
        org_traffic=traffic,
        review=review,
        last_price=None if price is None else Decimal(price),
        last_price_currency=None if price is None else currency,
        last_price_at=priced if price is not None else None,
    )
    session.add(donor)
    await session.flush()
    return donor


@pytest.fixture
async def priced(session: AsyncSession) -> None:
    """Доноры со свежей, устаревшей и без цены — и два не донора."""
    await _donor(session, "cheap.example.test", price="90", priced=NOW - timedelta(days=3), dr=40)
    await _donor(session, "dear.example.test", price="400", priced=NOW - timedelta(days=30), dr=70)
    await _donor(session, "old.example.test", price="150", priced=NOW - TTL * 2, dr=20)
    await _donor(session, "none.example.test", dr=90, traffic=5_000)
    await _donor(session, "rejected.example.test", price="10", priced=NOW, review="rejected")
    await _donor(session, "waiting.example.test", dr=50, review=None)
    await session.commit()


def _hosts(body: dict[str, Any]) -> list[str]:
    return [row["host"] for row in body["rows"]]


async def _page(client: AsyncClient, token: str, query: str = "") -> dict[str, Any]:
    response = await client.get(f"/api/donors{query}", headers=bearer(token))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


class TestPriceColumn:
    async def test_a_row_carries_the_price_its_date_and_staleness(
        self, client: AsyncClient, token: str, priced: None
    ) -> None:
        rows = {row["host"]: row for row in (await _page(client, token))["rows"]}

        cheap, old = rows["cheap.example.test"], rows["old.example.test"]
        assert (cheap["last_price"], cheap["last_price_currency"]) == ("90.00", "USD")
        assert cheap["last_price_at"] is not None
        assert (cheap["price_stale"], old["price_stale"]) == (False, True)
        assert rows["none.example.test"]["price_stale"] is False

    @pytest.mark.parametrize(
        ("state", "hosts"),
        [
            ("priced", {"cheap.example.test", "dear.example.test", "old.example.test"}),
            ("fresh", {"cheap.example.test", "dear.example.test"}),
            ("stale", {"old.example.test"}),
            ("none", {"none.example.test"}),
        ],
    )
    async def test_price_filter(
        self, client: AsyncClient, token: str, priced: None, state: str, hosts: set[str]
    ) -> None:
        assert set(_hosts(await _page(client, token, f"?price={state}"))) == hosts

    async def test_price_counts_are_the_options_of_the_filter(
        self, client: AsyncClient, token: str, priced: None
    ) -> None:
        body = await _page(client, token, "?price=stale")

        assert body["prices"] == {"priced": 3, "fresh": 2, "stale": 1, "none": 1}
        assert body["price_ttl_days"] == filters_cfg.PRICE_TTL_DAYS

    async def test_stale_mark_and_filter_agree_at_the_border(self, session: AsyncSession) -> None:
        """Одно условие у значка и фильтра: на самой границе срока они не расходятся."""
        await _donor(session, "edge.example.test", price="100", priced=NOW - TTL)
        browser = DonorBrowser(session)

        (row,) = (await browser.page(DonorFilters(), now=NOW)).rows
        stale = await browser.page(DonorFilters(price=PriceState.STALE), now=NOW)

        assert row.price_stale is False
        assert stale.rows == []


class TestDecision:
    async def test_without_the_filter_the_screen_is_donors_as_before(
        self, client: AsyncClient, token: str, priced: None
    ) -> None:
        body = await _page(client, token)

        assert "rejected.example.test" not in _hosts(body)
        assert "waiting.example.test" not in _hosts(body)
        assert body["reviews"] == {"accepted": 4, "rejected": 1, "undecided": 1, "any": 6}

    @pytest.mark.parametrize(
        ("review", "hosts"),
        [
            ("rejected", ["rejected.example.test"]),
            ("undecided", ["waiting.example.test"]),
        ],
    )
    async def test_other_decisions_are_on_request(
        self, client: AsyncClient, token: str, priced: None, review: str, hosts: list[str]
    ) -> None:
        body = await _page(client, token, f"?review={review}")

        assert _hosts(body) == hosts
        assert body["total"] == 1
        assert body["rows"][0]["review"] == (None if review == "undecided" else review)

    async def test_all_checked_sites_and_their_counts(
        self, client: AsyncClient, token: str, priced: None
    ) -> None:
        """«Все проверенные» — таблица ТЗ; счётчики под фильтрами — по ней же."""
        body = await _page(client, token, "?review=any")

        assert body["total"] == 6
        assert sum(body["counts"].values()) == 6

    async def test_picked_are_exported_under_the_decision_they_were_picked_at(
        self, client: AsyncClient, token: str, session: AsyncSession, priced: None
    ) -> None:
        rejected = await DonorBrowser(session).page(DonorFilters(review=Review.REJECTED))
        ids = [rejected.rows[0].donor.id]

        as_donors = await client.post(
            "/api/donors/export", json={"ids": ids}, headers=bearer(token)
        )
        as_rejected = await client.post(
            "/api/donors/export", json={"ids": ids, "review": "rejected"}, headers=bearer(token)
        )

        assert (as_donors.headers["X-Export-Rows"], as_donors.headers["X-Export-Not-Donors"]) == (
            "0",
            "1",
        )
        assert as_rejected.headers["X-Export-Rows"] == "1"


class TestOrder:
    @pytest.mark.parametrize(
        ("sort", "hosts"),
        [
            (
                "dr",
                [
                    "none.example.test",
                    "dear.example.test",
                    "cheap.example.test",
                    "old.example.test",
                ],
            ),
            (
                "price",
                [
                    "cheap.example.test",
                    "old.example.test",
                    "dear.example.test",
                    "none.example.test",
                ],
            ),
            (
                "price_desc",
                [
                    "dear.example.test",
                    "old.example.test",
                    "cheap.example.test",
                    "none.example.test",
                ],
            ),
            (
                "priced_new",
                [
                    "cheap.example.test",
                    "dear.example.test",
                    "old.example.test",
                    "none.example.test",
                ],
            ),
            (
                "priced_old",
                [
                    "old.example.test",
                    "dear.example.test",
                    "cheap.example.test",
                    "none.example.test",
                ],
            ),
        ],
    )
    async def test_sorted_by(
        self, client: AsyncClient, token: str, priced: None, sort: str, hosts: list[str]
    ) -> None:
        """Пусто — в конце при любом направлении."""
        assert _hosts(await _page(client, token, f"?sort={sort}")) == hosts

    async def test_by_traffic_and_unknown_last(
        self, client: AsyncClient, token: str, priced: None
    ) -> None:
        assert _hosts(await _page(client, token, "?sort=traffic"))[0] == "none.example.test"

    async def test_export_keeps_the_order_of_the_screen(
        self, client: AsyncClient, token: str, priced: None
    ) -> None:
        exported = await client.get("/api/donors/export?sort=price", headers=bearer(token))

        lines = exported.content.decode("utf-8-sig").splitlines()[1:]
        assert [line.split(";")[0] for line in lines] == [
            "cheap.example.test",
            "old.example.test",
            "dear.example.test",
            "none.example.test",
        ]

    async def test_picked_are_exported_in_the_order_of_the_screen(
        self, client: AsyncClient, token: str, session: AsyncSession, priced: None
    ) -> None:
        """Отмеченные — в выбранном порядке: «Порядок» над таблицей обещает файл тот же."""
        page = await DonorBrowser(session).page(DonorFilters())
        ids = [row.donor.id for row in page.rows]

        exported = await client.post(
            "/api/donors/export", json={"ids": ids, "sort": "price_desc"}, headers=bearer(token)
        )

        lines = exported.content.decode("utf-8-sig").splitlines()[1:]
        assert [line.split(";")[0] for line in lines] == [
            "dear.example.test",
            "old.example.test",
            "cheap.example.test",
            "none.example.test",
        ]

    async def test_an_unknown_order_is_refused(self, client: AsyncClient, token: str) -> None:
        response = await client.get("/api/donors?sort=cheapest", headers=bearer(token))

        assert response.status_code == 422


class TestCard:
    async def test_the_card_has_the_whole_price_and_the_way_to_its_reply(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "card.example.test", price="150", priced=NOW - TTL * 2)
        campaign = CampaignModel(stage=Stage.DONORS, name="Цены", status="running")
        contact = ContactModel(
            domain_id=donor.domain_id, email="ed@card.example.test", source=ContactSource.PAGE
        )
        session.add_all([campaign, contact])
        await session.flush()
        thread = ThreadModel(
            domain_id=donor.domain_id, campaign_id=campaign.id, contact_id=contact.id
        )
        session.add(thread)
        await session.flush()
        reply = ReplyModel(
            thread_id=thread.id,
            kind=ReplyKind.HUMAN,
            raw_body=(
                "A guest post is 150 USD.\n\n"
                "On Mon, 5 Oct 2026 at 10:00, Anna <anna@ours.example.test> wrote:\n"
                "> What is your price?"
            ),
        )
        session.add(reply)
        await session.flush()
        # Цены — как их отдаёт база (`DECIMAL(10, 2)`): сессия теста одна на тест
        # и помнит записанное.
        donor.last_price_white = Decimal("150.00")
        donor.last_price_grey = Decimal("200.00")
        donor.last_payment_methods = ["PayPal"]
        donor.last_placement_days = 4
        donor.last_link_type = "nofollow"
        donor.last_price_reply_id = reply.id
        await session.commit()

        card = (await client.get(f"/api/donors/{donor.id}", headers=bearer(token))).json()

        assert (card["last_price_white"], card["last_price_grey"]) == ("150.00", "200.00")
        assert card["last_payment_methods"] == ["PayPal"]
        assert (card["last_placement_days"], card["last_link_type"]) == (4, "nofollow")
        assert (card["last_price_reply_id"], card["last_price_thread_id"]) == (reply.id, thread.id)
        assert card["price_stale"] is True
        # Рядом с ценой — что донор написал, без цитаты нашего письма (ТЗ: «и рядом
        # исходный текст ответа, из которого они взяты»).
        assert card["last_price_reply_text"] == "A guest post is 150 USD."
        expires = datetime.fromisoformat(card["price_expires_at"])
        assert expires == (NOW - TTL * 2 + TTL)

    async def test_a_reply_that_is_gone_leaves_no_link(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "gone.example.test", price="150", priced=NOW)
        donor.last_price_reply_id = 987_654
        await session.commit()

        card = (await client.get(f"/api/donors/{donor.id}", headers=bearer(token))).json()

        assert (card["last_price_reply_id"], card["last_price_thread_id"]) == (987_654, None)
        assert card["last_price_reply_text"] is None
        assert (card["price_stale"], card["price_expires_at"] is not None) == (False, True)
