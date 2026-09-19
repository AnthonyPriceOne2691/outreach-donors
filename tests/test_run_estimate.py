"""Смета до запуска: арифметика и главный вопрос «помещается ли».

Смета — единственное, что стоит между человеком и потраченным месячным
лимитом. Поэтому проверяется не только счёт, но и то, в какую сторону
она ошибается: занижение здесь означает «кнопка нажалась, а юниты
кончились на середине».
"""

from __future__ import annotations

from backend.features.runs.estimate import RESULTS_PER_PAGE, UNIQUE_SHARE, forecast


class TestArithmetic:
    def test_results_are_keywords_by_depth(self) -> None:
        made = forecast(keywords=10, depth_pages=2, units_left=10**6, units_cap=10**6)

        assert made.expected_results == 10 * 2 * RESULTS_PER_PAGE

    def test_domains_use_the_measured_share(self) -> None:
        """Доля уникальных — замер, а не догадка: 85 доменов на 500
        результатов, то есть 83% схлопывается в дубли."""
        made = forecast(keywords=50, depth_pages=1, units_left=10**6, units_cap=10**6)

        assert made.expected_domains == round(500 * UNIQUE_SHARE)

    def test_no_keywords_costs_nothing(self) -> None:
        made = forecast(keywords=0, depth_pages=1, units_left=10**6, units_cap=10**6)

        assert made.expected_results == 0
        assert made.estimate.total == 0
        assert made.affordable


class TestBudget:
    def test_budget_is_the_smaller_of_cap_and_left(self) -> None:
        """Кап ограничивает нас добровольно, остаток провайдера — жёстко."""
        assert forecast(keywords=1, depth_pages=1, units_left=500, units_cap=9000).budget == 500
        assert forecast(keywords=1, depth_pages=1, units_left=9000, units_cap=500).budget == 500

    def test_does_not_fit_and_says_how_much_is_missing(self) -> None:
        made = forecast(keywords=200, depth_pages=3, units_left=100, units_cap=100)

        assert not made.affordable
        assert made.shortfall == made.estimate.total - 100

    def test_fits_exactly(self) -> None:
        tight = forecast(keywords=5, depth_pages=1, units_left=10**6, units_cap=10**6)
        exact = forecast(
            keywords=5,
            depth_pages=1,
            units_left=tight.estimate.total,
            units_cap=tight.estimate.total,
        )

        assert exact.affordable
        assert exact.shortfall == 0

    def test_estimate_assumes_the_worst_case(self) -> None:
        """Все домены считаются новыми. За свежие второй раз не платят,
        поэтому факт обычно ниже — и это правильная сторона ошибки."""
        made = forecast(keywords=20, depth_pages=1, units_left=10**6, units_cap=10**6)

        assert made.estimate.domains == made.expected_domains
