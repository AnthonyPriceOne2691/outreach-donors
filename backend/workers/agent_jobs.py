"""Задачи агента переписки: черновик ответа — следом за разбором ответа.

Черновик пишется задачей, а не в вебхуке приёма и не в разборе: вызов модели
идёт секундами, и разбор цены не должен ждать письма, которое, может быть,
никто не отправит. После разбора — потому что агенту нужна разобранная цена.

Имя задачи и её номер живут здесь, а не в `shared/queue.py`: задачам агента
свой модуль, и общий файл очереди не правится ради одной строки.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from redis.exceptions import RedisError
from rq.exceptions import DuplicateJobError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.agent import drafting
from backend.features.agent.writer import AgentWriter, DraftUnavailableError
from backend.features.core.usage import LlmCapExceededError
from backend.shared.logs import setup_logging
from backend.shared.queue import runs_queue, with_retries

logger = logging.getLogger(__name__)

#: Черновик агента переписки — путь задачи для очереди.
DRAFT_JOB = "backend.workers.agent_jobs.draft_answer"


def draft_job_id(reply_id: int) -> str:
    """Номер задачи черновика: один на ответ — повтор разбора второй не ставит."""
    return f"draft-reply-{reply_id}"


def queue_draft(reply_id: int) -> None:
    """Поставить черновик ответа — одну задачу на ответ.

    Не встала — не беда: черновик человек попросит кнопкой в переписке.
    Поэтому сбой очереди пишется в лог, а разбор, который её ставит, остаётся
    сделанным. Есть ли агенту что писать, решает сама задача
    (`drafting.draft_answer`): ставить её дёшево, а пропуск она называет.
    """
    try:
        runs_queue().enqueue(
            DRAFT_JOB,
            reply_id,
            job_id=draft_job_id(reply_id),
            unique=True,
            **with_retries(),
        )
    except DuplicateJobError:
        logger.info("черновик ответа №%s уже в очереди — второй не ставлю", reply_id)
    except RedisError as exc:
        logger.warning(
            "черновик ответа №%s не поставлен — очередь недоступна (%s); "
            "его можно попросить кнопкой в переписке",
            reply_id,
            exc,
        )


async def _draft_answer(reply_id: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    agent = AgentWriter()
    try:
        async with factory() as session:
            outcome = await drafting.draft_answer(session, agent, reply_id)
            await session.commit()
            await drafting.announce(outcome)
            return {
                "reply": reply_id,
                "draft": outcome.draft_id,
                "status": None if outcome.status is None else outcome.status.value,
                "skipped": outcome.skipped,
                "tokens": outcome.tokens,
            }
    finally:
        await agent.aclose()
        await engine.dispose()


def draft_answer(reply_id: int) -> dict[str, Any]:
    """Черновик ответа агентом переписки.

    Потолок расхода на модель и отказ модели «чинить» (ключ, права) — итог
    задачи с причиной, а не повтор: повтор через минуты не исправит ни того,
    ни другого, а черновик человек попросит кнопкой, когда причина уйдёт.
    Сеть и «можно повторить» — падение, его повторит очередь.
    """
    setup_logging()
    check_storage()
    try:
        return asyncio.run(_draft_answer(reply_id))
    except (DraftUnavailableError, LlmCapExceededError) as exc:
        if isinstance(exc, DraftUnavailableError) and not exc.permanent:
            raise
        logger.warning("черновик ответа №%s не написан: %s", reply_id, exc)
        return {"reply": reply_id, "error": str(exc), "permanent": True}
