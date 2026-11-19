"""Как сервис рассказывает о себе: формат логов, их уровень и схема API."""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Observability(DomainSettings):
    # json — для прода: поля из extra остаются полями, их можно искать.
    # text — для терминала: человек читает строку, а не объект.
    log_format: str = Field(default="text", validation_alias="LOG_FORMAT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")
    # Схема API и песочница (`/api/docs`, `/api/openapi.json`). По умолчанию выключены, и в бою
    # тоже (аудит 19.11.2026): схема — карта всех ручек, а песочница грузит скрипт с чужого
    # адреса на тот же адрес, где экран держит пропуск. Включаются для разработки.
    api_docs: bool = Field(default=False, validation_alias="API_DOCS_ENABLED")


_s = _Observability()

LOG_FORMAT: str = _s.log_format.strip().lower()
LOG_LEVEL: str = _s.log_level.strip().upper()
API_DOCS: bool = _s.api_docs
