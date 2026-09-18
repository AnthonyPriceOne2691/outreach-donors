"""Каркас Ф1: схема собирается, конфиг читается, инварианты на месте."""

from __future__ import annotations

from backend.config import ahrefs, filters, outreach, serp, storage
from backend.features.core import models
from backend.shared.database.base import Base

EXPECTED_TABLES = {
    "domains",
    "donors",
    "contacts",
    "senders",
    "campaigns",
    "threads",
    "messages",
    "replies",
    "runs",
    "run_settings",
    "usage_records",
    "suppressions",
    # Срез доступа: учётки и журнал действий.
    "users",
    "audit_log",
}


def test_all_entities_registered() -> None:
    """Все таблицы видны метаданным.

    Не косметика: Alembic генерирует миграции из этого же реестра, и модель,
    забытая в `models/__init__.py`, молча не попадёт в схему.
    """
    assert set(Base.metadata.tables) == EXPECTED_TABLES


def test_models_exported() -> None:
    assert len(models.__all__) == len(EXPECTED_TABLES)


def test_domain_host_is_unique() -> None:
    """Хост — ключ дедупликации. Без уникальности повторный прогон
    заведёт второй домен и заплатит за него юнитами второй раз."""
    assert Base.metadata.tables["domains"].c.host.unique is True


def test_message_idempotency_key_is_unique() -> None:
    """Защита от второго письма тому же донору при ретрае задачи."""
    constraints = {c.name for c in Base.metadata.tables["messages"].constraints}
    assert "uq_messages_idempotency" in constraints


def test_donor_one_per_domain() -> None:
    """Донор — роль домена, а не отдельная его копия."""
    assert Base.metadata.tables["donors"].c.domain_id.unique is True


def test_filters_defaults_match_spec() -> None:
    """Пороги по умолчанию — из ответов  и ."""
    assert filters.MIN_DR == 20
    assert filters.MIN_ORG_TRAFFIC == 500
    assert filters.MIN_REFDOMAINS == 100
    assert filters.MIN_KEYWORDS == 300
    assert filters.GEO_TOP_N == 5
    assert filters.GEO_MIN_SHARE == 0.20
    assert filters.METRICS_TTL_DAYS == 90
    assert filters.PRICE_TTL_DAYS == 150


def test_outreach_defaults_match_spec() -> None:
    """Добивки на 7-й и 14-й день; уникализация 15–25%."""
    assert outreach.FOLLOWUP_DAYS == (7, 14)
    assert outreach.UNIQUENESS_TARGET_MIN == 0.15
    assert outreach.UNIQUENESS_TARGET_MAX == 0.25


def test_config_modules_load() -> None:
    """Конфиг читается без .env — умолчания самодостаточны."""
    assert storage.DSN.startswith("postgresql+asyncpg://")
    assert serp.DEPTH_PAGES == 1  # топ-10
    assert ahrefs.UNITS_CAP == 100_000  # кап расхода
