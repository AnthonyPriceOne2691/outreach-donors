"""Задача «отправить очередь этапа» — пачкой (слово Anthony 06.10.2026).

Задача, а не запрос: сотня писем — минуты обмена с почтовой платформой,
и держать соединение всё это время значит потерять пачку, если человек
закрыл вкладку. Итог — в результате задачи: экран читает его строкой задачи.

Повторов нет: пачку ставит человек кнопкой, и упавшую он видит и ставит
снова — уже отправленные письма из очереди ушли, второй раз их не взять.

Очередь, которая отправляется сама (`letters/autosend.py`), — та же пачка: итог задачи кнопки
говорит, что остальное уйдёт само, а проход процесса разбора (`autosend_pass`) шлёт остаток
по письму с ящика. Здесь — только проводка прохода: база, транспорты, своя транзакция на запуск.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import outreach as outreach_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import QueueAutosendModel
from backend.features.crawl.niche import LINKS
from backend.features.letters import autosend
from backend.features.letters.batch import send_queue
from backend.features.letters.transport_factory import Transports, in_use
from backend.shared.logs import setup_logging

logger = logging.getLogger(__name__)


def send_letter_queue(
    stage: str, author_id: int | None = None, audience: str = LINKS
) -> dict[str, Any]:
    """Отправить очередь этапа и аудитории. Этап строкой: задача живёт дольше версии
    кода. Задача, поставленная до аудиторий, приходит без неё — и шлёт письма по ссылке."""
    setup_logging()
    check_storage()
    return asyncio.run(_send(Stage(stage), author_id, audience))


async def _send(stage: Stage, author_id: int | None, audience: str = LINKS) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session, in_use(Transports()) as transports:
            itself = await autosend.running(session, stage, audience) is not None
            report = await send_queue(
                session,
                transports,
                stage=stage,
                author_id=author_id,
                audience=audience,
                automatic=itself,
            )
            return report.as_dict()
    finally:
        await engine.dispose()


async def autosend_pass() -> None:
    """Проход очередей, которые отправляются сами: каждый идущий запуск — своим проходом и
    своей транзакцией, чтобы сбой одного не держал остальные. Рубильник выключен или запусков
    нет — ни транспортов, ни отправки."""
    if not outreach_cfg.AUTOSEND:
        return
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            ids = [mode.id for mode in await autosend.every_running(session)]
        if not ids:
            return
        async with in_use(Transports()) as transports:
            for mode_id in ids:
                async with factory() as session:
                    mode = await session.get(QueueAutosendModel, mode_id)
                    if mode is None or mode.stopped_at is not None:
                        continue
                    said = await autosend.run(session, transports, mode, now=datetime.now(UTC))
                    await session.commit()
                    logger.debug("письма: проход запуска №%s — %s", mode_id, said)
    finally:
        await engine.dispose()
