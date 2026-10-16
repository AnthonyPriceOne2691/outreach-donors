"""Трёхступенчатый сбор метрик донора.

Ступени идут в порядке возрастания цены, а не удобства (okf/unit-economy.md):

    1. просев по DR пакетом          2 юнита на домен, отсекает ~17%
    2. пороги и ВЕРХНЯЯ СТРАНА пакетом   28 юнитов, отсекает ещё ~44%
    3. топ-5 стран по одному домену  55 юнитов, каждому, кто прошёл пороги

Смысл в том, чтобы самый дорогой запрос видел как можно меньше доменов.
При обратном порядке прогон дороже почти вдвое.

**Третья ступень зовётся всегда** (решение владельца 16.10.2026). ТЗ называет
«org_traffic по топ-5 странам» обязательным полем донора, и смета ТЗ закладывала
запрос по странам на каждый проверяемый домен. С 22.09 по 16.10.2026 он не шёл,
когда верхняя страна пакета совпадала со страной прогона: вердикт от этого не
менялся — страна на первом месте в топ-N при любом N, — но у донора оставалась
одна страна вместо пяти (`geo_partial`), и таблица «Доноры» не могла показать
и отфильтровать остальные. Таких по замеру 22.09 — 118 годных из 132. Докупить
им страны — `outreach geo-top5` (`donors/geo_top5.py`).

**Верхняя страна пакета — запасной путь.** Запрос по странам не удался, а верхняя
страна из того же пакета и есть целевая — донор годен по правилу региона
(первое место — в топ-N при любом N), и сбой дорогого запроса его не отнимает:
«подходит» с неполной разбивкой и строкой в журнал. Верхняя другая или её нет —
«не проверен», страны доберёт следующий прогон.

На вход идут ТОЛЬКО домены с истёкшим сроком годности: за свежие уже
заплачено, и отбирает их вызывающий, до входа сюда.

Результат отдаётся пачками, а не одним списком в конце. Прогон на тысячу
доменов идёт часами, и падение на середине не должно стоить всей работы:
сохранив пачку, мы поставили отметку времени, и повторный запуск эти домены
уже пропустит.

**Страны не пришли — следующий прогон добирает только их.** Метрики к этой
минуте куплены, и домен ложится «не проверен» с ними и с причиной
(`COUNTRIES_FAILED`). Свежим он не считается (`repository.countries_missing`),
а следующий прогон получает его метрики готовыми (`PaidMetrics`) и платит
только за страны. До 11.10.2026 такой домен 90 дней числился проверенным,
и обещание «доберёт страны» не исполнялось.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import MAX_BATCH_TARGETS
from backend.features.core.domain import DonorStatus
from backend.features.donors.geo import (
    COUNTRY_ROWS,
    CountryShare,
    GeoRule,
    GeoVerdict,
    build_breakdown,
    check_geo,
    share_base,
)
from backend.features.donors.verdict import Metrics, Thresholds, check_dr, check_metrics

logger = logging.getLogger(__name__)

SCREEN_FIELDS = ("url", "domain_rating")
FULL_FIELDS = (
    "url",
    "domain_rating",
    "org_traffic",
    "refdomains",
    "org_keywords",
    # Верхняя страна приезжает вместе с метриками за 10 юнитов на домен. До 16.10.2026
    # она избавляла от запроса по странам (55 юнитов) тех, у кого совпадала со страной
    # прогона; теперь страны спрашиваются всегда, а она — вердикт, когда этот запрос
    # не удался (`_countries_failed`).
    "org_traffic_top_by_country",
)

#: Причина «не проверен», когда упал запрос по странам: метрики куплены,
#: и следующий прогон доберёт только страны (`repository.countries_missing`).
COUNTRIES_FAILED = "страны не получены"


@dataclass(slots=True)
class DomainResult:
    """Что мы узнали про один домен и во что это сложилось."""

    host: str
    status: DonorStatus
    reason: str
    metrics: Metrics
    geo: GeoVerdict | None = None
    raw: dict[str, Any] | None = None
    #: Когда куплены метрики. Пусто — этим прогоном; у добора стран — прошлым
    #: (`PaidMetrics`): срок годности метрик идёт от покупки, а не от добора.
    metrics_at: datetime | None = None

    @property
    def breakdown(self) -> list[CountryShare]:
        return self.geo.breakdown if self.geo else []


@dataclass(frozen=True, slots=True)
class PaidMetrics:
    """Метрики, купленные прошлым прогоном у домена, до которого не дошли страны.

    Строка пакетного ответа как есть — вердикт по ней считается тем же разбором,
    что при покупке, — и когда она куплена.
    """

    raw: dict[str, Any]
    bought_at: datetime


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
    Непонятый ответ здесь — это `None`, то есть «запасного вердикта нет».
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
    # База та же, что у полной разбивки: колонка страны и трафик домена
    # считаются Ahrefs по-разному, и страна бывает больше домена (07.10.2026).
    return CountryShare(country, traffic, traffic / share_base(total, [traffic]))


@dataclass(frozen=True, slots=True)
class _Region:
    """Чем судить регион: целевая страна, день отчёта и правило версии настроек прогона."""

    country: str
    day: str
    rule: GeoRule

    def judge(self, breakdown: list[CountryShare]) -> GeoVerdict:
        return check_geo(
            self.country, breakdown, top_n=self.rule.top_n, min_share=self.rule.min_share
        )


def _countries_failed(
    host: str, metrics: Metrics, raw: dict[str, Any], region: _Region
) -> DomainResult:
    """Запрос по странам не удался: вердикт — по верхней стране пакета, если её хватает.

    Хватает ровно одного случая: верхняя страна и есть целевая. Она на первом месте,
    то есть в топ-N при любом N ≥ 1, — донор годен, и сбой дорогого запроса годного
    не отнимает: «подходит» с неполной разбивкой (`GeoVerdict.partial`), остальные
    страны докупит `outreach geo-top5`. Во всех прочих случаях про целевую страну
    мы не знаем ничего — она может быть второй, а может не быть в ответе вовсе, —
    и догадка отбраковывала бы годных молча: «не проверен», метрики сохранены,
    и следующий прогон доберёт только страны (`repository.countries_missing`).
    Строку в журнал о сбое пишет вызывающий — с тем исходом, что вышел здесь.
    """
    top = _top_country_share(raw, metrics.org_traffic or 0)
    wanted = region.country.lower()
    verdict = region.judge([top]) if top is not None and top.country == wanted else None
    if verdict is not None and verdict.passed:
        return DomainResult(
            host,
            DonorStatus.SUITABLE,
            verdict.reason,
            metrics,
            geo=replace(verdict, partial=True),
            raw=raw,
        )
    return DomainResult(host, DonorStatus.UNCHECKED, COUNTRIES_FAILED, metrics, raw=raw)


async def _resolve_geo(
    client: AhrefsClient,
    host: str,
    metrics: Metrics,
    raw: dict[str, Any],
    region: _Region,
) -> DomainResult:
    """Ступень 3. Топ-5 стран по одному домену — пакетного аналога у запроса нет.

    Зовётся для каждого, кто прошёл пороги: страны — обязательное поле донора, а не
    только довод вердикта. Строк в ответе — `COUNTRY_ROWS`: шире топа правило
    не бывает (`geo.assert_settings_allow_limited_fetch`). Вердикт — правилом региона
    (`geo.check_geo`) по полной разбивке; сбой запроса — `_countries_failed`.
    """
    try:
        response = await client.metrics_by_country(host, region.day, top_n=COUNTRY_ROWS)
    except AhrefsError as exc:
        # Метрики уже собраны и оплачены — сохраняем их в любом случае.
        failed = _countries_failed(host, metrics, raw, region)
        kept = "годен по верхней стране пакета, разбивка неполная"
        logger.warning(
            "Страны для %s не получены: %s — %s", host, exc, kept if failed.geo else failed.reason
        )
        return failed

    breakdown = build_breakdown(response.rows, metrics.org_traffic or 0)
    geo = region.judge(breakdown)
    status = DonorStatus.SUITABLE if geo.passed else DonorStatus.UNSUITABLE
    if not breakdown:
        status = DonorStatus.UNCHECKED
    return DomainResult(host, status, geo.reason, metrics, geo=geo, raw=raw)


async def _countries_only(
    client: AhrefsClient,
    host: str,
    paid: PaidMetrics,
    thresholds: Thresholds,
    region: _Region,
) -> DomainResult:
    """Добор стран к метрикам, купленным прошлым прогоном: ступени 1–2 не платятся.

    Пороги — этого прогона, по купленным метрикам: не прошедшему их страны не
    нужны, и вердикт выносится даром. Прошедшему — запрос по странам, как у
    свежего домена; верхняя страна той же строки ответа — запасной вердикт.
    """
    metrics = _metrics_from_row(paid.raw)
    verdict = check_metrics(metrics, thresholds)
    if verdict is None:
        result = await _resolve_geo(client, host, metrics, paid.raw, region)
    else:
        result = DomainResult(host, verdict.status, verdict.reason, metrics, raw=paid.raw)
    return replace(result, metrics_at=paid.bought_at)


async def _measured(
    client: AhrefsClient,
    hosts: Sequence[str],
    thresholds: Thresholds,
    region: _Region,
) -> list[DomainResult]:
    """Три ступени по порядку цены — для доменов, у которых метрик ещё нет."""
    if not hosts:
        return []
    survived_screen, results = await _screen_batch(client, hosts, thresholds)
    if survived_screen:
        survived_metrics, rejected = await _measure_batch(client, survived_screen, thresholds)
        results.extend(rejected)
        for host, metrics, raw in survived_metrics:
            results.append(await _resolve_geo(client, host, metrics, raw, region))
    return results


async def collect(
    hosts: Sequence[str],
    client: AhrefsClient,
    thresholds: Thresholds,
    target_country: str,
    *,
    date: str | None = None,
    batch_size: int = MAX_BATCH_TARGETS,
    paid: Mapping[str, PaidMetrics] | None = None,
    geo: GeoRule = GeoRule(),
) -> AsyncIterator[list[DomainResult]]:
    """Собирает метрики по доменам и отдаёт результат пачками.

    Каждая выданная пачка — чекпоинт: вызывающий сохраняет её и ставит
    отметку времени, после чего эти домены выпадают из повторных прогонов.

    `paid` — домены, у которых метрики куплены, а стран нет
    (`DonorRepository.paid_metrics`): им прогон добирает только страны.
    `geo` — правило региона версии настроек прогона (`RunRequest.geo`);
    умолчание — конфига, для тестов.
    """
    region = _Region(target_country, date or datetime.now(UTC).date().isoformat(), geo)
    known = paid or {}

    for chunk in _chunks(hosts, batch_size):
        results = await _measured(
            client, [host for host in chunk if host not in known], thresholds, region
        )
        for host in chunk:
            if host in known:
                results.append(await _countries_only(client, host, known[host], thresholds, region))
        yield results
