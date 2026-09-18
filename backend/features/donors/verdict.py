"""Вердикт по донору: пороги  и сроки годности .

Порядок проверок повторяет порядок оплаты, а не читаемости. По замеру
(okf/unit-economy.md) просев по DR стоит 2 юнита на домен, остальные три порога —
18, страны — 55. Поэтому DR проверяется отдельной первой ступенью: он отсекает
17% доменов до того, как мы заплатим за них полную цену.

«Не проверен» — не отказ. Если Ahrefs не вернул данных, домен помечается
отдельным статусом и попадает в отчёт: это повод добрать позже,
а не закрыть сайт навсегда.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.features.core.domain import DonorStatus


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Пороги отбора. Приходят из настроек прогона, а не из кода: смена порога
    не должна переписывать вердикты прошлых прогонов."""

    min_dr: int
    min_org_traffic: int
    min_refdomains: int
    min_keywords: int


@dataclass(frozen=True, slots=True)
class Metrics:
    """Метрики домена. `None` означает «Ahrefs не дал», а не «ноль»."""

    dr: float | None = None
    org_traffic: int | None = None
    refdomains: int | None = None
    org_keywords: int | None = None


@dataclass(frozen=True, slots=True)
class Verdict:
    """Решение и его объяснение — то, что увидит человек в отчёте прогона."""

    status: DonorStatus
    reason: str

    @property
    def is_rejection(self) -> bool:
        return self.status is DonorStatus.UNSUITABLE


def check_dr(dr: float | None, thresholds: Thresholds) -> Verdict | None:
    """Первая ступень: только DR.

    Возвращает вердикт, если вопрос решён, и `None`, если домен прошёл дальше
    и за него стоит платить за остальные метрики.
    """
    if dr is None:
        return Verdict(DonorStatus.UNCHECKED, "Ahrefs не вернул DR")
    if dr < thresholds.min_dr:
        return Verdict(DonorStatus.UNSUITABLE, f"DR {dr:.0f} ниже {thresholds.min_dr}")
    return None


def check_metrics(metrics: Metrics, thresholds: Thresholds) -> Verdict | None:
    """Вторая ступень: остальные три порога.

    Порядок внутри ступени — от самого отсеивающего к наименее: по замеру
    трафик убирает 19% доменов, число ключей ещё 25%, а реф. домены поверх
    этих двух не отсекают ничего. Дешевле назвать причину сразу верную.
    """
    if (verdict := check_dr(metrics.dr, thresholds)) is not None:
        return verdict

    checks: list[tuple[str, int | None, int]] = [
        ("органический трафик", metrics.org_traffic, thresholds.min_org_traffic),
        ("ключей", metrics.org_keywords, thresholds.min_keywords),
        ("реф. доменов", metrics.refdomains, thresholds.min_refdomains),
    ]
    for name, value, minimum in checks:
        if value is None:
            return Verdict(DonorStatus.UNCHECKED, f"Ahrefs не вернул: {name}")
        if value < minimum:
            return Verdict(DonorStatus.UNSUITABLE, f"{name} {value} ниже {minimum}")
    return None


def is_fresh(refreshed_at: datetime | None, ttl_days: int, *, now: datetime | None = None) -> bool:
    """Данные ещё годны — платить за них второй раз не нужно.

    Пустая отметка означает «не запрашивали», то есть данных нет и их надо
    добрать. Для случая «запрашивали, данных нет» отметка ставится всё равно,
    иначе каждый прогон заново жёг бы юниты на доменах вне индекса.
    """
    if refreshed_at is None:
        return False
    moment = now or datetime.now(UTC)
    return moment - refreshed_at < timedelta(days=ttl_days)
