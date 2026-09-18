"""Как сервис рассказывает о себе: формат логов и их уровень."""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Observability(DomainSettings):
    # json — для прода: поля из extra остаются полями, их можно искать.
    # text — для терминала: человек читает строку, а не объект.
    log_format: str = Field(default="text", validation_alias="LOG_FORMAT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")


_s = _Observability()

LOG_FORMAT: str = _s.log_format.strip().lower()
LOG_LEVEL: str = _s.log_level.strip().upper()
