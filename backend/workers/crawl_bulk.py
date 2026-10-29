"""«Обойти всех по фильтру» фоном: обходы многих доноров ставятся задачей быстрой очереди.

Вкладка «Обход доноров» (29.10.2026, `crawl/donor_page.py`) ставит в обход всех под условиями —
это сотни и тысячи доноров. Постановка — та же, что у кнопки (`crawl/launch.launch`): строка
обхода и задача на донора, каждый своей транзакцией, чтобы отказ очереди на третьем не
откатывал первые два, и второй незаконченный обход донора не заводится. Тысяча таких — минута:
в запросе экрана это висело бы, поэтому маршрут отдаёт задачу сюда, а экран видит ход по
счётчику «в очереди».
"""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.crawl.launch import launch
from backend.shared.logs import setup_logging
from backend.shared.queue import crawl_job_id, enqueue_crawl

logger = logging.getLogger(__name__)


def launch_hosts(hosts: list[str], by: str | None) -> dict[str, int]:
    """Поставить обходы доноров `hosts` от имени `by`; итог — числами."""
    setup_logging()
    check_storage()
    return asyncio.run(_launch(hosts, by))


async def _launch(hosts: list[str], by: str | None) -> dict[str, int]:
    engine = create_async_engine(storage.DSN)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            done = await launch(session, hosts, by=by, job_id=crawl_job_id, enqueue=enqueue_crawl)
    finally:
        await engine.dispose()
    said = {"queued": len(done.queued), "busy": len(done.busy), "failed": len(done.failed)}
    logger.info("обход по фильтру: %s из %s", said, len(hosts))
    return said
