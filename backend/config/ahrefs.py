"""Ahrefs: ключ, лимиты, сроки годности.

Про лимит важно помнить: ключ общий с соседней системой, и месячный
лимит у них один на двоих. Поэтому остаток считается по общему счётчику
расхода, а не по своему — так записано в требованиях.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Ahrefs(DomainSettings):
    api_key: str = Field(default="", validation_alias="AHREFS_API_KEY")
    base_url: str = Field(default="https://api.ahrefs.com", validation_alias="AHREFS_BASE_URL")
    timeout_s: float = Field(default=30.0, validation_alias="AHREFS_TIMEOUT_S")
    # Месячный лимит всего аккаунта, делится с соседними системами.
    monthly_units: int = Field(default=1_000_000, validation_alias="AHREFS_MONTHLY_UNITS")
    # Потолок расхода на этот сервис. Прогон дороже — не запускается.
    units_cap: int = Field(default=100_000, validation_alias="AHREFS_UNITS_CAP")
    # Запросов в минуту: у Ahrefs динамический троттлинг, держимся ниже.
    rate_limit_per_min: int = Field(default=50, validation_alias="AHREFS_RATE_LIMIT_PER_MIN")


_s = _Ahrefs()

API_KEY: str = _s.api_key
BASE_URL: str = _s.base_url
TIMEOUT_S: float = _s.timeout_s
MONTHLY_UNITS: int = _s.monthly_units
UNITS_CAP: int = _s.units_cap
RATE_LIMIT_PER_MIN: int = _s.rate_limit_per_min
