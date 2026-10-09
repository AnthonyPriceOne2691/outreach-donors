"""Вложение нашего письма: файл, который человек прикладывает к ответу собеседнику."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import DateTime

from backend.shared.database.base import Base


class OutgoingAttachmentModel(Base):
    """Файл к нашему ответу в переписке.

    **Файл живёт раньше письма.** Человек прикладывает его, пока пишет ответ:
    строка заводится с перепиской и без письма, а письмо ответа получает её,
    когда заводится само (`letters/outgoing_store.py`). Неотправленный файл
    можно убрать; приложенный к письму — нет: он ушёл или уйдёт вместе с ним.

    **Тип — наш, а не браузера** (`letters/outgoing_files.py`): его назначает
    белый список по расширению, сверенному с содержимым. Тип, который прислал
    браузер, пишет та же сторона, что и сам файл, и он не читается вовсе.

    **Письмо удаляется вместе со своими файлами** (каскад по `message_id`):
    иначе ушедший файл, оставшись без письма, выглядел бы ждущим отправки и
    ушёл бы второй раз с другим ответом.

    Тело файла не грузится вместе со строкой (`deferred`), как у вложений
    ответа: карточке переписки нужны имена и размеры, а не мегабайты.
    """

    __tablename__ = "outgoing_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    thread_id: Mapped[int] = mapped_column(
        ForeignKey("threads.id", ondelete="CASCADE"), nullable=False
    )
    #: Письмо, с которым файл уходит. Пусто — приложен к переписке, но ещё
    #: ни с одним письмом не ушёл, и его можно убрать.
    message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="CASCADE"), nullable=True
    )
    #: Имя, очищенное правилами файла: последнее звено пути, без управляющих
    #: и невидимых знаков, с расширением.
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    content_type: Mapped[str] = mapped_column(String(255), nullable=False)
    #: Байт.
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[bytes] = mapped_column(LargeBinary, nullable=False, deferred=True)
    #: Кто приложил. Пусто — учётку удалили.
    uploaded_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("idx_outgoing_attachments_thread_id", "thread_id"),
        Index("idx_outgoing_attachments_message_id", "message_id"),
    )
