"""Репозиторий доноров. Тесты идут на настоящей базе: проверять запрос
к базе на подделке значит проверять подделку."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.collect import DomainResult
from backend.features.donors.geo import CountryShare, GeoVerdict
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Metrics
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 9, 18, tzinfo=UTC)


def _suitable(host: str) -> DomainResult:
    breakdown = [CountryShare("us", 8000, 0.8), CountryShare("gb", 1000, 0.1)]
    return DomainResult(
        host=host,
        status=DonorStatus.SUITABLE,
        reason="us на 1-м месте по трафику (80%)",
        metrics=Metrics(dr=40, org_traffic=10_000, refdomains=500, org_keywords=2000),
        geo=GeoVerdict(True, "us на 1-м месте", "us", breakdown),
        raw={"domain_rating": 40, "org_traffic": 10_000},
    )


def _unchecked(host: str) -> DomainResult:
    return DomainResult(host, DonorStatus.UNCHECKED, "Ahrefs не знает домен", Metrics())


class TestEnsureDomains:
    async def test_creates_and_returns_ids(self, session: AsyncSession) -> None:
        repo = DonorRepository(session)
        ids = await repo.ensure_domains(["a.com", "b.com"])
        assert set(ids) == {"a.com", "b.com"}

    async def test_is_idempotent(self, session: AsyncSession) -> None:
        """Два прогона по пересекающимся ключам не должны падать друг о друга."""
        repo = DonorRepository(session)
        first = await repo.ensure_domains(["a.com"])
        second = await repo.ensure_domains(["a.com", "b.com"])
        assert first["a.com"] == second["a.com"]

    async def test_duplicates_in_one_call_collapse(self, session: AsyncSession) -> None:
        repo = DonorRepository(session)
        ids = await repo.ensure_domains(["a.com", "a.com", "a.com"])
        assert len(ids) == 1


class TestFreshness:
    """Главный запрос сервиса: за какие домены уже заплачено."""

    async def test_just_saved_domain_is_fresh(self, session: AsyncSession) -> None:
        repo = DonorRepository(session)
        await repo.save_results([_suitable("a.com")], now=NOW)

        assert await repo.fresh_hosts(["a.com"], now=NOW) == {"a.com"}

    async def test_expired_domain_is_not_fresh(self, session: AsyncSession) -> None:
        repo = DonorRepository(session)
        await repo.save_results([_suitable("a.com")], now=NOW - timedelta(days=91))

        assert await repo.fresh_hosts(["a.com"], ttl_days=90, now=NOW) == set()

    async def test_unknown_domain_is_not_fresh(self, session: AsyncSession) -> None:
        repo = DonorRepository(session)
        assert await repo.fresh_hosts(["never-seen.com"], now=NOW) == set()

    async def test_domain_ahrefs_does_not_know_is_still_fresh(self, session: AsyncSession) -> None:
        """и деньги: домен вне индекса Ahrefs получает отметку времени,
        иначе каждый прогон будет жечь на нём юниты заново."""
        repo = DonorRepository(session)
        await repo.save_results([_unchecked("ghost.com")], now=NOW)

        assert await repo.fresh_hosts(["ghost.com"], now=NOW) == {"ghost.com"}

    async def test_empty_input_touches_no_database(self, session: AsyncSession) -> None:
        assert await DonorRepository(session).fresh_hosts([]) == set()


class TestSaveResults:
    async def test_stores_metrics_geo_and_verdict(self, session: AsyncSession) -> None:
        repo = DonorRepository(session)
        await repo.save_results([_suitable("a.com")], now=NOW)

        donor = (await session.execute(select(DonorModel))).scalar_one()
        assert donor.status is DonorStatus.SUITABLE
        assert donor.dr == 40
        assert donor.org_traffic == 10_000
        assert donor.geo == "us"
        assert donor.geo_top_share == pytest.approx(0.8)
        assert [row["country"] for row in donor.geo_breakdown or []] == ["us", "gb"]

    async def test_reject_reason_is_kept_only_for_rejections(self, session: AsyncSession) -> None:
        """У подходящего донора «причина» объясняла бы успех и путала отчёт."""
        repo = DonorRepository(session)
        rejected = DomainResult("bad.com", DonorStatus.UNSUITABLE, "DR 5 ниже 20", Metrics(dr=5))
        await repo.save_results([_suitable("good.com"), rejected], now=NOW)

        rows = (
            await session.execute(
                select(DomainModel.host, DonorModel.reject_reason).join(
                    DonorModel, DonorModel.domain_id == DomainModel.id
                )
            )
        ).all()
        by_host = dict(rows)
        assert by_host["bad.com"] == "DR 5 ниже 20"
        assert by_host["good.com"] is None

    async def test_second_run_updates_instead_of_duplicating(self, session: AsyncSession) -> None:
        """Донор — роль домена, а не его копия: повторный сбор обновляет запись."""
        repo = DonorRepository(session)
        await repo.save_results([_suitable("a.com")], now=NOW - timedelta(days=100))

        changed = _suitable("a.com")
        changed.metrics = Metrics(dr=55, org_traffic=90_000)
        await repo.save_results([changed], now=NOW)

        donors = (await session.execute(select(DonorModel))).scalars().all()
        assert len(donors) == 1
        assert donors[0].dr == 55

    async def test_geo_mark_is_absent_when_countries_were_not_asked(
        self, session: AsyncSession
    ) -> None:
        """Домен, отсеянный на первой ступени, до стран не доходил — и отметка
        по ним не ставится. Иначе повторный прогон решит, что гео уже собрано."""
        repo = DonorRepository(session)
        early = DomainResult("weak.com", DonorStatus.UNSUITABLE, "DR 5 ниже 20", Metrics(dr=5))
        await repo.save_results([early], now=NOW)

        donor = (await session.execute(select(DonorModel))).scalar_one()
        assert donor.geo_refreshed_at is None
        assert donor.metrics_refreshed_at is not None

    async def test_empty_batch_is_a_no_op(self, session: AsyncSession) -> None:
        assert await DonorRepository(session).save_results([]) == 0
