"""RunModel и RunSettingsModel — прогон и пороги, с которыми он запущен."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import Float, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.features.core.domain import RunStatus, Stage
from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


def _enum(e: type) -> SQLEnum:
    return SQLEnum(e, values_callable=lambda x: [i.value for i in x])


class RunSettingsModel(TimestampedMixin, Base):
    """Пороги отбора. Версионируются намеренно: смена порога не должна
    переписывать вердикты прошлых прогонов — иначе непонятно, почему
    полгода назад домен отсеялся."""

    __tablename__ = "run_settings"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)

    min_dr: Mapped[int] = mapped_column(Integer, nullable=False)
    min_org_traffic: Mapped[int] = mapped_column(Integer, nullable=False)
    min_refdomains: Mapped[int] = mapped_column(Integer, nullable=False)
    min_keywords: Mapped[int] = mapped_column(Integer, nullable=False)

    # Гео-правило: страна в топ-N ИЛИ доля ≥ порога.
    geo_top_n: Mapped[int] = mapped_column(Integer, nullable=False)
    geo_min_share: Mapped[float] = mapped_column(Float, nullable=False)

    metrics_ttl_days: Mapped[int] = mapped_column(Integer, nullable=False)
    price_ttl_days: Mapped[int] = mapped_column(Integer, nullable=False)
    units_cap: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (Index("idx_run_settings_version", "version"),)

    runs: Mapped[list[RunModel]] = relationship("RunModel", back_populates="settings")


class RunModel(TimestampedMixin, Base):
    """Единица работы: список ключей и страна на входе, база доноров на выходе."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    stage: Mapped[Stage] = mapped_column(_enum(Stage), nullable=False)
    settings_id: Mapped[int] = mapped_column(ForeignKey("run_settings.id"), nullable=False)
    status: Mapped[RunStatus] = mapped_column(
        _enum(RunStatus), nullable=False, default=RunStatus.ESTIMATING
    )

    keywords: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    country: Mapped[str] = mapped_column(String(8), nullable=False)

    # Смета до запуска и факт после. Расхождение этих двух чисел и есть
    # проверка сметы — без неё оценка расхода ничем не подтверждается.
    estimated_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_units: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Сколько принято, отсеяно и по какому порогу — отчёт прогона.
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    __table_args__ = (Index("idx_runs_status", "status"),)

    settings: Mapped[RunSettingsModel] = relationship("RunSettingsModel", back_populates="runs")
