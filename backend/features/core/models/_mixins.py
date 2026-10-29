"""Повторяющиеся колонки моделей."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Enum as SQLEnum
from sqlalchemy import ForeignKey, SmallInteger, Text, func
from sqlalchemy.orm import Mapped, declared_attr, mapped_column
from sqlalchemy.types import DateTime

from backend.features.core.domain import ContactStatus


def sql_enum(enum: type) -> SQLEnum:
    """Колонка-перечисление по значениям членов, а не по именам: в базе `"queued"`, а не `"QUEUED"`.

    Одна на все модели: до 20.10.2026 эти две строки были скопированы в семь файлов (`_enum`).
    """
    return SQLEnum(enum, values_callable=lambda x: [i.value for i in x])


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


class SetByMixin:
    """Кто и когда завёл строку истории смены: `set_by` — учётка (пусто — её удалили, а строка
    истории осталась), `set_at` — минута смены. Одна пара у смен капа Ahrefs и потолков модели
    на «Расходе»: по ним видно, кто, когда и что ставил.

    Внешний ключ — через `declared_attr`: колонку с ним каждой модели заводят свою."""

    @declared_attr
    def set_by(cls) -> Mapped[int | None]:  # noqa: N805 — declared_attr зовётся на классе
        return mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    set_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ContactAttemptMixin:
    """Исход поиска адреса у роли домена — одна схема на все очереди.

    Очередей у поиска несколько (доноры, рекламодатели, дальше — кандидаты
    продаж), а правило «кому пора искать» и запись исхода одни
    (`contacts/attempts.py`). Поэтому и колонки одни: разойдись они, правило
    пришлось бы писать под каждую очередь своё, и копии разъехались бы.
    """

    contact_status: Mapped[ContactStatus | None] = mapped_column(
        sql_enum(ContactStatus), nullable=True
    )
    contact_attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Проходов подряд, на которых сайт не ответил. Ответ сайта обнуляет.
    contact_tries: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, server_default="0"
    )
    #: Почему исход не окончательный — словами: «нет ответа: обрыв или таймаут».
    contact_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    if TYPE_CHECKING:
        #: Домен роли. Внешний ключ у каждой таблицы свой, а правило очереди
        #: находит строку по нему — объявлено только для типизатора.
        domain_id: Mapped[int]
