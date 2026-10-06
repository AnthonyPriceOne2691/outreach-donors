"""Задача очереди продаж: `sales_reply` — ответ лида после приёма.

Своя очередь (`sales`) и свой воркер (`worker-sales`,
`python -m backend.workers.main --queue sales`): в общей очереди ответ лида
ждал бы часовой прогон доноров. Задача — тонкая обёртка, как в
`workers/jobs.py`: что делать с ответом, решает `features/sales/replies.py`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.sales.replies import SalesReplies
from backend.shared.logs import setup_logging

logger = logging.getLogger(__name__)


def sales_reply(reply_id: int) -> dict[str, Any]:
    """Взять ответ лида продаж. Итог — словами: что сделано или почему нет."""
    setup_logging()
    check_storage()
    return asyncio.run(handle(reply_id))


async def handle(reply_id: int) -> dict[str, Any]:
    """Тело задачи: свой движок базы на свой цикл событий (как у `jobs.py`)."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            handled = await SalesReplies(session).handle(reply_id)
            await session.commit()
    finally:
        await engine.dispose()
    logger.info("продажи: задача ответа кончилась", extra=handled.as_report)
    return handled.as_report
