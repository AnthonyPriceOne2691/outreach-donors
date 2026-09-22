"""Трёхступенчатый сбор метрик донора.

Ступени идут в порядке возрастания цены, а не удобства (okf/unit-economy.md):

    1. просев по DR пакетом          2 юнита на домен, отсекает ~17%
    2. пороги и ВЕРХНЯЯ СТРАНА пакетом   28 юнитов, отсекает ещё ~44%
    3. страны по одному домену       55 юнитов, только когда без них никак

Смысл в том, чтобы самый дорогой запрос видел как можно меньше доменов.
При обратном порядке прогон дороже почти вдвое.

**Третья ступень зовётся не всегда.** Верхняя страна приезжает пакетом
вместе с метриками, и когда она же целевая — вердикт по региону готов:
страна на первом месте, а значит в топ-N по определению. Дорогой запрос
остаётся для тех, у кого верхняя страна другая: про целевую мы тогда
не знаем ничего. Замер: у 118 годных доноров из 132 верхняя страна
совпадает с целевой, то есть дорогой шаг нужен примерно каждому девятому.

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
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import MAX_BATCH_TARGETS
from backend.features.core.domain import DonorStatus
from backend.features.donors.geo import CountryShare, GeoVerdict, build_breakdown, check_geo
from backend.features.donors.verdict import Metrics, Thresholds, check_dr, check_metrics

logger = logging.getLogger(__name__)

SCREEN_FIELDS = ("url", "domain_rating")
FULL_FIELDS = (
    "url",
    "domain_rating",
    "org_traffic",
    "refdomains",
    "org_keywords",
    # Верхняя страна приезжает вместе с метриками за 10 юнитов на домен.
    # Отдельный запрос по странам стоит 55 и идёт по одному домену — это
    # 70% расхода прогона. Замер 22.09.2026: 360 юнитов на пять доменов
    # нынешним путём против 135 пакетом.
    "org_traffic_top_by_country",
)


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


def _top_pair(rows: Any) -> tuple[str, int] | None:
    """Пара «страна, трафик» из колонки пакетного ответа.

    Разбор защитный: формат чужой, и когда он приедет другим, молчаливое
    «страны нет» отбраковало бы домен, за метрики которого уже заплачено.
    Непонятый ответ здесь — это `None`, то есть «платим как раньше».
    """
    # Сопоставление с образцом, а не цепочка проверок: форма ответа
    # видна целиком одной строкой, и лишнее поле в ней ничего не ломает.
    match rows:
        case [[str() as country, int() as traffic, *_], *_]:
            return country.lower(), traffic
        case _:
            return None


def _top_country_share(row: dict[str, Any], total: int) -> CountryShare | None:
    """Верхняя страна из пакетного ответа. `None` — колонки нет или пусто.

    Ahrefs отдаёт её списком пар «страна, трафик», и список этот всегда
    из одной строки: колонка так и называется — ВЕРХНЯЯ страна. Проверено
    на восьми доменах с разной географией, включая bbc.com и wikipedia.org.
    """
    pair = _top_pair(row.get("org_traffic_top_by_country"))
    if pair is None or total <= 0:
        return None
    country, traffic = pair
    return CountryShare(country, traffic, traffic / total)


def _geo_without_paying(
    row: dict[str, Any], metrics: Metrics, target_country: str
) -> GeoVerdict | None:
    """Вердикт по региону из пакетного ответа — или `None`, если его мало.

    Хватает ровно одного случая: верхняя страна и есть целевая. Тогда она
    на первом месте, то есть в топ-N при любом N ≥ 1, и дорогой запрос
    ничего не изменит — он вернул бы ту же страну первой строкой.

    Во всех прочих случаях про целевую страну мы не знаем ничего: она
    может быть второй, а может не быть в ответе вовсе. Догадываться здесь
    значит отбраковывать годных доноров молча, поэтому `None` — и платим.
    """
    top = _top_country_share(row, metrics.org_traffic or 0)
    if top is None or top.country != target_country.lower():
        return None
    verdict = check_geo(target_country, [top])
    return replace(verdict, partial=True) if verdict.passed else None


async def _resolve_geo(
    client: AhrefsClient,
    host: str,
    metrics: Metrics,
    raw: dict[str, Any],
    target_country: str,
    date: str,
) -> DomainResult:
    """Ступень 3. Страны по одному домену — пакетного аналога у запроса нет.

    Зовётся только когда верхней страны не хватило: см. `_geo_without_paying`.
    """
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


async def _geo_result(
    client: AhrefsClient,
    host: str,
    metrics: Metrics,
    raw: dict[str, Any],
    target_country: str,
    day: str,
) -> DomainResult:
    """Вердикт по региону — даром, если верхней страны хватило.

    Дорогой запрос остаётся для тех, у кого верхняя страна другая: про
    целевую мы тогда не знаем ничего.
    """
    cheap = _geo_without_paying(raw, metrics, target_country)
    if cheap is None:
        return await _resolve_geo(client, host, metrics, raw, target_country, day)
    return DomainResult(host, DonorStatus.SUITABLE, cheap.reason, metrics, geo=cheap, raw=raw)


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
                results.append(await _geo_result(client, host, metrics, raw, target_country, day))

        yield results
