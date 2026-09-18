"""Правило региона страна в топ-N ИЛИ доля не меньше порога."""

from __future__ import annotations

import pytest
from backend.features.donors.geo import (
    assert_settings_allow_limited_fetch,
    build_breakdown,
    check_geo,
)

# Замер techcrunch.com, 18.09.2026: весь органический трафик и топ-5 стран.
TOTAL = 1_140_802
ROWS: list[dict[str, object]] = [
    {"country": "us", "org_traffic": 748_538},
    {"country": "in", "org_traffic": 173_147},
    {"country": "gb", "org_traffic": 24_784},
    {"country": "br", "org_traffic": 18_838},
    {"country": "ca", "org_traffic": 14_497},
]


def test_share_is_taken_from_total_traffic_not_from_rows() -> None:
    """Топ-5 покрывают лишь 86% трафика. Делить на их сумму — значит завысить
    каждую долю на шестую часть и пропустить домены, не прошедшие порог."""
    breakdown = build_breakdown(ROWS, TOTAL)
    us = breakdown[0]
    assert us.share == pytest.approx(0.656, abs=0.001)

    inflated = us.org_traffic / sum(r["org_traffic"] for r in ROWS)  # type: ignore[operator]
    assert inflated == pytest.approx(0.764, abs=0.001)


def test_target_in_top_five_passes() -> None:
    verdict = check_geo("us", build_breakdown(ROWS, TOTAL))
    assert verdict.passed
    assert "1-м месте" in verdict.reason


def test_target_outside_top_five_fails() -> None:
    verdict = check_geo("de", build_breakdown(ROWS, TOTAL))
    assert not verdict.passed
    assert "не входит" in verdict.reason


def test_low_position_but_big_share_passes_on_narrow_top() -> None:
    """Вторая половина правила  включается, когда топ узкий: при топ-2
    страна на третьем месте с долей 25% всё равно подходит."""
    rows: list[dict[str, object]] = [
        {"country": "us", "org_traffic": 400},
        {"country": "gb", "org_traffic": 300},
        {"country": "de", "org_traffic": 250},
    ]
    verdict = check_geo("de", build_breakdown(rows, 1000), top_n=2, min_share=0.20)
    assert verdict.passed
    assert "25% трафика" in verdict.reason


def test_no_country_data_is_not_a_rejection() -> None:
    """Пустой ответ — это «не проверен», а не «не подходит»."""
    assert build_breakdown([], TOTAL) == []
    assert build_breakdown(ROWS, 0) == []
    verdict = check_geo("us", [])
    assert not verdict.passed
    assert verdict.reason == "нет данных по странам"


def test_limit_five_is_enough_at_default_settings() -> None:
    """Страна с долей >= 20% не может быть за топ-5: пять стран выше неё дали бы
    больше 100% трафика. Поэтому ответа с limit=5 хватает."""
    assert_settings_allow_limited_fetch(top_n=5, min_share=0.20)


def test_lowering_share_threshold_breaks_limited_fetch() -> None:
    """А вот при пороге 10% страна с достаточной долей может оказаться шестой —
    и мы её не увидим. Настройка должна падать, а не отсеивать молча."""
    with pytest.raises(ValueError, match="1650 юнитов"):
        assert_settings_allow_limited_fetch(top_n=5, min_share=0.10)


def test_unrecognised_country_rows_are_reported(caplog: pytest.LogCaptureFixture) -> None:
    """Если провайдер переименует поля, непрочитанными окажутся все строки,
    и домен станет «нет данных по странам» без всякой причины. Такое должно
    быть видно в логе, а не выглядеть как отсутствие трафика."""
    rows: list[dict[str, object]] = [{"iso": "us", "traffic": 100}, {"iso": "gb", "traffic": 50}]

    with caplog.at_level("WARNING"):
        breakdown = build_breakdown(rows, 1000)

    assert breakdown == []
    assert "не разобрана" in caplog.text
