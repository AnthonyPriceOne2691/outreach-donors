"""Первое письмо переписки — его тема и наш `Message-ID`: по ним добивка ложится в его ветку.

Отдельно от прохода добивок (`followups.py`): тот упёрся в потолок длины файла. Оба ответа
читают одно и то же первое письмо переписки — то, что ушло, а не снятое.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.outreach import MessageModel
from backend.features.letters.chain import FIRST_STEP


async def subject(session: AsyncSession, thread_id: int | None) -> str | None:
    """Тема первого письма переписки — того, что ушло (с нашим `Message-ID`), а не снятого."""
    if thread_id is None:
        return None
    found = await session.scalar(
        select(MessageModel.subject)
        .where(MessageModel.thread_id == thread_id, MessageModel.step == FIRST_STEP)
        .order_by(MessageModel.internet_message_id.is_(None), MessageModel.id)
        .limit(1)
    )
    return found.strip() if found and found.strip() else None


async def anchor(session: AsyncSession, thread_id: int | None) -> str | None:
    """Наш `Message-ID` первого письма переписки.

    Берётся именно первое, а не предыдущее: у почтового клиента
    донора вся цепочка должна лежать одной веткой, и якорь у неё
    один. Предыдущее письмо дало бы лесенку из вложенных ответов.

    Именно наш идентификатор, а не номер письма у платформы: номер
    получатель не видит, и `In-Reply-To` с ним не кладёт добивку никуда.
    """
    if thread_id is None:
        return None
    return await session.scalar(
        select(MessageModel.internet_message_id)
        .where(
            MessageModel.thread_id == thread_id,
            MessageModel.step == FIRST_STEP,
            MessageModel.internet_message_id.is_not(None),
        )
        .order_by(MessageModel.id)
        .limit(1)
    )
