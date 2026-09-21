"""Бюджет прогона учитывает то, что уже обещали потратить соседи.

Остаток у Ahrefs — правда о прошлом: он показывает потраченное, а не
обещанное. Два прогона, стартовавшие рядом, оба видят один и тот же
остаток и оба планируются под него целиком. Вместе они выбирают вдвое
больше, чем есть, и узнаётся это отказом API посреди платной работы.
Юниты не возвращаются — поэтому проверяется вычитание до планирования.
"""

from __future__ import annotations

import pytest
from backend.features.ahrefs.units import UnitsCost
from backend.features.core.domain import RunStatus, Stage
from backend.features.donors.verdict import Thresholds
from backend.features.runs.budget import units_left
from backend.features.runs.repository import RunRepository
from sqlalchemy.ext.asyncio import AsyncSession

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)


async def _repo(session: AsyncSession) -> RunRepository:
    return RunRepository(session)


async def _run(
    repo: RunRepository, *, status: RunStatus, estimate: int | None, spent: int = 0
) -> int:
    settings = await repo.create_settings(
        T,
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = await repo.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=["crm"],
        country="us",
        estimated_units=estimate,
        status=status,
    )
    if spent:
        await repo.record_usage(
            run_id=run.id,
            operation="batch_metrics",
            cost=UnitsCost(actual=spent, estimated=spent, per_row=None),
        )
    await repo.session_flush()
    return int(run.id)


class TestClaimedUnits:
    async def test_active_run_holds_its_unspent_estimate(self, session: AsyncSession) -> None:
        repo = await _repo(session)
        await _run(repo, status=RunStatus.RUNNING, estimate=10_000, spent=3_000)
        assert await repo.claimed_units() == 7_000

    async def test_closed_run_holds_nothing(self, session: AsyncSession) -> None:
        """За закрытый прогон говорит журнал расхода, а не смета."""
        repo = await _repo(session)
        await _run(repo, status=RunStatus.DONE, estimate=10_000, spent=3_000)
        assert await repo.claimed_units() == 0

    async def test_overspent_run_does_not_give_budget_back(self, session: AsyncSession) -> None:
        """Перерасход соседа не увеличивает наш бюджет — max(0, …), не минус."""
        repo = await _repo(session)
        await _run(repo, status=RunStatus.RUNNING, estimate=1_000, spent=5_000)
        assert await repo.claimed_units() == 0

    async def test_run_without_estimate_holds_nothing_yet(self, session: AsyncSession) -> None:
        """Пока смета не объявлена, держать нечего: числа ещё нет."""
        repo = await _repo(session)
        await _run(repo, status=RunStatus.ESTIMATING, estimate=None)
        assert await repo.claimed_units() == 0

    async def test_claims_add_up_across_runs(self, session: AsyncSession) -> None:
        repo = await _repo(session)
        await _run(repo, status=RunStatus.RUNNING, estimate=10_000, spent=1_000)
        await _run(repo, status=RunStatus.QUEUED, estimate=5_000)
        assert await repo.claimed_units() == 14_000

    async def test_own_claim_can_be_excluded(self, session: AsyncSession) -> None:
        """Прогон не должен вычитать сам себя, когда спрашивает про свой бюджет."""
        repo = await _repo(session)
        mine = await _run(repo, status=RunStatus.RUNNING, estimate=10_000)
        await _run(repo, status=RunStatus.RUNNING, estimate=4_000)
        assert await repo.claimed_units(exclude_run_id=mine) == 4_000


class _Client:
    def __init__(self, available: int) -> None:
        self._available = available

    async def limits_and_usage(self) -> dict[str, int]:
        return {
            "units_limit_workspace": 1_000_000,
            "units_usage_workspace": 1_000_000 - self._available,
            "units_limit_api_key": 1_000_000,
            "units_usage_api_key": 1_000_000 - self._available,
        }


class TestBudgetSubtractsClaims:
    async def test_claims_are_subtracted_before_planning(self) -> None:
        assert await units_left(_Client(100_000), claimed=30_000) == 70_000  # type: ignore[arg-type]

    async def test_budget_never_goes_negative(self) -> None:
        """Удержано больше, чем осталось: бюджет ноль, а не минус."""
        assert await units_left(_Client(10_000), claimed=50_000) == 0  # type: ignore[arg-type]

    async def test_cap_still_applies_on_top(self) -> None:
        assert await units_left(_Client(100_000), cap=20_000, claimed=30_000) == 20_000  # type: ignore[arg-type]

    async def test_without_claims_behaviour_is_unchanged(self) -> None:
        """Прежний вызов без удержаний обязан отвечать как раньше."""
        assert await units_left(_Client(100_000)) == 100_000  # type: ignore[arg-type]


@pytest.mark.parametrize("claimed", [1, 999_999])
async def test_any_claim_reduces_the_budget(claimed: int) -> None:
    """Проверка обязана уметь падать: любое удержание видно в ответе."""
    assert await units_left(_Client(1_000_000), claimed=claimed) == 1_000_000 - claimed  # type: ignore[arg-type]
