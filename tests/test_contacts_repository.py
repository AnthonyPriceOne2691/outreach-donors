"""Хранение контактов — на настоящей базе.

Проверять запрос к базе на подделке значит проверять подделку: типы,
уникальные ключи и enum-колонки живут в Postgres, а не в модели. Один
такой случай уже стоил нам боевого прогона — целое число в модели
выглядело верным, пока не приехал домен с трафиком за два миллиарда.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.quality import Candidate
from backend.features.contacts.repository import ContactRepository
from backend.features.core.domain import ContactSource, ContactStatus, DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


async def _donor(
    session: AsyncSession,
    host: str,
    *,
    status: DonorStatus = DonorStatus.SUITABLE,
    contact_status: ContactStatus | None = None,
    attempted_at: datetime | None = None,
    dr: int | None = 40,
) -> int:
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    session.add(
        DonorModel(
            domain_id=domain.id,
            status=status,
            dr=dr,
            contact_status=contact_status,
            contact_attempted_at=attempted_at,
        )
    )
    await session.flush()
    return int(domain.id)


def _found(host: str, email: str) -> LadderResult:
    return LadderResult(
        host=host,
        status=ContactStatus.FOUND,
        contact=Candidate(email, ContactSource.PAGE),
        source=ContactSource.PAGE,
    )


class TestPending:
    async def test_donor_without_an_attempt_is_taken(self, session: AsyncSession) -> None:
        await _donor(session, "fresh.com")
        assert await ContactRepository(session).pending_hosts(now=NOW) == ["fresh.com"]

    async def test_unsuitable_donor_is_skipped(self, session: AsyncSession) -> None:
        """Контакт ищем только тем, кому собираемся писать."""
        await _donor(session, "bad.com", status=DonorStatus.UNSUITABLE)
        assert await ContactRepository(session).pending_hosts(now=NOW) == []

    async def test_recent_not_found_is_not_repeated(self, session: AsyncSession) -> None:
        """За этот домен уже сходили: повтор стоил бы денег ни за что."""
        await _donor(
            session,
            "tried.com",
            contact_status=ContactStatus.NOT_FOUND,
            attempted_at=NOW - timedelta(days=5),
        )
        assert await ContactRepository(session).pending_hosts(now=NOW) == []

    async def test_stale_attempt_comes_back(self, session: AsyncSession) -> None:
        await _donor(
            session,
            "old.com",
            contact_status=ContactStatus.NOT_FOUND,
            attempted_at=NOW - timedelta(days=400),
        )
        assert await ContactRepository(session).pending_hosts(now=NOW) == ["old.com"]

    async def test_no_quota_returns_even_when_fresh(self, session: AsyncSession) -> None:
        """«Квота кончилась» — не «контакта нет»: домен обязан вернуться."""
        await _donor(
            session,
            "unpaid.com",
            contact_status=ContactStatus.NO_QUOTA,
            attempted_at=NOW - timedelta(hours=1),
        )
        assert await ContactRepository(session).pending_hosts(now=NOW) == ["unpaid.com"]

    async def test_strongest_donors_go_first(self, session: AsyncSession) -> None:
        """Кончится квота — пусть она кончится на слабых, а не на сильных."""
        await _donor(session, "weak.com", dr=20)
        await _donor(session, "strong.com", dr=80)
        hosts = await ContactRepository(session).pending_hosts(now=NOW)
        assert hosts == ["strong.com", "weak.com"]

    async def test_limit_holds(self, session: AsyncSession) -> None:
        for i in range(5):
            await _donor(session, f"site{i}.com", dr=i)
        hosts = await ContactRepository(session).pending_hosts(limit=2, now=NOW)
        assert len(hosts) == 2


class TestSave:
    async def test_address_and_outcome_are_stored(self, session: AsyncSession) -> None:
        domain_id = await _donor(session, "site.com")
        saved = await ContactRepository(session).save([_found("site.com", "ads@site.com")], now=NOW)
        await session.flush()

        assert saved == 1
        contact = (
            await session.execute(select(ContactModel).where(ContactModel.domain_id == domain_id))
        ).scalar_one()
        assert contact.email == "ads@site.com"
        assert contact.source is ContactSource.PAGE

        donor = (
            await session.execute(select(DonorModel).where(DonorModel.domain_id == domain_id))
        ).scalar_one()
        assert donor.contact_status is ContactStatus.FOUND
        assert donor.contact_attempted_at is not None

    async def test_outcome_without_address_still_marks_the_attempt(
        self, session: AsyncSession
    ) -> None:
        """Отметка времени без адреса — это и есть «искали, не нашли».
        Без неё каждый прогон ходил бы по домену заново."""
        domain_id = await _donor(session, "empty.com")
        result = LadderResult(host="empty.com", status=ContactStatus.NOT_FOUND)

        saved = await ContactRepository(session).save([result], now=NOW)
        await session.flush()

        assert saved == 0
        donor = (
            await session.execute(select(DonorModel).where(DonorModel.domain_id == domain_id))
        ).scalar_one()
        assert donor.contact_status is ContactStatus.NOT_FOUND
        assert donor.contact_attempted_at is not None

    async def test_second_run_does_not_duplicate_the_address(self, session: AsyncSession) -> None:
        domain_id = await _donor(session, "site.com")
        repository = ContactRepository(session)

        await repository.save([_found("site.com", "ads@site.com")], now=NOW)
        await repository.save([_found("site.com", "ads@site.com")], now=NOW)
        await session.flush()

        rows = (
            (await session.execute(select(ContactModel).where(ContactModel.domain_id == domain_id)))
            .scalars()
            .all()
        )
        assert len(rows) == 1

    async def test_unknown_host_does_not_break_the_batch(self, session: AsyncSession) -> None:
        """Домен мог исчезнуть между отбором и сохранением — пачка из-за
        этого падать не должна."""
        await _donor(session, "site.com")
        results = [_found("site.com", "a@site.com"), _found("нет-такого.com", "b@x.com")]

        saved = await ContactRepository(session).save(results, now=NOW)
        assert saved == 1

    async def test_empty_batch_is_legal(self, session: AsyncSession) -> None:
        assert await ContactRepository(session).save([], now=NOW) == 0
