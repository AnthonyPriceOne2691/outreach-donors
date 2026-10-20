"""Статистика eval судьи сегмента: граница Клоппера–Пирсона и парное сравнение версий на одних сайтах.

Доля ошибок на десятках сайтов шумит: «2 из 57» и «4 из 57» могут быть одной и той же долей. Поэтому
у каждой доли — односторонняя 95-процентная граница сверху (Клоппер–Пирсон: наибольшая доля, при
которой столько ошибок или меньше ещё не редкость), а две версии сравниваются **парно** — на одних и
тех же сайтах с решением человека: в счёт идут только сайты, где одна версия права, а другая нет
(точный двусторонний знаковый тест Макнемара).
"""

from __future__ import annotations

from collections.abc import Iterable
from math import comb

from scripts.eval_sales_segment_metrics import Result

#: Уровень доверия границы.
CONFIDENCE = 0.95
#: Шагов деления пополам при поиске границы: точность — до тысячных долей процента.
STEPS = 60


def _at_most(k: int, n: int, p: float) -> float:
    """Вероятность получить не больше `k` ошибок из `n` при доле ошибок `p`."""
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def cp_upper(k: int, n: int, confidence: float = CONFIDENCE) -> float | None:
    """Верхняя граница доли ошибок `k` из `n` (Клоппер–Пирсон, односторонняя); `None` — сайтов нет."""
    if n == 0:
        return None
    if k >= n:
        return 1.0
    low, high = k / n, 1.0
    for _ in range(STEPS):
        middle = (low + high) / 2
        if _at_most(k, n, middle) > 1 - confidence:
            low = middle
        else:
            high = middle
    return low


def sign_p(first_better: int, second_better: int) -> float:
    """Точный двусторонний p знакового теста по несовпавшим сайтам."""
    n = first_better + second_better
    if n == 0:
        return 1.0
    tail = sum(comb(n, i) for i in range(min(first_better, second_better) + 1))
    return min(1.0, float(2 * tail) / float(2**n))


def paired(first: Iterable[Result], second: Iterable[Result]) -> tuple[int, int, float]:
    """Парно на общих сайтах с решением человека: где права только первая, только вторая, и p."""
    others = {result.case_id: result for result in second}
    first_better = second_better = 0
    for result in first:
        other = others.get(result.case_id)
        if other is None or not result.labeled:
            continue
        first_better += result.right and not other.right
        second_better += other.right and not result.right
    return first_better, second_better, sign_p(first_better, second_better)


def bounded(k: int, n: int) -> str:
    """«k из n (доля, ≤ граница)» — словами отчёта eval."""
    upper = cp_upper(k, n)
    if upper is None:
        return "—"
    return f"{k} из {n} ({k / n:.0%}, ≤ {upper:.0%})"
