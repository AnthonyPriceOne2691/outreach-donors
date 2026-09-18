"""DonorModel и ContactModel — донор как роль домена и его адреса."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    DECIMAL,
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import DateTime

from backend.features.core.domain import ContactSource, ContactStatus, DonorStatus
from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base

if TYPE_CHECKING:
    from backend.features.core.models.domain import DomainModel


def _enum(e: type) -> SQLEnum:
    return SQLEnum(e, values_callable=lambda x: [i.value for i in x])


class DonorModel(TimestampedMixin, Base):
    """Роль домена в нашем процессе: проверен, отобран, опрошен по цене."""

    __tablename__ = "donors"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    status: Mapped[DonorStatus] = mapped_column(
        _enum(DonorStatus), nullable=False, default=DonorStatus.UNCHECKED
    )
    # По какому именно порогу отсеян — требование отчёта прогона.
    reject_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- Метрики Ahrefs ---
    # Сырой ответ в JSONB: набор полей ещё будет меняться.
    metrics: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    # Пусто при непустой отметке = «проверяли, данных нет». Без этой
    # пары каждый прогон заново жёг бы юниты на доменах вне индекса.
    metrics_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Подняты из метрик отдельными колонками: по ним фильтруем и сортируем.
    dr: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # BigInteger, а не Integer: у крупных доноров органический трафик
    # переваливает за два миллиарда и в 32 бита не влезает. Поймано боевым
    # прогоном — reddit.com отдаёт 939 млн, и это ещё не предел.
    org_traffic: Mapped[int | None] = mapped_column(BigInteger, nullable=True)

    # --- Гео ---
    geo: Mapped[str | None] = mapped_column(String(8), nullable=True)  # ISO-2 нижним регистром
    # ТОП-5 стран с долями, по убыванию: [{"country": "us", "share": 0.62}, ...].
    # Именно пять, а не только доли > 20%: правило  требует проверить
    # вхождение в топ-5, и страна с долей 12% на третьем месте нам подходит.
    geo_breakdown: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB, nullable=True)
    geo_top_share: Mapped[float | None] = mapped_column(Float, nullable=True)
    geo_refreshed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- Цена (срок годности 150 дней) ---
    last_price_usd: Mapped[Decimal | None] = mapped_column(DECIMAL(10, 2), nullable=True)
    last_price_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # --- Контакт ---
    contact_status: Mapped[ContactStatus | None] = mapped_column(
        _enum(ContactStatus), nullable=True
    )
    contact_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index("idx_donors_status", "status"),
        Index("idx_donors_geo", "geo"),
        Index("idx_donors_dr", "dr"),
        # Отбор «кому пора обновить метрики» — по этой паре.
        Index("idx_donors_metrics_refreshed", "metrics_refreshed_at"),
    )

    domain: Mapped[DomainModel] = relationship("DomainModel", back_populates="donor")


class ContactModel(TimestampedMixin, Base):
    """Адрес сайта. Принадлежит домену, а не донору: в Этапе 2 тот же адрес
    может понадобиться, когда сайт выступает рекламодателем."""

    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    domain_id: Mapped[int] = mapped_column(
        ForeignKey("domains.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[ContactSource] = mapped_column(_enum(ContactSource), nullable=False)

    verification_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    verification_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Дешёвая проверка задержки перед отправкой, без похода в письма.
    last_contacted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Когда с этого адреса ответили. Отвечающий адрес важнее найденного:
    # дальше пишем тому, кто отвечает, а не в ящик, где письмо пролежало
    # неделю. Отметка, а не флаг «предпочтительный»: она говорит ещё и когда.
    last_replied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("domain_id", "email", name="uq_contacts_domain_email"),
        Index("idx_contacts_domain_id", "domain_id"),
    )

    domain: Mapped[DomainModel] = relationship("DomainModel", back_populates="contacts")
