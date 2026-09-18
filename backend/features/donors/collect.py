"""Трёхступенчатый сбор метрик донора.

Ступени идут в порядке возрастания цены, а не удобства (okf/unit-economy.md):

    1. просев по DR пакетом          2 юнита на домен, отсекает ~17%
    2. остальные пороги пакетом      18 юнитов, отсекает ещё ~44%
    3. страны по одному домену       55 юнитов, только для дошедших

Смысл в том, чтобы самый дорогой запрос видел как можно меньше доменов.
При обратном порядке прогон дороже почти вдвое.

На вход идут ТОЛЬКО домены с истёкшим сроком годности: за свежие уже
заплачено, и отбирает их вызывающий, до входа сюда.

Результат отдаётся пачками, а не одним списком в конце. Прогон на тысячу
доменов идёт часами, и падение на середине не должно стоить всей работы:
сохранив пачку, мы поставили отметку времени, и повторный запуск эти домены
уже пропустит.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import MAX_BATCH_TARGETS
from backend.features.core.domain import DonorStatus
from backend.features.donors.geo import CountryShare, GeoVerdict, build_breakdown, check_geo
from backend.features.donors.verdict import Metrics, Thresholds, check_dr, check_metrics

logger = logging.getLogger(__name__)

SCREEN_FIELDS = ("url", "domain_rating")
FULL_FIELDS = ("url", "domain_rating", "org_traffic", "refdomains", "org_keywords")


@dataclass(slots=True)
class DomainResult:
    """Что мы узнали про один домен и во что это сложилось."""

    host: str
    status: DonorStatus
    reason: str
    metrics: Metrics
    geo: GeoVerdict | None = None
    raw: dict[str, Any] | None = None

    @property
    def breakdown(self) -> list[CountryShare]:
        return self.geo.breakdown if self.geo else []


def parse_domain_rating(row: dict[str, Any]) -> float | None:
    """DR приходит в двух формах: числом либо объектом с вложенным полем.

    Наивное чтение однажды вернёт словарь, сравнение с порогом упадёт —
    и упадёт на середине прогона, когда юниты за пачку уже списаны.
    """
    value = row.get("domain_rating")
    if isinstance(value, dict):
        value = value.get("domain_rating")
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _as_int(row: dict[str, Any], key: str) -> int | None:
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)


def _metrics_from_row(row: dict[str, Any]) -> Metrics:
    return Metrics(
        dr=parse_domain_rating(row),
        org_traffic=_as_int(row, "org_traffic"),
        refdomains=_as_int(row, "refdomains"),
        org_keywords=_as_int(row, "org_keywords"),
    )


def _host_of(row: dict[str, Any], fallback: str) -> str:
    """Ahrefs возвращает `url` с завершающим слэшем — приводим к хосту."""
    raw = row.get("url")
    if not isinstance(raw, str) or not raw:
        return fallback
    return raw.removeprefix("https://").removeprefix("http://").rstrip("/").lower()


def _chunks(items: Sequence[str], size: int) -> list[Sequence[str]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


async def _screen_batch(
    client: AhrefsClient, hosts: Sequence[str], thresholds: Thresholds
) -> tuple[list[str], list[DomainResult]]:
    """Ступень 1. Возвращает прошедших и вердикты по отсеянным."""
    response = await client.batch_metrics(hosts, SCREEN_FIELDS)
    by_host = {_host_of(row, ""): row for row in response.rows}

    passed: list[str] = []
    rejected: list[DomainResult] = []
    for host in hosts:
        row = by_host.get(host)
        if row is None:
            rejected.append(
                DomainResult(host, DonorStatus.UNCHECKED, "Ahrefs не знает домен", Metrics())
            )
            continue
        dr = parse_domain_rating(row)
        verdict = check_dr(dr, thresholds)
        if verdict is None:
            passed.append(host)
        else:
            rejected.append(DomainResult(host, verdict.status, verdict.reason, Metrics(dr=dr)))
    return passed, rejected


async def _measure_batch(
    client: AhrefsClient, hosts: Sequence[str], thresholds: Thresholds
) -> tuple[list[tuple[str, Metrics, dict[str, Any]]], list[DomainResult]]:
    """Ступень 2. Возвращает дошедших до стран и вердикты по отсеянным."""
    response = await client.batch_metrics(hosts, FULL_FIELDS)
    by_host = {_host_of(row, ""): row for row in response.rows}

    passed: list[tuple[str, Metrics, dict[str, Any]]] = []
    rejected: list[DomainResult] = []
    for host in hosts:
        row = by_host.get(host)
        if row is None:
            rejected.append(
                DomainResult(host, DonorStatus.UNCHECKED, "Ahrefs не вернул метрики", Metrics())
            )
            continue
        metrics = _metrics_from_row(row)
        verdict = check_metrics(metrics, thresholds)
        if verdict is None:
            passed.append((host, metrics, row))
        else:
            rejected.append(DomainResult(host, verdict.status, verdict.reason, metrics, raw=row))
    return passed, rejected


async def _resolve_geo(
    client: AhrefsClient,
    host: str,
    metrics: Metrics,
    raw: dict[str, Any],
    target_country: str,
    date: str,
) -> DomainResult:
    """Ступень 3. Страны для одного домена — пакетного аналога у запроса нет."""
    try:
        response = await client.metrics_by_country(host, date)
    except AhrefsError as exc:
        # Метрики уже собраны и оплачены — сохраняем их и помечаем домен
        # «не проверен». Повторный прогон доберёт только страны.
        logger.warning("Страны для %s не получены: %s", host, exc)
        return DomainResult(host, DonorStatus.UNCHECKED, "страны не получены", metrics, raw=raw)

    breakdown = build_breakdown(response.rows, metrics.org_traffic or 0)
    geo = check_geo(target_country, breakdown)
    status = DonorStatus.SUITABLE if geo.passed else DonorStatus.UNSUITABLE
    if not breakdown:
        status = DonorStatus.UNCHECKED
    return DomainResult(host, status, geo.reason, metrics, geo=geo, raw=raw)


async def collect(
    hosts: Sequence[str],
    client: AhrefsClient,
    thresholds: Thresholds,
    target_country: str,
    *,
    date: str | None = None,
    batch_size: int = MAX_BATCH_TARGETS,
) -> AsyncIterator[list[DomainResult]]:
    """Собирает метрики по доменам и отдаёт результат пачками.

    Каждая выданная пачка — чекпоинт: вызывающий сохраняет её и ставит
    отметку времени, после чего эти домены выпадают из повторных прогонов.
    """
    day = date or datetime.now(UTC).date().isoformat()

    for chunk in _chunks(hosts, batch_size):
        results: list[DomainResult] = []

        survived_screen, rejected = await _screen_batch(client, chunk, thresholds)
        results.extend(rejected)

        if survived_screen:
            survived_metrics, rejected = await _measure_batch(client, survived_screen, thresholds)
            results.extend(rejected)

            for host, metrics, raw in survived_metrics:
                results.append(await _resolve_geo(client, host, metrics, raw, target_country, day))

        yield results
