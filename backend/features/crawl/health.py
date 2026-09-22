"""Здоровье обхода: доля отказов за последние N запросов.

Требование Этапа 2 — «403/429/5xx/timeout больше 10% за последние 500
запросов: темп вдвое ниже и смена прокси; больше 30%: пауза и тревога».
Три решения внутри этой строки стоит назвать вслух.

**Окно считается по запросам, а не за всё время.** Прогон, начавшийся
хорошо и упёршийся в защиту на середине, по среднему за всё время
выглядит здоровым до самого конца — то есть сигнал приходит тогда,
когда обход уже закончен и чинить нечего.

**404 — не отказ.** Страницы удаляют, ссылки протухают; сайт, честно
сказавший «нет такой страницы», здоров. Посчитав 404 отказом, мы
замедляли бы обход ровно на тех сайтах, где список адресов старый.

**Малое число запросов долей не меряется.** Три отказа из трёх в начале
прогона — это не сто процентов нездоровья, это три отказа. Без нижней
границы обход останавливался бы на первом же сайте, начавшем с ошибки.
"""

from __future__ import annotations

import logging
from collections import deque
from enum import StrEnum

from backend.config import crawl as cfg
from backend.features.crawl.fetch import FetchOutcome

logger = logging.getLogger(__name__)

#: Что считается отказом. `MISSING` сюда не входит — см. док модуля.
FAILURES = frozenset({FetchOutcome.BLOCKED, FetchOutcome.ERROR})


class HealthVerdict(StrEnum):
    """Что делать с темпом обхода прямо сейчас."""

    OK = "ok"
    SLOW_DOWN = "slow_down"
    STOP = "stop"


class CrawlHealth:
    """Скользящее окно исходов и вердикт по нему.

    Держит только окно, а не всю историю: при 1 000 страниц на донора
    и сотне доноров полная история — это сотни тысяч записей ради одного
    числа. Итоговые счётчики ведутся отдельно и не зависят от окна.
    """

    def __init__(self, *, window: int | None = None, min_requests: int | None = None) -> None:
        size = window if window is not None else cfg.HEALTH_WINDOW
        self._window: deque[bool] = deque(maxlen=size)
        self._min = min_requests if min_requests is not None else cfg.HEALTH_MIN_REQUESTS
        self.totals: dict[FetchOutcome, int] = dict.fromkeys(FetchOutcome, 0)

    def record(self, outcome: FetchOutcome) -> None:
        self.totals[outcome] += 1
        self._window.append(outcome in FAILURES)

    @property
    def requests(self) -> int:
        """Сколько запросов сделано всего — по счётчикам, не по окну."""
        return sum(self.totals.values())

    @property
    def failure_share(self) -> float:
        """Доля отказов в окне. Пустое окно — ноль, а не деление на ноль."""
        if not self._window:
            return 0.0
        return sum(self._window) / len(self._window)

    @property
    def blocked_share(self) -> float:
        """Доля закрытых страниц от всех запросов.

        Это то самое число, ради которого затевался замер: от него
        зависит смета Этапа 2 с шестикратным разбросом.
        """
        total = self.requests
        return self.totals[FetchOutcome.BLOCKED] / total if total else 0.0

    @property
    def verdict(self) -> HealthVerdict:
        if len(self._window) < self._min:
            return HealthVerdict.OK
        share = self.failure_share
        if share > cfg.STOP_SHARE:
            return HealthVerdict.STOP
        if share > cfg.SLOW_DOWN_SHARE:
            return HealthVerdict.SLOW_DOWN
        return HealthVerdict.OK

    def report(self) -> dict[str, float | int]:
        """Числа для записи обхода и для отчёта замера."""
        return {
            "requests": self.requests,
            "opened": self.totals[FetchOutcome.OK],
            "missing": self.totals[FetchOutcome.MISSING],
            "blocked": self.totals[FetchOutcome.BLOCKED],
            "errors": self.totals[FetchOutcome.ERROR],
            "blocked_share": round(self.blocked_share, 4),
            "failure_share": round(self.failure_share, 4),
        }
