"""Задачи агента переписки: черновик ответа — следом за разбором ответа.

Черновик пишется задачей, а не в вебхуке приёма и не в разборе: вызов модели
идёт секундами, и разбор цены не должен ждать письма, которое, может быть,
никто не отправит. После разбора — потому что агенту нужна разобранная цена.

Имя задачи и её номер живут здесь, а не в `shared/queue.py`: задачам агента
свой модуль, и общий файл очереди не правится ради одной строки.

Очередь черновика — та, чей воркер разобрал ответ: разбор цены доноров и
рекламодателей ставит его в общую (`runs`), разбор ответа лида продаж — в свою
(`sales`, `worker-sales`, `workers/sales_jobs.py`).

**Модель черновик не написала, и повтора не будет** — ключ или права модели, потолок
расхода, временный отказ на последней попытке очереди: черновик человеку без текста
с причиной словами (`drafting.unwritten`), фиксация, затем крючок этапа
(`drafting.announce`). До 13.10.2026 такая задача кончалась итогом и строкой журнала:
черновика нет, группа продаж не узнаёт, а ответ ждёт агента, которого не будет.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from redis.exceptions import RedisError
from rq import Queue, get_current_job
from rq.exceptions import DuplicateJobError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.agent import autopilot, drafting
from backend.features.agent.autopilot import AutopilotOutcome
from backend.features.agent.drafting import DraftOutcome
from backend.features.agent.writer import AgentWriter, DraftUnavailableError
from backend.features.core.domain import DraftStatus
from backend.features.core.usage import LlmCapExceededError
from backend.features.letters.sending import Sending
from backend.features.letters.transport_factory import Transports, in_use
from backend.shared.logs import setup_logging
from backend.shared.queue import quick_queue, with_retries

logger = logging.getLogger(__name__)

#: Черновик агента переписки — путь задачи для очереди.
DRAFT_JOB = "backend.workers.agent_jobs.draft_answer"


def draft_job_id(reply_id: int) -> str:
    """Номер задачи черновика: один на ответ — повтор разбора второй не ставит."""
    return f"draft-reply-{reply_id}"


def queue_draft(reply_id: int, *, queue: Callable[[], Queue] | None = None) -> None:
    """Поставить черновик ответа — одну задачу на ответ — в очередь `queue`: по умолчанию
    общую (`runs`). Разбор ответа лида продаж ставит его в свою (`sales`): в общей черновик
    лиду ждал бы часовой прогон доноров, как ждал бы сам ответ.

    Не встала — не беда: черновик человек попросит кнопкой в переписке.
    Поэтому сбой очереди пишется в лог, а разбор, который её ставит, остаётся
    сделанным. Есть ли агенту что писать, решает сама задача
    (`drafting.draft_answer`): ставить её дёшево, а пропуск она называет.
    """
    put = quick_queue if queue is None else queue  # общая — по имени модуля на момент вызова
    try:
        put().enqueue(
            DRAFT_JOB,
            reply_id,
            job_id=draft_job_id(reply_id),
            unique=True,
            **with_retries(),
        )
    except DuplicateJobError:
        logger.info(
            "черновик ответа №%s уже в очереди — второй не ставлю",
            reply_id,
            extra={"reply": reply_id},
        )
    except RedisError as exc:
        logger.warning(
            "черновик ответа №%s не поставлен — очередь недоступна (%s); "
            "его можно попросить кнопкой в переписке",
            reply_id,
            exc,
            extra={"reply": reply_id, "why": str(exc)},
        )


async def after_parse(
    session: AsyncSession, reply_id: int, *, queue: Callable[[], Queue] | None = None
) -> None:
    """Черновик — задачей сразу после разбора ответа: агенту нужна разобранная цена.
    Где агент на этапе не пишет (`drafting.wants_draft`), очередь не трогается.
    `queue` — очередь задачи черновика (`queue_draft`): по умолчанию общая.

    Разбор к этому месту уже закоммичен: сбой постановки черновика — любой, и запрос
    `wants_draft` к базе тоже, — не роняет задачу разбора и не уводит её на повтор
    (на повторе итогом стало бы «уже разобран» вместо настоящего разбора). Черновик
    тогда не поставлен — его можно попросить кнопкой в переписке."""
    try:
        if await drafting.wants_draft(session, reply_id):
            queue_draft(reply_id, queue=queue)
    except Exception as exc:
        logger.warning(
            "ответ №%s разобран, а черновик не поставлен — его можно попросить кнопкой в переписке",
            reply_id,
            exc_info=True,
            extra={"reply": reply_id, "why": str(exc)},
        )


async def _draft_answer(reply_id: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    agent = AgentWriter()
    try:
        async with factory() as session:
            outcome = await _drafted(session, agent, reply_id)
            await session.commit()
            flown = await _autopilot(session, outcome)
            if flown.sent_message_id is None:
                await drafting.announce(_after(outcome, flown))
            return {
                "reply": reply_id,
                "draft": outcome.draft_id,
                "status": None if outcome.status is None else outcome.status.value,
                "skipped": outcome.skipped,
                "tokens": outcome.tokens,
                "autopilot_sent": flown.sent_message_id,
                "autopilot_held": flown.held,
            }
    finally:
        await agent.aclose()
        await engine.dispose()


async def _drafted(session: AsyncSession, agent: AgentWriter, reply_id: int) -> DraftOutcome:
    """Черновик ответа. Модель его не написала, и повтора не будет, — черновик человеку
    с причиной (`_unwritten`), а исключение — дальше: итог задачи — отказ модели."""
    try:
        return await drafting.draft_answer(session, agent, reply_id)
    except (DraftUnavailableError, LlmCapExceededError) as exc:
        if _retried(exc) and not _last_try():
            raise
        await _unwritten(session, reply_id, exc)
        raise


def _retried(exc: Exception) -> bool:
    """Повторяет ли такой отказ очередь: временный отказ модели — да; ключ, права
    и потолок — нет (`draft_answer`)."""
    return isinstance(exc, DraftUnavailableError) and not exc.permanent


def _last_try() -> bool:
    """Последняя ли попытка: повторов не осталось (rq: `retries_left` ноль или пусто) или
    задача запущена не из очереди (консоль, тест) — повторять некому."""
    job = get_current_job()
    return job is None or not job.retries_left


async def _unwritten(session: AsyncSession, reply_id: int, exc: Exception) -> None:
    """Черновик не написан, и повтора не будет — черновик человеку без текста с причиной,
    фиксация, затем крючок этапа (его сбой черновик не теряет — `drafting.announce`).
    Сбой записи — исключением, как сбой базы у задачи: очередь её повторит, а причина
    модели остаётся в цепочке исключения."""
    why = f"{exc} — повторы задачи кончились" if _retried(exc) else str(exc)
    outcome = await drafting.unwritten(session, reply_id, why)
    await session.commit()
    await drafting.announce(outcome)


async def _autopilot(session: AsyncSession, outcome: DraftOutcome) -> AutopilotOutcome:
    """Автопилот — только готовому черновику и только где он разрешён: иначе
    почту не трогаем вовсе."""
    notice = outcome.notice
    if notice is None or notice.status is not DraftStatus.DRAFTED:
        return AutopilotOutcome()
    if autopilot.refusal(notice.stage) is not None:
        return AutopilotOutcome()
    async with in_use(Transports()) as transports:
        return await autopilot.run(session, Sending(session, transports), notice.draft_id)


def _after(outcome: DraftOutcome, flown: AutopilotOutcome) -> DraftOutcome:
    """Что объявлять: черновик, который автопилот отдал человеку, — с его причиной."""
    if outcome.notice is None or flown.held is None:
        return outcome
    held = replace(outcome.notice, status=DraftStatus.ESCALATED, reason=flown.held)
    return replace(outcome, notice=held)


def draft_answer(reply_id: int) -> dict[str, Any]:
    """Черновик ответа агентом переписки.

    Потолок расхода на модель и отказ модели «чинить» (ключ, права) — итог
    задачи с причиной, а не повтор: повтор через минуты не исправит ни того,
    ни другого, а черновик человек попросит кнопкой, когда причина уйдёт.
    Сеть и «можно повторить» — падение, его повторит очередь. В обоих случаях,
    когда повтора не будет, ответ получает черновик человеку с причиной (`_unwritten`).
    """
    setup_logging()
    check_storage()
    try:
        return asyncio.run(_draft_answer(reply_id))
    except (DraftUnavailableError, LlmCapExceededError) as exc:
        if _retried(exc):
            raise
        logger.warning(
            "черновик ответа №%s не написан: %s",
            reply_id,
            exc,
            extra={"reply": reply_id, "why": str(exc)},
        )
        return {"reply": reply_id, "error": str(exc), "permanent": True}
