"""Окончательное решение по ответу снимает ожидание с ранних ответов того же диалога.

Проверка прода 09.11.2026: лид ответил неясно (одно имя) — ответ ждал человека; следующим письмом
назвал адрес коллеги — модуль завёл нового лида и закрыл диалог, а первый ответ так и висел в
«ждёт человека». Решение по более позднему ответу — диалог закрыт, отсылка к коллеге, отписка,
передача телемаркетологу — отвечает и на ранние: они перестают ждать словами «решён следующим
ответом №N: <вид>» (решение владельца 09.11: «править логику, конечно, давай сделаем»).

**Что снимается.** Ответы человека этого диалога с меньшим номером, которые ждут человека
(`threads.review_of`) и уже разобраны — в снимке есть вид. Неразобранный ответ (отказ модели,
продажи были выключены) ждёт дальше: снимок без вида задача разбора возьмёт снова, и закрытый здесь
он бы снова открылся. Поля решения человека (`reviewed_*`) не трогаются: решал сервис, а не
человек. Не коммитит: снимки ложатся тем же коммитом, что и решение по позднему ответу.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.outreach.threads import review_of

logger = logging.getLogger(__name__)

#: Чем ранний ответ снят с ожидания — словами; дальше — номер позднего ответа и его вид.
LATER = "решён следующим ответом"


async def settle_earlier(session: AsyncSession, reply: ReplyModel, words: str) -> tuple[int, ...]:
    """Снять ожидание с ранних разобранных ответов диалога `reply` словами решения по нему."""
    if reply.thread_id is None:
        return ()
    earlier = (
        await session.scalars(
            select(ReplyModel)
            .where(ReplyModel.thread_id == reply.thread_id, ReplyModel.id < reply.id)
            .order_by(ReplyModel.id)
        )
    ).all()
    said = f"{LATER} №{reply.id}: {words}"
    settled = tuple(one.id for one in earlier if _settle(one, said))
    if settled:
        logger.info(
            "продажи: ранние ответы диалога решены следующим",
            extra={"reply": reply.id, "thread_id": reply.thread_id, "settled": settled},
        )
    return settled


def _settle(reply: ReplyModel, said: str) -> bool:
    snapshot = reply.model_parse or {}
    if not snapshot.get("kind") or not review_of(reply, Stage.SALES).waiting:
        return False
    reply.model_parse = {**snapshot, "waits": False, "reason": said}
    return True
