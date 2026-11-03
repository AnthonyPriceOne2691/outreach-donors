"""Доля на малой выборке — честными границами Клоппера — Пирсона.

«2 из 57» и «4 из 57» могут быть одной долей: число без границы читается точной долей, и
вывод на горстке решений выглядит доказанным. Граница здесь односторонняя, 95 %: верхняя
говорит «доля не больше», нижняя — «доля не меньше», и каждая ошибается не чаще раза из
двадцати. Вывод «в допуске» — верхняя граница не выше допуска; «вне допуска» — нижняя выше
него; между ними — «не доказано» (`crawl/accuracy.py`).

**Точно, а не приближением.** Граница — корень биномиального хвоста, найденный делением
отрезка: нормальное приближение на нуле событий даёт ноль, а «0 из 30» значит «не больше
9,5 %». Чистый питон: зависимостей ради трёх функций проект не тянет.
"""

from __future__ import annotations

import math
from collections.abc import Callable

#: Ошибка односторонней границы: 5 % — граница 95 %.
ALPHA = 0.05

#: Сколько раз делить отрезок: 2⁻¹⁰⁰ — мельче, чем различает число с плавающей точкой.
_STEPS = 100


def _check(events: int, trials: int) -> None:
    if trials < 0 or not 0 <= events <= trials:
        raise ValueError(f"событий {events} из {trials}: нужно 0 ≤ событий ≤ испытаний")


def binom_cdf(events: int, trials: int, p: float) -> float:
    """Вероятность не больше `events` событий в `trials` испытаниях, у каждого — `p`."""
    if events < 0:
        return 0.0
    if events >= trials or p <= 0.0:
        return 1.0
    if p >= 1.0:
        return 0.0
    log_p, log_q = math.log(p), math.log1p(-p)
    head = math.lgamma(trials + 1)
    terms = (
        math.exp(
            head
            - math.lgamma(i + 1)
            - math.lgamma(trials - i + 1)
            + i * log_p
            + (trials - i) * log_q
        )
        for i in range(events + 1)
    )
    return min(1.0, math.fsum(terms))


def _solve(falling: Callable[[float], float], target: float, *, upper: bool) -> float:
    """Корень убывающей по `p` функции на отрезке [0, 1] — делением отрезка. Конец отрезка —
    с запасом в сторону границы: верхняя не занижается, нижняя не завышается."""
    low, high = 0.0, 1.0
    for _ in range(_STEPS):
        middle = (low + high) / 2
        if falling(middle) > target:
            low = middle
        else:
            high = middle
    return high if upper else low


def upper_bound(events: int, trials: int, alpha: float = ALPHA) -> float:
    """Верхняя граница доли: `events` событий из `trials`. Испытаний нет — 1: о доле ничего
    не известно."""
    _check(events, trials)
    if events == trials:  # и испытаний нет: 0 из 0
        return 1.0
    if events == 0:
        return 1.0 - math.pow(alpha, 1.0 / trials)
    return _solve(lambda p: binom_cdf(events, trials, p), alpha, upper=True)


def lower_bound(events: int, trials: int, alpha: float = ALPHA) -> float:
    """Нижняя граница доли: `events` событий из `trials`. Событий нет — 0."""
    _check(events, trials)
    if events == 0:
        return 0.0
    if events == trials:
        return math.pow(alpha, 1.0 / trials)
    return _solve(lambda p: binom_cdf(events - 1, trials, p), 1.0 - alpha, upper=False)


def clean_needed(events: int, trials: int, limit: float, alpha: float = ALPHA) -> int:
    """Сколько ещё испытаний без событий нужно, чтобы верхняя граница легла не выше `limit`.

    Ноль — уже легла. Верхняя граница не выше `limit` ровно тогда, когда при доле `limit`
    видеть не больше `events` событий маловероятно (`binom_cdf ≤ alpha`): так число ищется
    без деления отрезка на каждом шаге — сначала удвоением, потом делением пополам."""
    _check(events, trials)
    if not 0.0 < limit < 1.0:
        raise ValueError(f"допуск {limit}: нужна доля строго между 0 и 1")

    def enough(total: int) -> bool:
        return binom_cdf(events, total, limit) <= alpha

    if enough(trials):
        return 0
    low, high = trials, max(trials, 1)
    while not enough(high):
        low, high = high, high * 2
    while high - low > 1:
        middle = (low + high) // 2
        if enough(middle):
            high = middle
        else:
            low = middle
    return high - trials
