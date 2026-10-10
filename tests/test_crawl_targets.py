"""Кого обходить: требование говорит «только доноры с известной ценой».

До этого модуля обход брал домены из командной строки и проверял их
только на robots.txt. Оффер рекламодателю строится на фразе «мы дешевле»,
а дешевле чего — знает только цена донора; протухшая цена делает эту
фразу обещанием, которого не сдержать.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.config import filters as cfg
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.donor import DonorModel
from backend.features.crawl.targets import choose, explain
from backend.features.donors.browse import DonorBrowser, DonorFilters
from backend.features.donors.manual_price import enter_host, manual_price
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

pytestmark = pytest.mark.asyncio


async def _donor(
    session: AsyncSession,
    host: str,
    *,
    price: Decimal | None = Decimal("120.00"),
    days_ago: int = 1,
    review: str | None = "accepted",
) -> None:
    domain = await make_donor(session, host, email=f"editor@{host}", review=review)
    donor = (
        await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id))
    ).scalar_one()
    if price is not None:
        donor.last_price = price
        donor.last_price_currency = "USD"
        donor.last_price_at = datetime.now(UTC) - timedelta(days=days_ago)
    await session.flush()


class TestWhoWeCrawl:
    async def test_a_donor_with_a_fresh_price_is_taken(self, session: AsyncSession) -> None:
        await _donor(session, "fresh.example.test")

        chosen = await choose(session)

        assert chosen.hosts == ["fresh.example.test"]

    async def test_a_donor_without_a_price_is_not(self, session: AsyncSession) -> None:
        """По нему нельзя написать ни одного письма: дешевле чего —
        неизвестно."""
        await _donor(session, "noprice.example.test", price=None)

        chosen = await choose(session)

        assert chosen.hosts == []
        assert chosen.no_price == ["noprice.example.test"]

    async def test_a_stale_price_is_a_separate_answer(self, session: AsyncSession) -> None:
        """«Цена протухла» и «цены нет» — разные состояния: у первой есть
        что перезапросить, у второй нет."""
        await _donor(session, "stale.example.test", days_ago=cfg.PRICE_TTL_DAYS + 5)

        chosen = await choose(session)

        assert chosen.hosts == []
        assert chosen.stale_price == ["stale.example.test"]
        assert chosen.no_price == []

    async def test_supplier_donors_are_not_crawled_at_all(self, session: AsyncSession) -> None:
        """Отсев поставщика раньше обхода, а не после: трафик на его
        страницы всё равно ни во что не превратится."""
        await _donor(session, "partner.example.test")
        session.add(SupplierDonorModel(host="partner.example.test"))
        await session.flush()

        chosen = await choose(session)

        assert chosen.hosts == []
        assert chosen.supplier == ["partner.example.test"]

    async def test_freshest_price_goes_first(self, session: AsyncSession) -> None:
        await _donor(session, "old.example.test", days_ago=100)
        await _donor(session, "new.example.test", days_ago=2)

        chosen = await choose(session, limit=1)

        assert chosen.hosts == ["new.example.test"]


class TestOnlyDonorsAcceptedByAPerson:
    """Донор — принятый человеком, как на экране «Доноры» (проверка QA 10.10.2026).

    Отбор смотрел только на «подходит» порогов: панель предлагала обойти домен,
    отклонённый человеком уже после того, как у него появилась цена, а «без
    цены — 26» стояло рядом с одним донором на «Донорах».
    """

    async def test_a_domain_rejected_by_a_person_is_not_offered(
        self, session: AsyncSession
    ) -> None:
        await _donor(session, "rejected.example.test", review="rejected")

        chosen = await choose(session)

        assert chosen.hosts == []
        # И не «не обходим по другой причине»: он не донор вовсе.
        assert chosen.as_dict() == {
            "к обходу": 0,
            "цены нет": 0,
            "цена протухла": 0,
            "донор-поставщик": 0,
        }

    async def test_a_candidate_nobody_reviewed_is_not_counted(self, session: AsyncSession) -> None:
        """Годный по порогам кандидат ждёт решения в очереди прогона — донором
        его делает человек, а не цифры."""
        await _donor(session, "waits.example.test", review=None)
        await _donor(session, "waits-unpriced.example.test", review=None, price=None)

        chosen = await choose(session)

        assert (chosen.hosts, chosen.no_price) == ([], [])

    async def test_a_donor_entered_by_hand_is_offered(self, session: AsyncSession) -> None:
        """Заведённый вручную — принят тем, кто завёл: обход его берёт."""
        await enter_host(
            session,
            "example.com",
            manual_price("90", "USD", "прайс агентства", by="anna@ours.example.test"),
        )

        assert (await choose(session)).hosts == ["example.com"]

    async def test_counts_add_up_to_the_donors_screen(self, session: AsyncSession) -> None:
        """Обходим, без цены, цена протухла, поставщик — это все доноры экрана
        «Доноры», и только они."""
        await _donor(session, "fresh.example.test")
        await _donor(session, "unpriced.example.test", price=None)
        await _donor(session, "stale.example.test", days_ago=cfg.PRICE_TTL_DAYS + 5)
        await _donor(session, "rejected.example.test", review="rejected", price=None)
        await _donor(session, "waits.example.test", review=None, price=None)

        chosen = await choose(session)
        screen = await DonorBrowser(session).page(DonorFilters())

        counted = chosen.hosts + chosen.no_price + chosen.stale_price + chosen.supplier
        assert sorted(counted) == sorted(row.host for row in screen.rows)
        assert len(counted) == screen.total == 3


class TestItSaysWhatToDo:
    async def test_stale_prices_are_explained(self, session: AsyncSession) -> None:
        """«Доноров нет» без продолжения — полсообщения."""
        await _donor(session, "stale.example.test", days_ago=cfg.PRICE_TTL_DAYS + 5)

        notes = explain(await choose(session))

        assert any("перезапрос цены" in note for note in notes)

    async def test_an_empty_base_is_explained_too(self, session: AsyncSession) -> None:
        notes = explain(await choose(session))

        assert any("прогон Этапа 1" in note for note in notes)

    async def test_nothing_is_said_when_there_is_work(self, session: AsyncSession) -> None:
        await _donor(session, "fresh.example.test")

        assert explain(await choose(session)) == []
