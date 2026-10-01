"""Модуль «Продажи»: выключатель.

**По умолчанию выключен.** Продажи пишут живым людям от имени компании,
и включение — решение человека для конкретного развёртывания, а не умолчание
кода. Сейчас выключать нечего: в модуле только данные (гипотезы и лиды)
и команда заведения гипотез. Выключатель читают срезы, которые приносят
работу продаж; первой — проверка ключей продаж на старте.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Sales(DomainSettings):
    enabled: bool = Field(default=False, validation_alias="SALES_ENABLED")


_s = _Sales()

ENABLED: bool = _s.enabled
