"""Задача очереди продаж: `sales_reply` — вид ответа лида и его путь.

Своя очередь (`sales`) и свой воркер (`worker-sales`,
`python -m backend.workers.main --queue sales`): в общей очереди ответ лида
ждал бы часовой прогон доноров. Задача — тонкая обёртка, как в
`workers/jobs.py`: что делать с ответом, решает `features/sales/replies.py`.

**Модель не ответила — повтор, а не вердикт.** Записка об отказе ложится
к ответу (ответ ждёт человека с причиной), а временный отказ поднимается
исключением: очередь повторит задачу (`queue.RETRY_INTERVALS`), и удачная
попытка запишет вид поверх записки. Постоянный отказ (ключ, права) —
итог с причиной: повтор его не исправит. На последней попытке записка
говорит «повторы кончились — разберите вручную»: обещанного повтора не будет.

**Передача лида — после коммита ответа.** «Хочет говорить» передаётся телемаркетологу
(`SalesReplies.pass_on` → `handoff.start`) уже после записи вида: передача коммитит
сама и ставит задачу Kommo и Telegram, а её отказ задачу ответа не роняет. Удачная
передача снимает с ответа ожидание человека (снимок: «передан телемаркетологу»).

**Черновик агента продаж — после пути.** Вопрос и интерес (путь «ответит агент»,
`replies.DRAFTED`) получают черновик задачей той же очереди (`sales`): в общей он ждал
бы часовой прогон доноров. Ставит его шов (`agent_jobs.after_parse`) после записи вида
и передачи лида: положен ли черновик — тумблер `SALES_AGENT_ENABLED` и настройки агента
продаж, — а сбой постановки задачу ответа не роняет: черновик попросят кнопкой.

**Потолок расхода на модель — «не сегодня».** Задача ставит себя на начало
следующих суток UTC, как разбор цены (`jobs._parse_or_postpone`).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from rq import get_current_job
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.usage import LlmCapExceededError
from backend.features.sales.replies import SalesReplies
from backend.features.sales.reply_kind import KindClient
from backend.features.sales.verifier import build_verifier
from backend.shared.logs import setup_logging
from backend.shared.queue import (
    SALES_REPLY_JOB,
    remember_job_error,
    sales_job_id,
    sales_queue,
    with_retries,
)
from backend.workers import agent_jobs
from backend.workers.jobs import next_utc_day

logger = logging.getLogger(__name__)


class ModelUnavailableError(RuntimeError):
    """Модель не ответила, и повтор может помочь: очередь повторит задачу."""


def sales_reply(reply_id: int) -> dict[str, Any]:
    """Разобрать вид ответа лида продаж. Итог — словами: что сделано или почему нет."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(handle(reply_id))
    except LlmCapExceededError as exc:
        logger.warning("продажи: потолок модели — вид ответа завтра", extra={"reply": reply_id})
        return postpone(reply_id, exc, now=datetime.now(UTC))


async def handle(reply_id: int) -> dict[str, Any]:
    """Тело задачи: свой движок базы на свой цикл событий (как у `jobs.py`).

    Последняя ли попытка — до разбора: записка об отказе модели не обещает повтор, которого
    не будет. Повторов не осталось (rq: `retries_left` ноль или пусто) или задача запущена
    не из очереди (консоль, тест) — повторять некому, это последняя попытка."""
    job = get_current_job()
    last_try = job is None or not job.retries_left
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    classifier = KindClient()
    # Проверка адреса лида из «пишите другому» — по настройке (fixture или
    # живой сервис); строится только тогда, когда такой лид появился.
    http = httpx.AsyncClient()
    try:
        async with factory() as session:
            sales = SalesReplies(
                session, classifier, verifier=lambda: build_verifier(http), last_try=last_try
            )
            handled = await sales.handle(reply_id)
            await session.commit()
            handled = await sales.pass_on(handled)
            if handled.drafted:  # вопрос, интерес — черновик агента своей очередью
                await agent_jobs.after_parse(session, reply_id, queue=sales_queue)
    finally:
        await classifier.aclose()
        await http.aclose()
        await engine.dispose()
    logger.info("продажи: задача ответа кончилась", extra=handled.as_report)
    missing = handled.unanswered
    if missing is None:
        return handled.as_report
    if missing.permanent:
        return {**handled.as_report, "error": missing.reason, "permanent": True}
    if job is not None:
        remember_job_error(job.id, missing.reason)
    raise ModelUnavailableError(f"ответ №{reply_id}: {missing.reason}")


def postpone(reply_id: int, exc: LlmCapExceededError, *, now: datetime) -> dict[str, Any]:
    """Поставить задачу на начало следующих суток UTC — когда потолок обнулится."""
    when = next_utc_day(now)
    job_id = f"{sales_job_id(reply_id)}-after-cap-{when:%Y%m%d}"
    sales_queue().enqueue_at(when, SALES_REPLY_JOB, reply_id, job_id=job_id, **with_retries())
    logger.warning(
        "продажи: вид ответа отложен — потолок модели",
        extra={"reply": reply_id, "until": when.isoformat(), "why": str(exc)},
    )
    return {"reply": reply_id, "postponed_until": when.isoformat(), "reason": str(exc)}
