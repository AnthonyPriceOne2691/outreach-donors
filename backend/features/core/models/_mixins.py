"""Повторяющиеся колонки моделей."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime


class TimestampedMixin:
    """Пара `created_at` + `updated_at` с серверными умолчаниями.

    Подмешивается первым родителем, чтобы декларативный механизм
    SQLAlchemy скопировал колонки в подкласс с правильным владельцем.
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
