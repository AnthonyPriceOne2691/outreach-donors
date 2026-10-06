"""Задача «отправить очередь этапа» — пачкой (слово Anthony 06.10.2026).

Задача, а не запрос: сотня писем — минуты обмена с почтовой платформой,
и держать соединение всё это время значит потерять пачку, если человек
закрыл вкладку. Итог — в результате задачи: экран читает его строкой задачи.

Повторов нет: пачку ставит человек кнопкой, и упавшую он видит и ставит
снова — уже отправленные письма из очереди ушли, второй раз их не взять.
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import Stage
from backend.features.letters.batch import send_queue
from backend.features.letters.transport_factory import Transports, in_use
from backend.shared.logs import setup_logging


def send_letter_queue(stage: str, author_id: int | None = None) -> dict[str, Any]:
    """Отправить очередь этапа. Этап строкой: задача живёт дольше версии кода."""
    setup_logging()
    check_storage()
    return asyncio.run(_send(Stage(stage), author_id))


async def _send(stage: Stage, author_id: int | None) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, in_use(Transports()) as transports:
            report = await send_queue(session, transports, stage=stage, author_id=author_id)
            return report.as_dict()
    finally:
        await engine.dispose()
