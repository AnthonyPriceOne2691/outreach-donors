"""Границы доли Клоппера — Пирсона: односторонние 95 %, точно, на чистом питоне.

Проверяется то, чем граница может соврать: ноль событий даёт ноль (нормальное приближение),
корень хвоста найден неточно, верхняя и нижняя не сходятся зеркально, счёт «сколько ещё
решений» разошёлся с самой границей на шаг.
"""

from __future__ import annotations

import pytest
from backend.shared import stats


class TestKnownValues:
    """Значения, которые сверяются руками: «правило трёх» на нуле и закрытые формы."""

    @pytest.mark.parametrize(
        ("trials", "upper"),
        [(30, 0.09503), (39, 0.07394), (29, 0.09815), (200, 0.01487)],
    )
    def test_zero_events_is_not_a_zero_share(self, trials: int, upper: float) -> None:
        """0 из 30 — не «0 %», а «не больше 9,5 %»: `1 − 0,05^(1/n)`."""
        assert stats.upper_bound(0, trials) == pytest.approx(upper, abs=1e-5)
        assert stats.lower_bound(0, trials) == 0.0

    def test_one_of_three(self) -> None:
        """1 из 3: доля где-то от 1,7 % до 86 % — три решения ничего не говорят."""
        assert stats.upper_bound(1, 3) == pytest.approx(0.86465, abs=1e-5)
        assert stats.lower_bound(1, 3) == pytest.approx(1 - 0.95 ** (1 / 3), abs=1e-12)

    def test_all_events(self) -> None:
        assert stats.upper_bound(5, 5) == 1.0
        assert stats.lower_bound(5, 5) == pytest.approx(0.05 ** (1 / 5), abs=1e-12)

    def test_no_trials_says_nothing(self) -> None:
        assert (stats.lower_bound(0, 0), stats.upper_bound(0, 0)) == (0.0, 1.0)


class TestTheRoot:
    @pytest.mark.parametrize(("events", "trials"), [(1, 3), (3, 200), (7, 234), (43, 155)])
    def test_the_upper_bound_is_the_root_of_the_tail(self, events: int, trials: int) -> None:
        """Верхняя граница — та доля, при которой видеть не больше `events` событий можно
        лишь в 5 % случаев."""
        upper = stats.upper_bound(events, trials)

        assert stats.binom_cdf(events, trials, upper) == pytest.approx(stats.ALPHA, abs=1e-9)
        assert stats.binom_cdf(events, trials, upper - 1e-6) > stats.ALPHA

    @pytest.mark.parametrize(("events", "trials"), [(1, 3), (3, 200), (7, 234), (43, 155)])
    def test_lower_and_upper_mirror_each_other(self, events: int, trials: int) -> None:
        """Нижняя граница событий — единица без верхней границы их отсутствия."""
        lower = stats.lower_bound(events, trials)

        assert lower == pytest.approx(1 - stats.upper_bound(trials - events, trials), abs=1e-9)
        assert lower < events / trials < stats.upper_bound(events, trials)

    def test_the_cumulative_tail_at_the_edges(self) -> None:
        assert stats.binom_cdf(2, 10, 0.0) == 1.0
        assert stats.binom_cdf(2, 10, 1.0) == 0.0
        assert stats.binom_cdf(-1, 10, 0.3) == 0.0
        assert stats.binom_cdf(10, 10, 0.3) == 1.0
        assert stats.binom_cdf(0, 4, 0.5) == pytest.approx(1 / 16)


class TestCleanNeeded:
    @pytest.mark.parametrize(("events", "trials"), [(0, 0), (0, 10), (1, 3), (2, 40), (5, 60)])
    def test_the_count_lands_exactly_on_the_limit(self, events: int, trials: int) -> None:
        """Столько ещё решений без событий — и граница впервые не выше допуска; на одно
        меньше — ещё выше: счёт не расходится с самой границей на шаг."""
        more = stats.clean_needed(events, trials, 0.10)

        assert stats.upper_bound(events, trials + more) <= 0.10
        if more > 0:
            assert stats.upper_bound(events, trials + more - 1) > 0.10

    def test_known_counts(self) -> None:
        """0 из 0 — нужно 29 решений; 1 «не пишем» из 3 — ещё 43 без «не пишем»."""
        assert stats.clean_needed(0, 0, 0.10) == 29
        assert stats.clean_needed(1, 3, 0.10) == 43
        assert stats.clean_needed(0, 29, 0.10) == 0

    def test_nonsense_is_refused(self) -> None:
        with pytest.raises(ValueError, match="событий"):
            stats.upper_bound(4, 3)
        with pytest.raises(ValueError, match="допуск"):
            stats.clean_needed(0, 3, 0.0)
