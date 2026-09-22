"""Учёт расхода и предрасчёт сметы — то, что не даёт выжечь месячный лимит."""

from __future__ import annotations

import pytest
from backend.features.ahrefs.units import (
    COUNTRY_CALL_SHARE,
    MIN_REQUEST_UNITS,
    UNITS_BY_COUNTRY,
    UNITS_DR_SCREEN,
    UnitsCost,
    batch_cost,
    estimate_run,
)


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
    def test_matches_the_measured_projection(self) -> None:
        """Прогон на 100 000 доменов — около 2,95 млн юнитов.

        Было 3,84 млн: страны брались отдельным запросом по каждому
        дошедшему домену, по 55 юнитов. Теперь верхняя страна приезжает
        пакетом вместе с метриками (+10 на домен), и отдельный запрос
        нужен примерно каждому пятому — 29,5 юнита на домен вместо 38.

        Если цифра поедет, значит поехали цены, воронка или доля доменов,
        которым верхней страны не хватает.
        """
        estimate = estimate_run(100_000)
        assert 2_800_000 < estimate.total < 3_100_000
        assert 28 < estimate.per_domain < 31

    def test_countries_no_longer_dominate_the_bill(self) -> None:
        """Раньше страны были самой дорогой статьёй — 70% счёта прогона.

        Ради этого срез и делался: верхняя страна приезжает пакетом, и
        отдельный запрос остаётся примерно каждому пятому. Главной статьёй
        стали метрики, которые платятся пачками и по всем дошедшим.

        Порядок ступеней при этом прежний: самый дорогой ЗАПРОС по-прежнему
        видит меньше всего доменов.
        """
        estimate = estimate_run(10_000)

        assert estimate.metrics > estimate.by_country > estimate.screen

    def test_better_funnel_costs_less(self) -> None:
        """Чем строже пороги, тем дешевле прогон: до стран доходит меньше."""
        loose = estimate_run(10_000, all_pass_share=0.80)
        strict = estimate_run(10_000, all_pass_share=0.20)
        assert strict.total < loose.total

    def test_only_new_domains_are_billed(self) -> None:
        """За домены со свежими данными уже заплачено, в смету
        они не входят — иначе сервис будет пугать ценой повторных прогонов."""
        assert estimate_run(0).total == 0

    @pytest.mark.parametrize("domains", [1, 50, 99, 100, 101, 1000])
    def test_estimate_never_below_the_request_minimum(self, domains: int) -> None:
        """Даже один домен стоит не меньше минимума на запрос."""
        assert estimate_run(domains).screen >= MIN_REQUEST_UNITS

    def test_by_country_has_no_batch_discount(self) -> None:
        """Пакетного аналога у запроса по странам нет — цена линейна.

        Но платят его не все дошедшие, а те, кому верхней страны из пакета
        не хватило: у них наверху не целевая страна, и про целевую мы
        не знаем ничего.
        """
        estimate = estimate_run(1000, all_pass_share=0.5)

        assert estimate.by_country == int(500 * COUNTRY_CALL_SHARE) * UNITS_BY_COUNTRY


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
