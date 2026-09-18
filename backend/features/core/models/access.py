"""Учётки и журнал действий.

Две таблицы, и обе про один вопрос: кто это был. Пароль хранится хешем,
права — ролью плюс точечными исключениями, а всё, что меняет состояние
или тратит деньги, попадает в журнал вместе с автором.

**Учётка отключается, а не удаляется.** Удалённый пользователь унёс бы
с собой историю: кто менял пороги, кто отправил письмо. Ссылка из
журнала на несуществующего человека — это потерянный ответ на главный
вопрос журнала.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, func
from sqlalchemy import Enum as SQLEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


def _enum(e: type) -> SQLEnum:
    return SQLEnum(e, values_callable=lambda x: [i.value for i in x])


class UserModel(TimestampedMixin, Base):
    """Сотрудник с доступом в сервис."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Почта — и логин, и способ отличить людей в журнале. Хранится
    # приведённой к нижнему регистру: иначе один человек заводится дважды.
    email: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[UserRole] = mapped_column(_enum(UserRole), nullable=False)

    # Точечные права поверх роли: `{"send": true}` добавляет действие,
    # `{"send": false}` отбирает. Отправка писем выдаётся именно так —
    # скомпрометированная учётка оператора не должна давать рассылку.
    permissions: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Пароль выдан разовый: до смены человек может только сменить пароль.
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("idx_users_email", "email"),)


class AuditLogModel(Base):
    """Запись журнала: кто, что, когда и над чем.

    Без `updated_at` намеренно: запись журнала не правится. Правка
    журнала лишает его единственного смысла.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Автор может быть неизвестен: неудачный вход — это тоже событие,
    # и почта оттуда остаётся в `details`.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[AuditAction] = mapped_column(_enum(AuditAction), nullable=False)
    # На что подействовали: «donor:1234», «user:7», «run:88».
    target: Mapped[str | None] = mapped_column(String(64), nullable=True)
    details: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_audit_created", "created_at"),
        Index("idx_audit_user", "user_id"),
    )
