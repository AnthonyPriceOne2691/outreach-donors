"""Хранилище: база и очередь."""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Storage(DomainSettings):
    dsn: str = Field(
        default="postgresql+asyncpg://outreach:outreach@localhost:5442/outreach",
        validation_alias="STORAGE_DSN",
    )
    redis_url: str = Field(default="redis://localhost:6389/0", validation_alias="STORAGE_REDIS_URL")
    echo_sql: bool = Field(default=False, validation_alias="STORAGE_ECHO_SQL")


_s = _Storage()

DSN: str = _s.dsn
REDIS_URL: str = _s.redis_url
ECHO_SQL: bool = _s.echo_sql
