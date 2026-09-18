"""DomainModel — каноничная запись о сайте.

Якорь всей схемы. Отдельная сущность, а не поле донора, по одной причине:
один и тот же сайт бывает донором в Этапе 1 и рекламодателем в Этапе 2.
Сквозной стоп-лист адресатов и дедупликация держатся на этом уровне.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base

if TYPE_CHECKING:
    from backend.features.core.models.donor import ContactModel, DonorModel


class DomainModel(TimestampedMixin, Base):
    __tablename__ = "domains"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Корневой домен без протокола, www и поддоменов — ключ дедупликации.
    host: Mapped[str] = mapped_column(String(253), nullable=False, unique=True)

    __table_args__ = (Index("idx_domains_host", "host"),)

    donor: Mapped[DonorModel | None] = relationship(
        "DonorModel", back_populates="domain", uselist=False
    )
    contacts: Mapped[list[ContactModel]] = relationship(
        "ContactModel", back_populates="domain", cascade="all, delete-orphan"
    )
