"""Вердикт по донору: пороги , «не проверен» , сроки годности ."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import DonorStatus
from backend.features.donors.verdict import (
    Metrics,
    Thresholds,
    check_dr,
    check_metrics,
    is_fresh,
)

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
GOOD = Metrics(dr=35, org_traffic=5000, refdomains=400, org_keywords=1200)


def test_good_domain_passes_both_stages() -> None:
    assert check_dr(GOOD.dr, T) is None
    assert check_metrics(GOOD, T) is None


def test_first_stage_rejects_on_dr_alone() -> None:
    """Смысл первой ступени — отсечь домен за 2 юнита, не платя 18 за остальные."""
    verdict = check_dr(15, T)
    assert verdict is not None
    assert verdict.status is DonorStatus.UNSUITABLE
    assert verdict.reason == "DR 15 ниже 20"


@pytest.mark.parametrize(
    ("metrics", "expected_reason"),
    [
        (
            Metrics(dr=35, org_traffic=100, refdomains=400, org_keywords=1200),
            "органический трафик 100 ниже 500",
        ),
        (Metrics(dr=35, org_traffic=5000, refdomains=400, org_keywords=50), "ключей 50 ниже 300"),
        (
            Metrics(dr=35, org_traffic=5000, refdomains=10, org_keywords=1200),
            "реф. доменов 10 ниже 100",
        ),
    ],
)
def test_rejection_names_the_exact_threshold(metrics: Metrics, expected_reason: str) -> None:
    """Отчёт прогона должен говорить, по какому именно порогу домен отсеян,
    иначе калибровать пороги не по чему."""
    verdict = check_metrics(metrics, T)
    assert verdict is not None
    assert verdict.status is DonorStatus.UNSUITABLE
    assert verdict.reason == expected_reason


@pytest.mark.parametrize(
    "metrics",
    [
        Metrics(dr=None),
        Metrics(dr=35, org_traffic=None),
        Metrics(dr=35, org_traffic=5000, org_keywords=None),
    ],
)
def test_missing_data_is_unchecked_not_rejected(metrics: Metrics) -> None:
    """отсутствие данных — повод добрать позже, а не закрыть домен.
    Если спутать, база будет копить ложные отказы и терять доноров."""
    verdict = check_metrics(metrics, T)
    assert verdict is not None
    assert verdict.status is DonorStatus.UNCHECKED
    assert not verdict.is_rejection


def test_zero_is_a_rejection_but_none_is_not() -> None:
    """Ноль — это измеренный ноль, отсутствие — это неизвестность."""
    zero = check_metrics(Metrics(dr=35, org_traffic=0, refdomains=400, org_keywords=1200), T)
    assert zero is not None
    assert zero.status is DonorStatus.UNSUITABLE

    unknown = check_metrics(Metrics(dr=35, org_traffic=None), T)
    assert unknown is not None
    assert unknown.status is DonorStatus.UNCHECKED


def test_threshold_boundary_is_inclusive() -> None:
    """«DR >= 20» значит, что ровно 20 проходит."""
    assert check_dr(20, T) is None
    assert check_dr(19.9, T) is not None


class TestFreshness:
    """Срок годности: пока не истёк, повторно за данные не платим."""

    NOW = datetime(2026, 9, 18, tzinfo=UTC)

    def test_fresh_metrics_skip_the_api(self) -> None:
        assert is_fresh(self.NOW - timedelta(days=89), 90, now=self.NOW)

    def test_expired_metrics_require_refresh(self) -> None:
        assert not is_fresh(self.NOW - timedelta(days=91), 90, now=self.NOW)

    def test_never_fetched_is_not_fresh(self) -> None:
        assert not is_fresh(None, 90, now=self.NOW)

    def test_price_has_its_own_longer_ttl(self) -> None:
        """Цена живёт 150 дней против 90 у метрик: перезапрашивать её дороже —
        это письмо донору и ожидание ответа, а не вызов API."""
        stale_for_metrics = self.NOW - timedelta(days=100)
        assert not is_fresh(stale_for_metrics, 90, now=self.NOW)
        assert is_fresh(stale_for_metrics, 150, now=self.NOW)
