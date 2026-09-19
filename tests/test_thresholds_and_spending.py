"""Пороги и расход: предпросмотр последствий и учёт трат.

Предпросмотр проверяется тем же правилом, что и отбор, — здесь это
не формальность: второй экземпляр правила разошёлся бы с настоящим,
и экран показывал бы последствия, которых не будет.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.features.core.domain import DonorStatus, UsageProvider
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.donors.verdict import Thresholds
from backend.features.runs.spending import SpendingRepository
from backend.features.runs.thresholds import ThresholdsRepository, consequences
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime.now(UTC)


def _donor(
    *,
    status: DonorStatus,
    dr: int | None,
    traffic: int | None = 5000,
    refdomains: int = 500,
    keywords: int = 900,
    price: Decimal | None = None,
) -> DonorModel:
    return DonorModel(
        domain_id=0,
        status=status,
        dr=dr,
        org_traffic=traffic,
        metrics=(None if dr is None else {"refdomains": refdomains, "org_keywords": keywords}),
        last_price_usd=price,
    )


class TestConsequences:
    def test_raising_dr_drops_donors(self) -> None:
        donors = [
            _donor(status=DonorStatus.SUITABLE, dr=30),
            _donor(status=DonorStatus.SUITABLE, dr=15),
        ]

        result = consequences(donors, Thresholds(25, 500, 100, 300))

        assert result.suitable_now == 2
        assert result.suitable_after == 1
        assert result.falls_out == 1

    def test_counts_those_with_price_separately(self) -> None:
        """За донора с полученной ценой заплачено не только юнитами,
        но и письмом. Это надо знать до правки порога, а не после."""
        donors = [_donor(status=DonorStatus.SUITABLE, dr=15, price=Decimal("250"))]

        result = consequences(donors, Thresholds(25, 500, 100, 300))

        assert result.falls_out == 1
        assert result.falls_out_with_price == 1

    def test_lowering_threshold_brings_donors_back(self) -> None:
        """Порог двигают в обе стороны: «выпадет 340» без «вернётся 12» —
        половина ответа."""
        donors = [_donor(status=DonorStatus.UNSUITABLE, dr=15)]

        result = consequences(donors, Thresholds(10, 500, 100, 300))

        assert result.comes_back == 1
        assert result.suitable_after == 1

    def test_unchecked_stay_unchecked(self) -> None:
        """Домен без метрик не отсеивается новым порогом — его вердикт
        не изменится, потому что его нет."""
        donors = [_donor(status=DonorStatus.UNCHECKED, dr=None)]

        result = consequences(donors, Thresholds(90, 10**6, 10**6, 10**6))

        assert result.unchecked == 1
        assert result.checked == 0
        assert result.falls_out == 0

    def test_preview_uses_the_same_rule_as_selection(self) -> None:
        """Порог по трафику, а не только по DR: предпросмотр зовёт то же
        `check_metrics`, что и отбор, поэтому знает про все четыре."""
        donors = [_donor(status=DonorStatus.SUITABLE, dr=50, traffic=100)]

        result = consequences(donors, Thresholds(20, 500, 100, 300))

        assert result.falls_out == 1


class TestVersions:
    async def test_saving_creates_a_new_version(self, session: AsyncSession) -> None:
        repository = ThresholdsRepository(session)

        first = await repository.save(Thresholds(20, 500, 100, 300), author="ivan@site.com")
        second = await repository.save(Thresholds(25, 500, 100, 300), author="ivan@site.com")
        await session.commit()

        assert first.version == 1
        assert second.version == 2
        current = await repository.current()
        assert current is not None
        assert current.min_dr == 25

    async def test_other_settings_carry_over(self, session: AsyncSession) -> None:
        """Остальные настройки переносятся из текущей версии, а не берутся
        из конфига: подмешать умолчания в десятую версию значило бы молча
        откатить чужую правку."""
        repository = ThresholdsRepository(session)
        first = await repository.save(Thresholds(20, 500, 100, 300), author="ivan@site.com")
        first.units_cap = 12345
        await session.flush()

        second = await repository.save(Thresholds(25, 500, 100, 300), author="ivan@site.com")
        await session.commit()

        assert second.units_cap == 12345


class TestSpending:
    async def test_groups_by_provider_and_operation(self, session: AsyncSession) -> None:
        session.add_all(
            [
                UsageRecordModel(
                    system="outreach",
                    provider=UsageProvider.AHREFS,
                    operation="batch_metrics",
                    units=200,
                ),
                UsageRecordModel(
                    system="outreach",
                    provider=UsageProvider.AHREFS,
                    operation="batch_metrics",
                    units=300,
                ),
                UsageRecordModel(
                    system="outreach",
                    provider=UsageProvider.SERP,
                    operation="serp_task",
                    amount_usd=Decimal("0.12"),
                ),
            ]
        )
        await session.commit()

        spending = await SpendingRepository(session).since_month_start()

        by_operation = {article.operation: article for article in spending.articles}
        assert by_operation["batch_metrics"].units == 500
        assert by_operation["batch_metrics"].calls == 2
        assert by_operation["serp_task"].amount_usd == Decimal("0.1200")

    async def test_last_month_is_not_counted(self, session: AsyncSession) -> None:
        """Лимиты провайдеров месячные, и расход считается с первого числа:
        «за последние 30 дней» отвечало бы на другой вопрос."""
        old = UsageRecordModel(
            system="outreach",
            provider=UsageProvider.AHREFS,
            operation="batch_metrics",
            units=999,
        )
        session.add(old)
        await session.flush()
        old.created_at = NOW.replace(day=1) - timedelta(days=5)
        await session.commit()

        spending = await SpendingRepository(session).since_month_start()

        assert all(article.units != 999 for article in spending.articles)
