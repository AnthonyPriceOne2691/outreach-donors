"""Вложение входящего ответа: сведения о файле и сам файл."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Index, Integer, LargeBinary, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from backend.shared.database.base import Base


class ReplyAttachmentModel(Base):
    """Файл, пришедший с ответом донора.

    **Файл лежит здесь, потому что другой копии нет нигде.** Платформа
    приёма — единственный получатель письма: не сохранённый при приёме
    прайс потерян навсегда, и человеку открыть его нечем.

    **Сведения пишутся и о файле, который не сохранён** — опасном,
    лишнем, слишком большом: причина лежит рядом словами. «Прайс не
    присылали» и «прайс прислали, но мы его не взяли» — разные ответы
    донора, и различать их человек должен по карточке, а не по логам.

    Тело файла не грузится вместе со строкой (`deferred`): карточка диалога
    читает сведения о десятке файлов, и мегабайты их содержимого ей не нужны.
    """

    __tablename__ = "reply_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    reply_id: Mapped[int] = mapped_column(
        ForeignKey("replies.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Тип, который назвал отправитель. Только для показа: отдаётся файл
    #: всегда как двоичный, иначе присланный HTML открылся бы страницей
    #: с нашего адреса — чужой скрипт с пропуском сотрудника.
    content_type: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Байт. Пусто — размер неизвестен: файл назван, но не пришёл.
    size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    data: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True, deferred=True)
    #: Сохранён и отдаётся. Не сохранённый — только сведения и причина.
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (Index("idx_reply_attachments_reply_id", "reply_id"),)
