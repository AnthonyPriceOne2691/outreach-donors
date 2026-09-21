"""Вечный цикл фонового прохода — один на всех, кто ходит по расписанию.

Проходов в сервисе два: разбор мёртвых прогонов и рассылка добивок.
Устройство у них одинаковое, и одинаковым оно должно остаться: сбой
одного прохода не роняет процесс. Фоновая работа полезна именно тогда,
когда что-то уже сломалось, и умереть вместе с поломкой — худшее, что
она может сделать.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

logger = logging.getLogger(__name__)

#: Один проход. Возвращать ничего не обязан: что случилось, он пишет
#: в лог сам — там это ищут.
Pass = Callable[[], Awaitable[None]]


async def every(interval: float, work: Pass, *, name: str) -> None:
    """Звать `work` раз в `interval` секунд, пока процесс жив."""
    logger.info("%s: проход раз в %.0f с", name, interval)
    while True:
        try:
            await work()
        except Exception:
            logger.exception("%s: проход не удался, повтор через %.0f с", name, interval)
        await asyncio.sleep(interval)
