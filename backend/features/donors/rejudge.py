"""Вердикт по сохранённым данным — для страны и порогов прогона, который спрашивает.

**Вердикт — у прогона, а не у домена** (аудит 20.10.2026). Замер ложился на домен
одной строкой без страны и порогов, а свежий домен прогон не перепроверял 90 дней:
годный для США предлагался в прогоне Канады, отсеянный для Канады не предлагался
в прогоне Великобритании, а поднятый порог не касался купленных под прежний.

**Пересуд не покупает ничего.** У домена лежит то, из чего вердикт и складывался:
метрики пакетного ответа (`donors.metrics`, DR и трафик — колонками) и топ-5 стран
с долями (`donors.geo_breakdown`). Судят те же функции, что и замер
(`verdict.check_dr`, `verdict.check_metrics`, `geo.check_geo`), — второй копии
правил нет. Срок годности метрик прежний: покупать заново — через 90 дней.

**Чего замер не купил, того пересуд не выдумывает.** Отсеянному по DR остальных
метрик не покупали, отсеянному по метрикам — стран; у неполной разбивки одна верхняя
страна. Пороги прогона, которые такого домена дальше пропускают, судить его нечем —
«не проверен» с причиной, а не догадка: отказ по догадке молча терял бы годных.

**Крупный сайт пересуд узнаёт и по причине замера** (ревью 20.10.2026): колонка DR — целая,
и DR 80,4 лежит в ней числом 80, которое порог 80 пропускает. Точный DR — в ответе пакета,
а у отсеянного первой ступенью ответа нет; зато причина «DR > 80 — …» помнит, что замер видел
DR выше 80 (`verdict.big_site_bound`).

Судит пересуд и новый замер — для прогона, где донора приняли (`donors/held.py`): те же
данные, что ложатся на донора, — из результата сбора (`Stored.of_result`).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any

from backend.features.core.domain import DonorStatus
from backend.features.core.models.donor import DonorModel
from backend.features.donors.collect import COUNTRIES_FAILED, DomainResult, parse_domain_rating
from backend.features.donors.geo import NO_COUNTRY_DATA, CountryShare, GeoRule, check_geo
from backend.features.donors.verdict import (
    Metrics,
    Thresholds,
    Verdict,
    big_site_bound,
    big_site_reason,
    check_dr,
    check_metrics,
)

#: Замер отсёк домен по DR, остальных метрик не покупал, а пороги прогона DR пропускают.
ONLY_DR = "куплен только DR: прежние пороги отсеяли домен по нему, других метрик нет"
#: Метрики есть, а страны замер не спрашивал: прежние пороги отсеяли домен по метрикам.
NOT_ASKED = "страны не спрашивали: прежние пороги отсеяли домен по метрикам"
#: Разбивка неполная — одна верхняя страна, и она не страна прогона.
PARTIAL = "в разбивке одна верхняя страна — места страны прогона не видно"
#: DR не пришёл, и почему — замер не записал (так бывало до 11.10.2026).
NO_DR = "Ahrefs не вернул DR"
#: «Не проверен» из-за нехватки данных: метрик или стран не купили, страны не пришли, в
#: разбивке одна верхняя. Свежий, такой домен 90 дней не перемеряется — резерв показывает
#: его своей группой (ревью 20.10.2026).
DATA_GAPS = frozenset({ONLY_DR, NOT_ASKED, PARTIAL, COUNTRIES_FAILED})


@dataclass(frozen=True, slots=True)
class Stored:
    """Что замер оставил на домене — ровно то, из чего вердикт складывается."""

    dr: float | None
    org_traffic: int | None
    #: Строка пакетного ответа как есть; `None` — пакет второй ступени не покупали.
    metrics: Mapping[str, Any] | None
    #: Топ-5 стран, как записаны (`donors.geo_breakdown`): по убыванию трафика.
    breakdown: Sequence[Any] | None
    partial: bool = False
    #: Страны спрашивали (`geo_refreshed_at`): пустая разбивка — «данных нет», а не
    #: «не спрашивали».
    countries_asked: bool = False
    #: Причина, записанная замером: у «не проверен» — почему.
    reason: str | None = None
    #: Вердикт замера: отсеянному порогами стран не спрашивали, а «не проверен» с метриками
    #: стран не получил — и это разные причины.
    status: DonorStatus | None = None
    #: Замер видел DR выше этого порога, а точного DR нет (`verdict.big_site_bound`).
    above: int | None = None

    @classmethod
    def of(cls, donor: DonorModel) -> Stored:
        raw = donor.metrics if isinstance(donor.metrics, dict) else None
        exact = None if raw is None else parse_domain_rating(raw)
        dr = exact if exact is not None else (None if donor.dr is None else float(donor.dr))
        breakdown = donor.geo_breakdown if isinstance(donor.geo_breakdown, list) else None
        # Вердикт и причина — замера: у принятого донора `status` — для выходов (`held.py`).
        measured = donor.measured_status
        reason = donor.reject_reason if measured is None else donor.measured_reason
        return cls(
            dr=dr,
            org_traffic=donor.org_traffic,
            metrics=raw,
            breakdown=breakdown,
            partial=bool(donor.geo_partial),
            countries_asked=donor.geo_refreshed_at is not None,
            reason=reason,
            status=donor.status if measured is None else _status(measured),
            above=None if exact is not None else big_site_bound(reason),
        )

    @classmethod
    def of_result(cls, result: DomainResult) -> Stored:
        """Что ляжет на донора из результата сбора — тем же видом, что из базы."""
        return cls(
            dr=result.metrics.dr,
            org_traffic=result.metrics.org_traffic,
            metrics=result.raw,
            breakdown=[asdict(share) for share in result.breakdown] or None,
            partial=result.geo is not None and result.geo.partial,
            countries_asked=result.geo is not None,
            reason=result.reason,
            status=result.status,
        )


def rejudge(stored: Stored, thresholds: Thresholds, country: str, rule: GeoRule) -> Verdict:
    """Вердикт для этой страны и этих порогов — тем же порядком, что у замера."""
    if stored.dr is None:
        return Verdict(DonorStatus.UNCHECKED, stored.reason or NO_DR)
    if stored.above is not None and stored.above >= thresholds.max_dr:
        return Verdict(DonorStatus.UNSUITABLE, big_site_reason(thresholds.max_dr))
    if (verdict := check_dr(stored.dr, thresholds)) is not None:
        return verdict
    if stored.metrics is None:
        return Verdict(DonorStatus.UNCHECKED, ONLY_DR)
    metrics = Metrics(
        dr=stored.dr,
        org_traffic=stored.org_traffic,
        refdomains=_number(stored.metrics, "refdomains"),
        org_keywords=_number(stored.metrics, "org_keywords"),
    )
    if (verdict := check_metrics(metrics, thresholds)) is not None:
        return verdict
    return _region(stored, country, rule)


def _region(stored: Stored, country: str, rule: GeoRule) -> Verdict:
    """Регион по записанной разбивке. Доли — как записал замер: база доли
    (`geo.share_base`) у него уже учтена, место считается по порядку трафика."""
    shares = _shares(stored.breakdown)
    if not shares:
        return Verdict(DonorStatus.UNCHECKED, _no_countries(stored))
    if stored.partial and shares[0].country != country.lower():
        return Verdict(DonorStatus.UNCHECKED, PARTIAL)
    geo = check_geo(country, shares, top_n=rule.top_n, min_share=rule.min_share)
    return Verdict(DonorStatus.SUITABLE if geo.passed else DonorStatus.UNSUITABLE, geo.reason)


def _no_countries(stored: Stored) -> str:
    """Почему стран нет: спрашивали — Ahrefs не дал; отсеянному порогами не спрашивали;
    прошедшему метрики запрос по странам не удался — «страны не получены»."""
    if stored.countries_asked:
        return NO_COUNTRY_DATA
    return NOT_ASKED if stored.status is DonorStatus.UNSUITABLE else COUNTRIES_FAILED


def _status(value: str) -> DonorStatus | None:
    """Вердикт замера из колонки строкой; незнакомое слово — «вердикта нет», не падение."""
    return DonorStatus(value) if value in _STATUSES else None


#: Вердикты, которые бывают у замера.
_STATUSES = frozenset(status.value for status in DonorStatus)


def _shares(rows: Sequence[Any] | None) -> list[CountryShare]:
    """Строки разбивки, из которых судят: страна строкой и доля числом. Строка без них
    не судит ничего — и не валит пересуд всем списком."""
    shares: list[CountryShare] = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        country, share = row.get("country"), row.get("share")
        if not isinstance(country, str) or isinstance(share, bool):
            continue
        if not isinstance(share, int | float):
            continue
        traffic = _number(row, "org_traffic") or 0
        shares.append(CountryShare(country.lower(), traffic, float(share)))
    return shares


def _number(row: Mapping[str, Any], key: str) -> int | None:
    """Число из записанного ответа. Флаг — не число, хоть Python и считает его им."""
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)
