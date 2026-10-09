"""Что читает разбор цены: сохранённый текст ответа и тексты его вложений.

Разбирается то, что лежит в базе, а не то, что пришло в вебхуке: между
приёмом и разбором проходит время, и единственный текст, за который мы
отвечаем, — сохранённый.

**Вложения читаются здесь, перед моделью, а не при приёме.** Чтение файла —
отдельный процесс и до двадцати секунд на файл (`attachment_text`), а вебхук
обязан отвечать быстро: платформа повторяет его по таймауту. Прочитанное
ложится к вложению (`attachments.ReplyFiles.texts`) — экран показывает
человеку ровно то, что видела модель.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.outreach import ReplyModel
from backend.features.replies.attachments import ReplyFiles
from backend.features.replies.inbound import Incoming


async def parse_input(
    session: AsyncSession, reply: ReplyModel, *, now: datetime | None = None
) -> Incoming:
    """Сохранённый ответ обратно во входящее письмо — ровно настолько, насколько
    это нужно разбору: текст, тема и тексты вложений. `now` — когда файлы
    читаны; пусто — сейчас."""
    files = await ReplyFiles(session).texts(reply.id, now=now or datetime.now(UTC))
    return Incoming(
        message_id=reply.inbound_message_id or "",
        to=(),
        from_email=reply.from_email or "",
        subject=reply.subject or "",
        text=reply.raw_body,
        attached=tuple(files),
    )
