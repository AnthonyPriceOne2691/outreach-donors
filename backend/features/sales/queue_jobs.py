"""Задача сборки очереди продаж (`build_sales_queue`): экран ставит, воркер собирает.

Сборка зовёт модель на каждое письмо — минуты, а не запрос: экран ставит задачу в общую
очередь путём строкой (`QUEUE_JOB`), как передача лида (`handoff_jobs.py`), и общий код
очереди о продажах не знает. Тонкая обёртка над `queue.build`: сессия, клиент модели,
итог словами.

**Отказы.** Постоянный (`permanent`: продажи не подключены) — итог задачи,
а не падение: повтор очереди не поможет. Остальное (база, модель) — исключение, и очередь
повторит задачу; повтор не задваивает (ключ письма с контактом, один диалог на лида).

**Письмо одному лиду — та же задача** (`build_for`): лид из «пишите другому» прошёл очистку —
его первое письмо собирается сразу, только ему (`queue.build(only=…)`), под тем же потолком модели
и с тем же итогом. Свой номер задачи — от лида: сборке гипотезы с кнопки она не мешает, а
столкнись они на этом лиде — второе письмо не даст один диалог на лида.
"""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from typing import Any

from rq import get_current_job
from rq.exceptions import DuplicateJobError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.letters.rewrite import RewriteClient
from backend.features.runs.failures import described, is_permanent
from backend.features.sales import queue
from backend.shared.logs import setup_logging
from backend.shared.queue import remember_job_error, sales_queue, with_retries

logger = logging.getLogger(__name__)

#: Путь задачи строкой: очередь импортирует её в воркере.
QUEUE_JOB = "backend.features.sales.queue_jobs.build_sales_queue"


def lead_job_id(lead_id: int) -> str:
    """Номер задачи письма одному лиду — от лида: одна такая задача на лида."""
    return f"sales-queue-lead-{lead_id}"


def build_for(hypothesis_id: int, lead_id: int) -> None:
    """Поставить сборку письма одному лиду в очередь продаж. Уже стоит — второй не ставим.
    Очередь не ответила — исключение вызывающему: лид `ready`, письмо соберёт кнопка."""
    try:
        sales_queue().enqueue(
            QUEUE_JOB,
            hypothesis_id,
            1,
            [lead_id],
            job_id=lead_job_id(lead_id),
            unique=True,
            **with_retries(),
        )
    except DuplicateJobError:
        logger.info("продажи: письмо лиду уже собирается", extra={"lead_id": lead_id})


async def run_build(
    hypothesis_id: int, limit: int, leads: list[int] | None = None
) -> dict[str, Any]:
    """Одна сборка: своя сессия и свой клиент модели на задачу. `leads` — только этим лидам."""
    engine = create_async_engine(storage.DSN)
    rewriter = RewriteClient()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            build = partial(
                queue.build, session, rewriter, hypothesis_id=hypothesis_id, limit=limit
            )
            report = await (build(only=tuple(leads)) if leads else build())
            return report.as_dict()
    finally:
        await rewriter.aclose()
        await engine.dispose()


def build_sales_queue(
    hypothesis_id: int, limit: int, leads: list[int] | None = None
) -> dict[str, Any]:
    """Задача очереди: собрать очередь писем продаж гипотезы (`leads` — только этим лидам).
    Ничего не отправляет."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(run_build(hypothesis_id, limit, leads))
    except Exception as exc:
        if not is_permanent(exc):
            job = get_current_job()
            if job is not None:
                remember_job_error(job.id, described(exc))
            raise
        logger.warning(
            "продажи: очередь не собрана",
            extra={"hypothesis_id": hypothesis_id, "error": described(exc)},
        )
        return {"error": described(exc), "permanent": True}
