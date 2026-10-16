"""Общая база для доменных настроек.

Одно правило на весь проект: обращение к переменным окружения живёт только
здесь, в `config/`. В остальном коде — `from config import ahrefs` и
`ahrefs.API_KEY`. Иначе опечатка в имени переменной прячется от типизатора
и всплывает в проде.

**Опечатка в настройке одного модуля не роняет чужие процессы** (`spared`, ревью
стыков 12.10.2026). Настройки разбираются при импорте, а импортируют их все процессы:
негодное значение лимита продаж роняло по кругу и API с вебхуками доноров, и воркер,
и сторож, и проход добивок. Такая настройка берётся безопасным значением своего модуля,
а громкая строка журнала при старте называет её, что в ней и что взято.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class DomainSettings(BaseSettings):
    """Настройки одного домена. Читают `.env`, лишнее в нём игнорируют."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@dataclass(frozen=True, slots=True)
class Fallback:
    """Что взять вместо негодного значения настройки — и слова строки журнала."""

    #: Безопасное значение поля: с ним модуль, чья это настройка, не делает ничего опасного.
    value: object
    #: Какое значение годится — «целое число не меньше нуля».
    wants: str
    #: Что значит взятое — «ноль первых писем продаж в сутки».
    means: str


def spared[S: DomainSettings](
    model: type[S], fallbacks: Mapping[str, Fallback]
) -> tuple[S, dict[str, str]]:
    """Настройки `model`, где негодное значение настроек из `fallbacks` (по имени переменной)
    не роняет импорт: берётся `Fallback.value`, и при старте — строка журнала `error` с именем
    переменной, тем, что в ней, и тем, что взято. Негодная настройка не из `fallbacks` — отказ
    pydantic, как всегда: общие настройки по-прежнему не стартуют с опечаткой.

    Возвращает настройки и непрочитанные: имя переменной → что в ней было."""
    try:
        return model(), {}
    except ValidationError as exc:
        unread = _named(exc)
        if not unread or not unread.keys() <= fallbacks.keys():
            raise
    for name, raw in unread.items():
        _said(name, raw, fallbacks[name])
    # Значения — по имени переменной: у поля настройки имя — его переменная окружения.
    taken: dict[str, Any] = {name: fallbacks[name].value for name in unread}
    return model(**taken), unread


def _named(exc: ValidationError) -> dict[str, str]:
    """Негодные настройки из отказа pydantic: имя переменной → что в ней было."""
    return {str(error["loc"][0]): str(error.get("input")) for error in exc.errors() if error["loc"]}


def _said(name: str, raw: str, fallback: Fallback) -> None:
    """Громкая строка при старте: имя, что в настройке (начало — значение бывает длинным) и что
    взято вместо неё."""
    logger.error(
        "НАСТРОЙКА НЕ ПРОЧИТАНА — %s: в ней «%s», а годится %s; взято: %s",
        name,
        raw[:80],
        fallback.wants,
        fallback.means,
    )
