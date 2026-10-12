"""Агент понял «хочет говорить» — лид передаётся телемаркетологу тем же путём, что по виду ответа.

Вид ответа назвал «вопрос» или «интерес», ответ ушёл агенту, а агент увидел в письме
«давайте созвонимся» (ситуация `wants_to_talk`, ход «лид» — `moves.toml`). Письма нет: бриф
пропускает его словами «агент понял: хочет говорить» (`moves.LEAD_SKIP`), а шов агента зовёт
крючок пропуска этапа (`parts.on_skip`) — он ставит эту задачу в очередь продаж. Задача — та же
передача, что у вида ответа (`reply_handoff.pass_on` → `handoff.start`: одна на диалог, Kommo и
Telegram, повторы проходом), и тот же итог в снимке ответа: «агент понял: хочет говорить —
передан телемаркетологу», человека ответ не ждёт; отказ — словами, ждёт.

**Задачей, а не в крючке**: крючок зовут из задачи черновика и из запроса «написать заново», а
передача коммитит сессию и ставит свою задачу. Очередь не ответила — строка в журнал: ответ ждёт
человека, а причина пропуска «агент понял: хочет говорить» — у черновика на виду.

Агент продаж выключен (`SALES_AGENT_ENABLED`) — черновиков и ситуации нет: лида передаёт только
вид ответа, как раньше.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.models.outreach import ReplyModel
from backend.features.sales import handoff, reply_handoff
from backend.features.sales.agent import moves
from backend.features.sales.reply_handoff import Ask, HandOver
from backend.shared.logs import setup_logging
from backend.shared.queue import sales_queue, with_retries

if TYPE_CHECKING:
    from backend.features.agent.stages import DraftNotice

logger = logging.getLogger(__name__)

#: Путь задачи строкой: очередь импортирует её в воркере.
HANDOVER_JOB = "backend.features.sales.agent.handover.hand_over_understood"
#: Начало причины в снимке ответа и строки журнала.
UNDERSTOOD = "агент понял: хочет говорить"


def queue_hand_over(notice: DraftNotice) -> None:
    """Пропуск «хочет говорить» — передача лида задачей очереди продаж. Другой пропуск —
    ничего; очередь не ответила — строка в журнал. Вторая задача на тот же ответ («написать
    заново») не вредит: передача одна на диалог, а повтор после починки (лиды-дубли разобраны)
    так и доходит до Kommo — номер задачи поэтому не закрепляется."""
    if not moves.handed_over(notice.reason):
        return
    extra = {"reply": notice.reply_id, "draft": notice.draft_id}
    try:
        sales_queue().enqueue(HANDOVER_JOB, notice.reply_id, **with_retries())
    except RedisError as exc:
        logger.warning(
            "продажи: агент понял: хочет говорить, а передача не поставлена — очередь "
            "недоступна; ответ ждёт человека",
            extra={**extra, "error": str(exc)},
        )
    else:
        logger.info("продажи: агент понял: хочет говорить — передача лида в очереди", extra=extra)


async def hand_over(
    session: AsyncSession, reply_id: int, *, start: HandOver = handoff.start
) -> dict[str, object]:
    """Передать лида по ответу и записать итог в снимок ответа. Итог — словами."""
    reply = await session.get(ReplyModel, reply_id)
    if reply is None or reply.thread_id is None:
        return {"reply": reply_id, "skipped": "ответа нет: удалён до передачи"}
    ask = Ask(reply_id=reply.id, thread_id=reply.thread_id, words=UNDERSTOOD, joint=" — ")
    said = await reply_handoff.pass_on(session, ask, start)
    if said is None:
        return {"reply": reply_id, "error": "итог передачи в снимок ответа не записан — в журнале"}
    extra = {"reply": reply_id, "thread_id": reply.thread_id, "reason": said.reason}
    if said.waits:
        logger.warning("продажи: агент понял: хочет говорить — лида передать не вышло", extra=extra)
    else:
        logger.info(
            "продажи: агент понял: хочет говорить — лид передан телемаркетологу", extra=extra
        )
    return {"reply": reply_id, "waits": said.waits, "reason": said.reason}


async def _run(reply_id: int) -> dict[str, object]:
    """Своя сессия на задачу: передача коммитит её сама."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await hand_over(session, reply_id)
    finally:
        await engine.dispose()


def hand_over_understood(reply_id: int) -> dict[str, Any]:
    """Задача очереди продаж: агент понял «хочет говорить» — передать лида. Сбой базы —
    исключение, очередь повторит; отказ передачи — итог словами в снимке ответа."""
    setup_logging()
    check_storage()
    return asyncio.run(_run(reply_id))
