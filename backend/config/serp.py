"""Источник выдачи Google."""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Serp(DomainSettings):
    provider: str = Field(default="dataforseo", validation_alias="SERP_PROVIDER")
    login: str = Field(default="", validation_alias="SERP_LOGIN")
    password: str = Field(default="", validation_alias="SERP_PASSWORD")
    timeout_s: float = Field(default=60.0, validation_alias="SERP_TIMEOUT_S")
    # Глубина выдачи: страница = 10 результатов. Топ-10.
    depth_pages: int = Field(default=1, validation_alias="SERP_DEPTH_PAGES")
    # Ключей за прогон. Приёмка идёт на 500 — это пять прогонов.
    max_keywords_per_run: int = Field(default=100, validation_alias="SERP_MAX_KEYWORDS_PER_RUN")


_s = _Serp()

PROVIDER: str = _s.provider
LOGIN: str = _s.login
PASSWORD: str = _s.password
TIMEOUT_S: float = _s.timeout_s
DEPTH_PAGES: int = _s.depth_pages
MAX_KEYWORDS_PER_RUN: int = _s.max_keywords_per_run
