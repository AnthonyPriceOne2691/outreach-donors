"""RunModel и RunSettingsModel — прогон и пороги, с которыми он запущен."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    false,
)
from sqlalchemy import Enum as SQLEnum
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
    # Глубина выдачи — параметр прогона наравне с ключами. Хранится,
    # чтобы задаче хватало одного номера прогона: повтор с другой
    # глубиной был бы уже другим прогоном, а выглядел бы тем же.
    depth_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Смета до запуска и факт после. Расхождение этих двух чисел и есть
    # проверка сметы — без неё оценка расхода ничем не подтверждается.
    estimated_units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actual_units: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Сколько принято, отсеяно и по какому порогу — отчёт прогона.
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Задача очереди, которая этот прогон выполняет. Хранится потому, что
    # без неё нельзя ответить на вопрос «он ещё идёт или воркер умер»:
    # у прогона есть только время последней записи, а оно одинаково
    # выглядит и у мёртвого, и у медленного.
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Выдача, за которую уже заплачено: домены и числа по ней. Лежит
    # в строке прогона, чтобы продолжение после смерти воркера не
    # покупало её второй раз — у соседней системы продолжение бесплатно,
    # у нас каждая попытка стоит запросов к источнику выдачи.
    candidates: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    # Второй индекс — для разбора мёртвых: он спрашивает «кто давно
    # молчит» раз в минуту, и без индекса это чтение всей таблицы.
    __table_args__ = (
        Index("idx_runs_status", "status"),
        Index("idx_runs_updated_at", "updated_at"),
    )

    settings: Mapped[RunSettingsModel] = relationship("RunSettingsModel", back_populates="runs")


class RunCandidateModel(TimestampedMixin, Base):
    """Домен, который прогон предлагает человеку: принять или отклонить.

    Прогон кончается не записью в базу доноров, а очередью на рассмотрение:
    пороги отвечают «годен ли по цифрам», и у бренда цифры отличные по
    построению. Строка на пару «прогон + домен» — история решений по
    прогонам; последнее решение по домену лежит у донора (`donors.review`).
    """

    __tablename__ = "run_candidates"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    #: `pending`, `accepted`, `rejected` (`review.candidates.Decision`).
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Решение перенесено из прошлого прогона, а не принято в этом:
    #: один и тот же домен человек рассматривает один раз.
    carried: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=false())

    __table_args__ = (
        UniqueConstraint("run_id", "domain_id", name="uq_run_candidates_run_domain"),
        Index("idx_run_candidates_run_status", "run_id", "status"),
        Index("idx_run_candidates_domain", "domain_id"),
    )
