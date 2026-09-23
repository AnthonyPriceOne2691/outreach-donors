"""Учёт расхода и предрасчёт сметы — то, что не даёт выжечь месячный лимит."""

from __future__ import annotations

import pytest
from backend.features.ahrefs.units import (
    COUNTRY_CALL_SHARE,
    MIN_REQUEST_UNITS,
    UNITS_BY_COUNTRY,
    UNITS_DR_SCREEN,
    UNITS_FULL_METRICS,
    UnitsCost,
    batch_cost,
    estimate_run,
)
from backend.features.runs.repository import country_share_from


class TestUnitsCost:
    def test_reads_all_three_headers(self) -> None:
        cost = UnitsCost.from_headers(
            {
                "x-api-units-cost-total-actual": "55",
                "x-api-units-cost-total": "55",
                "x-api-units-cost-row": "11",
            }
        )
        assert cost.actual == 55
        assert cost.per_row == 11
        assert cost.billable == 55

    def test_missing_headers_give_none_not_zero(self) -> None:
        """Ноль означал бы «бесплатно». Отсутствие означает «неизвестно»,
        и это разные вещи для учёта."""
        cost = UnitsCost.from_headers({})
        assert cost.actual is None
        assert cost.estimated is None

    def test_garbage_headers_do_not_crash(self) -> None:
        cost = UnitsCost.from_headers({"x-api-units-cost-total-actual": "не число"})
        assert cost.actual is None

    def test_falls_back_to_estimate_when_actual_is_absent(self) -> None:
        cost = UnitsCost.from_headers({"x-api-units-cost-total": "42"})
        assert cost.billable == 42


class TestBatchCost:
    def test_minimum_applies_to_small_batches(self) -> None:
        """Замер: пачка из 10 доменов на просеве стоит 50 юнитов, а не 20."""
        assert batch_cost(10, UNITS_DR_SCREEN) == MIN_REQUEST_UNITS
        assert batch_cost(10, UNITS_DR_SCREEN) / 10 == 5.0

    def test_full_batch_escapes_the_minimum(self) -> None:
        """А пачка из 100 — ровно 2 юнита на домен, как и замерено."""
        assert batch_cost(100, UNITS_DR_SCREEN) == 200
        assert batch_cost(100, UNITS_DR_SCREEN) / 100 == 2.0

    def test_empty_batch_is_free(self) -> None:
        assert batch_cost(0, UNITS_DR_SCREEN) == 0


class TestRunEstimate:
    def test_estimate_is_an_upper_bound_on_metrics(self) -> None:
        """Полные метрики считаются на все новые домены: просев по DR
        отсекает 0–15%, а заниженная смета значит, что кап не держит трату.
        Было 83% — и смета занижала трату в 14 прогонах из 14, до +132%."""
        estimate = estimate_run(1000, country_share=0.0)

        assert estimate.metrics == 10 * 100 * UNITS_FULL_METRICS
        assert estimate.by_country == 0

    def test_battle_run_is_not_underestimated(self) -> None:
        """Прогон 23.09.2026: 508 новых доменов США, списано 16 998. Доля
        запросов по странам у США по истории — около 8%."""
        estimate = estimate_run(508, country_share=0.079)

        assert estimate.total >= 16_998
        assert estimate.total < 16_998 * 1.1

    def test_more_country_calls_cost_more(self) -> None:
        """Страна, где верхняя страна пакета редко совпадает с целевой,
        дороже: у ЮАР и Австрии запрос нужен 60%, у США 8%."""
        us = estimate_run(10_000, country_share=0.08)
        za = estimate_run(10_000, country_share=0.6)
        assert za.total > us.total

    def test_only_new_domains_are_billed(self) -> None:
        """За домены со свежими данными уже заплачено, в смету
        они не входят — иначе сервис будет пугать ценой повторных прогонов."""
        assert estimate_run(0).total == 0

    @pytest.mark.parametrize("domains", [1, 50, 99, 100, 101, 1000])
    def test_estimate_never_below_the_request_minimum(self, domains: int) -> None:
        """Даже один домен стоит не меньше минимума на запрос."""
        assert estimate_run(domains).screen >= MIN_REQUEST_UNITS

    def test_by_country_has_no_batch_discount(self) -> None:
        """Пакетного аналога у запроса по странам нет — цена линейна."""
        estimate = estimate_run(1000, country_share=0.5)

        assert estimate.by_country == 500 * UNITS_BY_COUNTRY

    def test_without_history_the_share_errs_high(self) -> None:
        """Без своей истории доля — с запасом: занизить значит пропустить
        трату мимо капа."""
        assert COUNTRY_CALL_SHARE >= 0.5


class TestCachedResponses:
    """Ahrefs кэширует ответы у себя и за повторный запрос берёт ноль.

    Ловушка в том, что «бесплатно» и «неизвестно» выглядят одинаково, если
    смотреть только на billable. Моки в остальных тестах всегда возвращают
    цену, поэтому этот случай они не видят — он пришёл с боевого прогона.
    """

    def test_zero_actual_with_estimate_is_a_free_request(self) -> None:
        cost = UnitsCost.from_headers(
            {"x-api-units-cost-total-actual": "0", "x-api-units-cost-total": "55"}
        )
        assert cost.known
        assert cost.was_free
        assert cost.billable == 0

    def test_no_headers_is_unknown_not_free(self) -> None:
        """Разница принципиальная: бесплатный запрос записывается в журнал,
        неизвестный — повод для предупреждения."""
        cost = UnitsCost.from_headers({})
        assert not cost.known
        assert not cost.was_free

    def test_paid_request_is_not_free(self) -> None:
        cost = UnitsCost.from_headers(
            {"x-api-units-cost-total-actual": "55", "x-api-units-cost-total": "55"}
        )
        assert cost.known
        assert not cost.was_free
        assert cost.billable == 55


class TestCountryShareFromHistory:
    """Доля запросов по странам — из своих прогонов, по своей стране.

    Бэктест на 14 прогонах 22–23.09.2026: одна константа занижала смету
    в 14 из 14 (до +132%), своя страна со свежим окном — в 2 из 14 (до +6%).
    """

    @staticmethod
    def _run(checked: int, calls: int) -> dict[str, object]:
        return {"checked_now": checked, "units_by_operation": {"by_country": calls * 55}}

    def test_own_country_wins(self) -> None:
        history = [("za", self._run(100, 60)), ("us", self._run(500, 40))]

        assert country_share_from(history, "US") == pytest.approx(0.08)
        assert country_share_from(history, "za") == pytest.approx(0.6)

    def test_other_countries_do_not_transfer(self) -> None:
        """Доля ходит от 3% до 88% по странам: средняя по чужим для новой
        страны занижала смету. Без своей истории — умолчание с запасом."""
        history = [("us", self._run(500, 40)), ("vn", self._run(300, 10))]

        assert country_share_from(history, "fr") == COUNTRY_CALL_SHARE

    def test_too_little_own_history_is_noise(self) -> None:
        assert country_share_from([("mx", self._run(5, 5))], "mx") == COUNTRY_CALL_SHARE

    def test_fresh_window_washes_out_old_logic(self) -> None:
        """Прогоны до пакетной верхней страны звали запрос на каждый домен.
        Свежее окно в 500 доменов вымывает их без дат и исключений."""
        newest_first = [("us", self._run(508, 40)), ("us", self._run(83, 80))]

        assert country_share_from(newest_first, "us") == pytest.approx(40 / 508)

    def test_runs_without_checks_are_skipped(self) -> None:
        history = [("us", {"checked_now": 0}), ("us", self._run(100, 10))]

        assert country_share_from(history, "us") == pytest.approx(0.1)
