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
    # Песочница провайдера: те же схемы и учётка, ответы выдуманные,
    # стоимость ноль. Режим для проверки проводки — через тестовую учётку
    # не должно проходить ни боевых данных, ни денег.
    sandbox: bool = Field(default=False, validation_alias="SERP_SANDBOX")
    # Цена одной задачи выдачи, для сметы. Замер из прайса провайдера
    # (okf/unit-economy.md): $0,0006 за задачу в отложенном режиме.
    # Настоящий расход берётся не отсюда, а из ответов провайдера —
    # это число нужно только до прогона, когда ответов ещё нет.
    price_per_keyword_usd: float = Field(
        default=0.0006, validation_alias="SERP_PRICE_PER_KEYWORD_USD"
    )
    # Отложенный режим: задача ставится, результат забирается опросом.
    # Пауза и число попыток дают потолок ожидания на пачку.
    poll_interval_s: float = Field(default=5.0, validation_alias="SERP_POLL_INTERVAL_S")
    poll_attempts: int = Field(default=24, validation_alias="SERP_POLL_ATTEMPTS")


_s = _Serp()

PROVIDER: str = _s.provider
LOGIN: str = _s.login
PASSWORD: str = _s.password
TIMEOUT_S: float = _s.timeout_s
DEPTH_PAGES: int = _s.depth_pages
MAX_KEYWORDS_PER_RUN: int = _s.max_keywords_per_run
SANDBOX: bool = _s.sandbox
PRICE_PER_KEYWORD_USD: float = _s.price_per_keyword_usd
POLL_INTERVAL_S: float = _s.poll_interval_s
POLL_ATTEMPTS: int = _s.poll_attempts
