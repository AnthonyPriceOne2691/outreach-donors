"""UsageRecordModel и SuppressionModel — расход и стоп-лист."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DECIMAL, DateTime, ForeignKey, Index, Integer, String, or_
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

    __table_args__ = (
        # Основной запрос — «сколько потрачено за месяц по провайдеру».
        Index("idx_usage_provider_created", "provider", "created_at"),
        Index("idx_usage_run_id", "run_id"),
    )


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
