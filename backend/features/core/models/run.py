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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.features.core.domain import RunStatus, Stage
from backend.features.core.models._mixins import TimestampedMixin
from backend.features.core.models._mixins import sql_enum as _enum
from backend.shared.database.base import Base


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
    #: Крупный сайт: DR выше — «не подходит», ему не пишем (ТЗ: «домены DR > 80»).
    #: До 12.10.2026 граница была числом в коде; строки до того — 80.
    max_dr: Mapped[int] = mapped_column(Integer, nullable=False, server_default="80")

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
    #: Режим покупки выдачи (`serp/modes.SerpMode` строкой), выбранный на запуске.
    #: Пусто — прогон до 14.10.2026 или продолжение без выбора: умолчание сервиса.
    serp_mode: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Прогон по ссылкам конкурентов (`runs/competitors.py`): домены конкурентов, глубина
    #: и что уже получено от Ahrefs — продолжение не покупает списки второй раз. Пусто —
    #: прогон по ключам. `none_as_null`: пусто — это поле без значения, а не JSON `null`,
    #: иначе отбор «только прогоны по выдаче» (`IS NULL`) молча терял бы прогоны по ключам.
    competitors: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True), nullable=True
    )

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


class RunDomainMixin:
    """Строка на пару «прогон + домен»: кандидат очереди (`RunCandidateModel`) и вердикт прогона
    (`RunVerdictModel`). Подмешивается первым родителем, как `TimestampedMixin`."""

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), nullable=False)
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )


class RunCandidateModel(RunDomainMixin, TimestampedMixin, Base):
    """Домен, который прогон предлагает человеку: принять или отклонить.

    Прогон кончается не записью в базу доноров, а очередью на рассмотрение:
    пороги отвечают «годен ли по цифрам», и у бренда цифры отличные по
    построению. Строка на пару «прогон + домен», а решение по домену одно
    (решение владельца 11.10.2026): строка прогона, где решали, — само
    решение, строки домена в остальных прогонах — его копия (`carried`),
    у донора — то же решение (`donors.review`). Пишет их `review.candidates`.
    """

    __tablename__ = "run_candidates"

    #: `pending`, `accepted`, `rejected` (`review.candidates.Decision`).
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(String(512), nullable=True)
    #: Строка — копия решения, принятого в другом прогоне (раньше этого или
    #: позже), а не само решение: один и тот же домен человек решает один раз.
    #: Автора, времени и записки у копии нет — они у решения и у донора.
    carried: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=false())
    #: Положил человек из резерва прогона (`review/reserve.py`, с 20.10.2026): кто и когда.
    #: Пусто — положил прогон, годного по своему вердикту. У взятого из резерва вердикт
    #: прогона «не подходит», и очередь показывает почему.
    taken_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("run_id", "domain_id", name="uq_run_candidates_run_domain"),
        Index("idx_run_candidates_run_status", "run_id", "status"),
        Index("idx_run_candidates_domain", "domain_id"),
    )


class RunVerdictModel(RunDomainMixin, TimestampedMixin, Base):
    """Вердикт домена в прогоне — страной и порогами этого прогона (аудит 20.10.2026).

    Вердикт на домене (`donors.status`) — последнего замера, без страны и порогов, для
    которых его вынесли; свежий домен прогон не перемерял, и годный для США шёл в очередь
    прогона Канады. Теперь каждый прогон судит каждый свой домен сам: купленный сейчас —
    замером (`measured`), свежий — по сохранённым метрикам и странам (`donors/rejudge.py`),
    без Ahrefs. По этой строке прогон кладёт годных в очередь и называет, почему остальные
    не прошли. Пишет её `runs/verdicts.py`; страна и пороги — прогона (`runs.country`,
    `runs.settings_id`): по ним вердикт объясним и через полгода.
    """

    __tablename__ = "run_verdicts"

    #: `DonorStatus` строкой: подходит, не подходит, не проверен — или `excluded`: домен
    #: снял гейт до Ahrefs (`runs.verdicts.EXCLUDED`, с резервом 20.10.2026).
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Почему не прошёл — кодом: порог отказа (`runs.refusals.Threshold`) у «не подходит»,
    #: причина гейта (`runs.exclusions.ExclusionReason`) у исключённого, `gap` у «не проверен»
    #: из-за нехватки данных (`rejudge.DATA_GAPS`); иначе пусто. По нему фильтруют и называют
    #: причину словами экрана.
    cause: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Причина словами вердикта: почему не подходит, не проверен — или чем подходит.
    reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: Данные куплены этим прогоном; иначе — вердикт по сохранённым, без Ahrefs.
    measured: Mapped[bool] = mapped_column(nullable=False, default=False, server_default=false())

    __table_args__ = (
        UniqueConstraint("run_id", "domain_id", name="uq_run_verdicts_run_domain"),
        Index("idx_run_verdicts_run_status", "run_id", "status"),
        Index("idx_run_verdicts_domain", "domain_id"),
    )
