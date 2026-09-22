"""Расход на выдачу: доллары доезжают до журнала, кап месячный.

Три правила, которых до этого среза не было ни одного:

* выдача — вторая статья расхода после Ahrefs, и платится она деньгами.
  Провайдер называет цену в каждом ответе, а журнал её не видел;
* строка расхода пишется один раз: продолжение прогона по сохранённой
  выдаче ничего не покупает, и вторая строка удвоила бы счёт;
* кап месячный. Не вычитать из него потраченное значит называть месячным
  то, что ограничивает один прогон.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from backend.features.core import usage
from backend.features.core.domain import UsageProvider
from backend.features.core.models.ops import UsageRecordModel
from backend.features.runs.planning import Candidates, gather_candidates
from backend.features.runs.spending import ahrefs_spent_this_month, cap_left
from backend.features.serp.protocol import SerpResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


class PaidSerp:
    """Источник выдачи, который берёт деньги и говорит сколько."""

    name = "paid"

    def __init__(self, *, price: float = 0.12) -> None:
        self.spent = 0.0
        self._price = price

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        self.spent += self._price
        return {kw: [SerpResult(1, "https://donor.example.test/a")] for kw in keywords}


class TestTheCostOfSearch:
    async def test_cost_travels_with_the_candidates(self) -> None:
        provider = PaidSerp(price=0.12)

        candidates = await gather_candidates(provider, ["crm"], "us")

        assert candidates.cost_usd == pytest.approx(0.12)

    async def test_only_this_run_is_counted(self) -> None:
        """Адаптер живёт дольше прогона, и его накопленный расход — это
        расход всех прогонов сразу. Считается разница, а не поле."""
        provider = PaidSerp(price=0.12)
        await gather_candidates(provider, ["crm"], "us")

        second = await gather_candidates(provider, ["seo"], "us")

        assert second.cost_usd == pytest.approx(0.12)
        assert provider.spent == pytest.approx(0.24)

    def test_restored_search_costs_nothing(self) -> None:
        """За неё уже заплачено и уже записано: продолжение прогона
        не должно удваивать счёт."""
        bought = Candidates(
            hosts=["donor.example.test"],
            keywords=1,
            results=1,
            empty_keywords=[],
            dropped=0,
            cost_usd=0.12,
        )

        restored = Candidates.restored(bought.as_dict())

        assert bought.as_dict()["cost_usd"] == pytest.approx(0.12)
        assert restored.cost_usd == 0.0


class TestTheJournal:
    async def test_money_reaches_the_row(self, session: AsyncSession) -> None:
        usage.record(session, operation="serp_search", amount_usd=0.36)
        await session.flush()

        row = (await session.execute(select(UsageRecordModel))).scalars().one()
        assert row.provider is UsageProvider.SERP
        assert row.amount_usd == Decimal("0.36")
        # Юнитов у этого провайдера нет вовсе: пустое поле означает
        # «в этой валюте не платили», а не ноль.
        assert row.units is None

    async def test_units_and_money_do_not_mix(self, session: AsyncSession) -> None:
        usage.record(session, operation="batch_metrics", units=2)
        usage.record(session, operation="serp_search", amount_usd=0.36)
        await session.flush()

        rows = (await session.execute(select(UsageRecordModel))).scalars().all()
        by_provider = {row.provider: (row.units, row.amount_usd) for row in rows}
        assert by_provider[UsageProvider.AHREFS] == (2, None)
        assert by_provider[UsageProvider.SERP] == (None, Decimal("0.36"))


class TestTheMonthlyCap:
    async def test_cap_shrinks_by_what_we_spent(self, session: AsyncSession) -> None:
        usage.record(session, operation="batch_metrics", units=6416)
        await session.flush()

        assert await ahrefs_spent_this_month(session) == 6416
        assert await cap_left(session, cap=100_000) == 93_584

    async def test_cap_never_goes_below_zero(self, session: AsyncSession) -> None:
        usage.record(session, operation="batch_metrics", units=120_000)
        await session.flush()

        assert await cap_left(session, cap=100_000) == 0

    async def test_money_of_the_search_does_not_eat_units(self, session: AsyncSession) -> None:
        """Счета разные: доллары выдачи не уменьшают кап юнитов."""
        usage.record(session, operation="serp_search", amount_usd=42)
        await session.flush()

        assert await cap_left(session, cap=100_000) == 100_000
