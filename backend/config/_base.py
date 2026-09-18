"""Общая база для доменных настроек.

Одно правило на весь проект: обращение к переменным окружения живёт только
здесь, в `config/`. В остальном коде — `from config import ahrefs` и
`ahrefs.UNITS_CAP`. Иначе опечатка в имени переменной прячется от типизатора
и всплывает в проде.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class DomainSettings(BaseSettings):
    """Настройки одного домена. Читают `.env`, лишнее в нём игнорируют."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
