"""Пороги отбора донора — ЗНАЧЕНИЯ ПО УМОЛЧАНИЮ.

Рабочие пороги живут в настройках прогона, в базе, и версионируются
(«все пороги — в настройках прогона»). Здесь — только то, что
подставляется при создании новых настроек, чтобы не заводить их с нуля.

Менять пороги правкой этого файла нельзя: смена значения тут не должна
переписывать вердикты уже сделанных прогонов.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Filters(DomainSettings):
    min_dr: int = Field(default=20, validation_alias="FILTERS_MIN_DR")
    min_org_traffic: int = Field(default=500, validation_alias="FILTERS_MIN_ORG_TRAFFIC")
    min_refdomains: int = Field(default=100, validation_alias="FILTERS_MIN_REFDOMAINS")
    min_keywords: int = Field(default=300, validation_alias="FILTERS_MIN_KEYWORDS")
    # Гео-правило страна в топ-N по органическому трафику ИЛИ доля ≥ порога.
    geo_top_n: int = Field(default=5, validation_alias="FILTERS_GEO_TOP_N")
    geo_min_share: float = Field(default=0.20, validation_alias="FILTERS_GEO_MIN_SHARE")
    # Сроки годности: пока не истёк — API не зовём.
    metrics_ttl_days: int = Field(default=90, validation_alias="FILTERS_METRICS_TTL_DAYS")
    price_ttl_days: int = Field(default=150, validation_alias="FILTERS_PRICE_TTL_DAYS")


_s = _Filters()

MIN_DR: int = _s.min_dr
MIN_ORG_TRAFFIC: int = _s.min_org_traffic
MIN_REFDOMAINS: int = _s.min_refdomains
MIN_KEYWORDS: int = _s.min_keywords
GEO_TOP_N: int = _s.geo_top_n
GEO_MIN_SHARE: float = _s.geo_min_share
METRICS_TTL_DAYS: int = _s.metrics_ttl_days
PRICE_TTL_DAYS: int = _s.price_ttl_days
