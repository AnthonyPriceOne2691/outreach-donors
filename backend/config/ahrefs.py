"""Ahrefs: ключ, адрес, частота запросов.

Про лимит важно помнить: ключ общий с соседней системой, и лимит у них
один на двоих. Поэтому остаток спрашивается у самого Ahrefs, а не
считается по своему счётчику расхода — так записано в требованиях.

**Лимитов и капа здесь нет** (решение владельца 16.10.2026): кап — от размера
ключа, который называет сам Ahrefs (`runs/ahrefs_key.py`, `runs/ahrefs_cap.py`), а
сузить его можно на экране «Расход». Прежние `AHREFS_MONTHLY_UNITS` и
`AHREFS_UNITS_CAP` логику не задают: оставшиеся в `.env` настройки не читают
(`DomainSettings`: лишнее в `.env` игнорируется).
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Ahrefs(DomainSettings):
    api_key: str = Field(default="", validation_alias="AHREFS_API_KEY")
    base_url: str = Field(default="https://api.ahrefs.com", validation_alias="AHREFS_BASE_URL")
    timeout_s: float = Field(default=30.0, validation_alias="AHREFS_TIMEOUT_S")
    # Запросов в минуту: у Ahrefs динамический троттлинг, держимся ниже.
    rate_limit_per_min: int = Field(default=50, validation_alias="AHREFS_RATE_LIMIT_PER_MIN")


_s = _Ahrefs()

API_KEY: str = _s.api_key.strip()  # хвостовой пробел из .env ломает заголовок
BASE_URL: str = _s.base_url
TIMEOUT_S: float = _s.timeout_s
RATE_LIMIT_PER_MIN: int = _s.rate_limit_per_min
