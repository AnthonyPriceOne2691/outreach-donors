"""UsageRecordModel, AhrefsCapModel и SuppressionModel — расход, кап Ahrefs и стоп-лист."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DECIMAL,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
    or_,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.elements import ColumnElement

from backend.features.core.domain import Stage, SuppressionReason, UsageProvider
from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


def _enum(e: type) -> SQLEnum:
    return SQLEnum(e, values_callable=lambda x: [i.value for i in x])


class UsageRecordModel(TimestampedMixin, Base):
    """Построчный расход. Остаток лимита НЕ хранится полем: два независимых
    счётчика неизбежно разойдутся. Остаток = месячный лимит минус сумма строк.

    Поле `system` существует потому, что ключ Ahrefs общий с сервисом
    соседней системой: обе системы пишут сюда, и каждая перед прогоном видит
    общий остаток, а не свой.
    """

    __tablename__ = "usage_records"

    id: Mapped[int] = mapped_column(primary_key=True)
    system: Mapped[str] = mapped_column(String(32), nullable=False)
    provider: Mapped[UsageProvider] = mapped_column(_enum(UsageProvider), nullable=False)
    run_id: Mapped[int | None] = mapped_column(
        ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    operation: Mapped[str] = mapped_column(String(64), nullable=False)
    units: Mapped[int | None] = mapped_column(Integer, nullable=True)
    amount_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 4), nullable=True)
    #: На какой этап ушла трата — расход Этапа 2 считается отдельно от Этапа 1 (требование
    #: к расходу Этапа 2). Пусто — общая на все этапы (отправка письма) или этап не назван:
    #: операцию делят этапы, а вызов его не сказал, либо строка старше 12.10.2026.
    stage: Mapped[Stage | None] = mapped_column(_enum(Stage), nullable=True)

    __table_args__ = (
        # Основной запрос — «сколько потрачено за месяц по провайдеру».
        Index("idx_usage_provider_created", "provider", "created_at"),
        Index("idx_usage_run_id", "run_id"),
    )


class AhrefsCapModel(Base):
    """Месячный кап юнитов Ahrefs, заданный на экране «Расход»: строка на каждую смену.

    Действующий кап — последняя строка; строк нет — умолчание окружения
    (`AHREFS_UNITS_CAP`, `runs/ahrefs_cap.py`). Строка не правится и не удаляется:
    по строкам видно, кто и когда менял кап, — как по версиям порогов (требование:
    «видно, кто и когда менял»). Остаток капа полем не хранится — он считается
    по журналу расхода (`runs/spending.cap_left`).
    """

    __tablename__ = "ahrefs_caps"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Юнитов в месяц; не больше лимита тарифа — проверяет смена (`set_ahrefs_cap`).
    units_cap: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Кто поставил. Пусто — учётку удалили: строка истории остаётся и без неё.
    set_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (CheckConstraint("units_cap > 0", name="ck_ahrefs_caps_positive"),)


class SuppressionModel(TimestampedMixin, Base):
    """Кому не пишем. Общий на оба этапа: один и тот же адресат не должен
    получить письмо и как донор, и как рекламодатель.

    Проверяется перед каждой отправкой, без исключений.
    """

    __tablename__ = "suppressions"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int | None] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=True
    )
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    reason: Mapped[SuppressionReason] = mapped_column(_enum(SuppressionReason), nullable=False)
    # Пусто = действует на обоих этапах.
    stage: Mapped[Stage | None] = mapped_column(_enum(Stage), nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    #: До какого момента запись держит. Пусто — навсегда, и это умолчание:
    #: требование даёт срок только тем, кого мы внесли сами (размещались
    #: за последние 12 месяцев), а отписка и жалоба бессрочны.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("idx_suppressions_domain_id", "domain_id"),
        Index("idx_suppressions_email", "email"),
    )

    @classmethod
    def in_force(cls, moment: datetime) -> ColumnElement[bool]:
        """Условие «запись ещё держит». Живёт рядом с полем намеренно.

        Читателей у стоп-листа пять — отбор прогона, очередь писем,
        отправка, перевод в рекламодатели и экран, — и забытый срок
        у любого из них означает письмо тому, кому писать нельзя,
        либо молчание тому, кому уже можно.
        """
        return or_(cls.expires_at.is_(None), cls.expires_at > moment)
