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
    # Глубже ста результатов на ключ прогон не берёт: столько и предлагает
    # экран (10, 20, 30, 50, 100). Провайдер принимает до 700 и берёт деньги
    # за каждые десять — смета растёт вместе с глубиной, а не упирается в неё.
    max_depth_pages: int = Field(default=10, validation_alias="SERP_MAX_DEPTH_PAGES")
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
    # Режим покупки по умолчанию (`features/serp/modes.py`) — синхронный: выдача за
    # секунды, а не за минуты ожидания в очереди (слово владельца 14.10.2026: «в первую
    # очередь скорость, по умолчанию быстрый»). Медленный и дешёвый выбирают на запуске.
    mode: str = Field(default="live", validation_alias="SERP_MODE")
    # Цена страницы в двух быстрых режимах — для сметы, как и цена выше. Прайс
    # провайдера (14.10.2026): приоритетная очередь вдвое дороже отложенной,
    # синхронный запрос — $0,002 за страницу.
    price_priority_usd: float = Field(default=0.0012, validation_alias="SERP_PRICE_PRIORITY_USD")
    price_live_usd: float = Field(default=0.002, validation_alias="SERP_PRICE_LIVE_USD")
    # Отложенный режим: задача ставится, результат забирается опросом.
    # Пауза и число попыток дают потолок ожидания на пачку. Попытки — на
    # каждую страницу глубины: сто результатов провайдер собирает десятью
    # страницами, и окно топ-10 для них не годится (25.09.2026).
    poll_interval_s: float = Field(default=5.0, validation_alias="SERP_POLL_INTERVAL_S")
    poll_attempts: int = Field(default=24, validation_alias="SERP_POLL_ATTEMPTS")


_s = _Serp()

PROVIDER: str = _s.provider
# Хвостовой пробел из .env ломает заголовок Basic — как у ключей.
LOGIN: str = _s.login.strip()
PASSWORD: str = _s.password.strip()
TIMEOUT_S: float = _s.timeout_s
DEPTH_PAGES: int = _s.depth_pages
MAX_DEPTH_PAGES: int = _s.max_depth_pages
MAX_KEYWORDS_PER_RUN: int = _s.max_keywords_per_run
SANDBOX: bool = _s.sandbox
PRICE_PER_KEYWORD_USD: float = _s.price_per_keyword_usd
MODE: str = _s.mode
PRICE_PRIORITY_USD: float = _s.price_priority_usd
PRICE_LIVE_USD: float = _s.price_live_usd
POLL_INTERVAL_S: float = _s.poll_interval_s
POLL_ATTEMPTS: int = _s.poll_attempts
