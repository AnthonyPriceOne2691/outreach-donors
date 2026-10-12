"""Журнал отправки недельного отчёта продаж в группу продаж (срез 6.3) — своя таблица модуля.

Своим модулем, а не в `models.py`: тот у предела длины файла. Модель регистрирует реестр моделей
(`core/models/__init__.py`), как и остальные таблицы продаж.

**Строка — неделя** (`week_start` — её понедельник по Москве), одна: уникальный ключ держит
«отчёт за неделю уходит один раз» — повтор прохода и второй процесс строку не задвоят, а
отправленную не пошлют снова. Числа отчёта здесь не хранятся: экран и выгрузка считают их
заново теми же функциями (`weekly.py`); в строке — текст, ушедший в группу, как его прочёл
человек, и исход отправки.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import Date, DateTime, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.features.core.models._mixins import TimestampedMixin
from backend.shared.database.base import Base


class WeeklyStatus(StrEnum):
    """Чем кончилась отправка отчёта недели. Строкой, а не типом базы, как `NoticeStatus`:
    новый исход — строка здесь, а не миграция типа."""

    PENDING = "pending"  # строка заведена проходом, задача ещё не отработала
    SENT = "sent"  # Telegram принял отчёт
    UNDELIVERED = "undelivered"  # не ушёл: ждёт повтора прохода (`due_at`) или повторов нет


class SalesWeeklyReportModel(TimestampedMixin, Base):
    """Отправка недельного отчёта в группу продаж: неделя, исход, текст, повтор."""

    __tablename__ = "sales_weekly_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    #: Понедельник недели отчёта по Москве; неделя — с него по воскресенье включительно.
    week_start: Mapped[date] = mapped_column(Date, nullable=False)
    #: `WeeklyStatus` строкой.
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: Что ушло в группу (или не ушло) — так, как его прочёл бы человек. Пусто — не собирали.
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Почему не доставлен — словами бота, без токена.
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: Неудач подряд в серии повторов (`telegram_series.py`); ушёл или итог сказан — ноль.
    tries: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0, server_default="0")
    #: Не раньше чего проход возьмёт строку (`weekly_jobs.due`). Пусто — брать нечего.
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Когда Telegram принял отчёт.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (UniqueConstraint("week_start", name="uq_sales_weekly_reports_week"),)
