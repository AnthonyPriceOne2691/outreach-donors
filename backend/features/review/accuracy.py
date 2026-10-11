"""Судья против человека: счёт согласия и порог автоприёма.

Отдельно от очереди (`candidates.py`): там решения пишутся и считаются
(`RunReview.accuracy`), здесь — что такое согласие и когда судье можно
доверить приём. Держать это рядом с записью решений значило бы растить
один файл двумя темами — как и порядок очереди (`ordering.py`).
"""

from __future__ import annotations

from dataclasses import dataclass

#: Автоприём по совету судьи — только когда совет «принять» верен так часто
#: на такой выборке. Требование Anthony 23.09.2026: «если судья в 95% случаев
#: будет прав — открыть на автоматическую приёмку». Выборка меньше — число
#: случайно, и по нему ничего не включают.
AUTO_ACCEPT_PRECISION = 0.95
AUTO_ACCEPT_MIN_DECISIONS = 200


@dataclass(frozen=True, slots=True)
class Agreement:
    """Как совет судьи сходится с решением человека.

    По всем прогонам — только там, где человек решал сам: перенесённое решение —
    копия, а не второе мнение. В одном прогоне — и перенесённые (`RunReview.accuracy`).
    «Посмотри» судьи — просьба, а не совет, и в точность не входит, но считается
    отдельно: доля «посмотри» — это сколько работы судья оставляет человеку.
    """

    advised: int = 0
    agreed: int = 0

    @property
    def precision(self) -> float | None:
        return self.agreed / self.advised if self.advised else None


@dataclass(frozen=True, slots=True)
class JudgeAccuracy:
    """Судья против человека: по совету, по слою, по типу сайта."""

    decided: int
    #: Совет «принять» / «отказ» → сколько раз человек согласился.
    by_advice: dict[str, Agreement]
    #: Слой (`rule` / `model` / `arbiter`) → совет → согласие.
    by_layer: dict[str, dict[str, Agreement]]
    #: Тип сайта по судье → совет → согласие. Здесь видны систематические
    #: ошибки: «модель режет продавцов размещения» — строка этой таблицы.
    by_intent: dict[str, dict[str, Agreement]]
    #: Решённых человеком без вердикта судьи — пропуск, а не мелочь.
    unjudged: int
    #: Решённых, где судья сказал «посмотри».
    asked_to_review: int

    @property
    def auto_accept_ready(self) -> bool:
        accept = self.by_advice.get("accept", Agreement())
        return (
            accept.advised >= AUTO_ACCEPT_MIN_DECISIONS
            and (accept.precision or 0.0) >= AUTO_ACCEPT_PRECISION
        )
