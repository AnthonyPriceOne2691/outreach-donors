"""Модуль «Продажи»: выключатель и проверяльщик адресов.

**По умолчанию выключен.** Продажи пишут живым людям от имени компании,
и включение — решение человека для конкретного развёртывания, а не умолчание
кода. Сейчас выключать нечего: в модуле только данные (гипотезы и лиды)
и команды консоли. Выключатель читают срезы, которые приносят работу
продаж; первой — проверка ключей продаж на старте.

**Проверяльщик адресов по умолчанию выдуманный (`fixture`).** Живой стоит
денег с ключа, общего с соседней системой, и на него переключает человек:
`SALES_VERIFIER_PROVIDER=live`. Ключ — тот же `CONTACTS_HUNTER_API_KEY`,
что у поиска адресов: сервис один, второй переменной для него не заводим.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Sales(DomainSettings):
    enabled: bool = Field(default=False, validation_alias="SALES_ENABLED")
    # `fixture` — вердикты по правилам из адреса, без сети и денег;
    # `live` — Hunter Email Verifier тем же ключом, что ступень 3 поиска.
    verifier_provider: str = Field(default="fixture", validation_alias="SALES_VERIFIER_PROVIDER")


_s = _Sales()

ENABLED: bool = _s.enabled
VERIFIER_PROVIDER: str = _s.verifier_provider
