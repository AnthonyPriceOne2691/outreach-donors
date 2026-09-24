"""Вечный цикл фонового прохода — один на всех, кто ходит по расписанию.

Проходов в сервисе два: разбор мёртвых прогонов и рассылка добивок.
Устройство у них одинаковое, и одинаковым оно должно остаться: сбой
одного прохода не роняет процесс. Фоновая работа полезна именно тогда,
когда что-то уже сломалось, и умереть вместе с поломкой — худшее, что
она может сделать.
"""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
from collections.abc import Awaitable, Callable
from pathlib import Path

logger = logging.getLogger(__name__)

#: Один проход. Возвращать ничего не обязан: что случилось, он пишет
#: в лог сам — там это ищут.
Pass = Callable[[], Awaitable[None]]

#: Где циклы оставляют отметки о жизни. Читает их `workers/health.py` —
#: healthcheck докера в том же контейнере.
BEATS_DIR = Path(tempfile.gettempdir()) / "outreach-beats"

#: Сколько проход может идти сверх своего интервала, прежде чем считаться
#: зависшим. Щедро: добивки ждут платформу, сторож — провайдеров, и ложная
#: тревога хуже опоздавшей на несколько минут.
PASS_BUDGET_SEC = 600.0


def beat(name: str, *, allowed: float, failures: int) -> None:
    """Отметка «цикл жив». Сама отметка не должна ронять цикл: не записалась
    — healthcheck увидит протухшую отметку, и это честный сигнал."""
    try:
        BEATS_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"at": time.time(), "allowed": allowed, "failures": failures}
        (BEATS_DIR / f"{name.replace('/', '-')}.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
    except OSError as exc:
        logger.warning("%s: отметку о жизни не записать (%s) — healthcheck её не увидит", name, exc)


async def every(interval: float, work: Pass, *, name: str) -> None:
    """Звать `work` раз в `interval` секунд, пока процесс жив.

    До и после каждого прохода — отметка о жизни с числом неудач подряд.
    Так видно главное, чего не видит докер: процесс жив, а цикл встал на
    зависшем ожидании, — и то, что процесс крутится, но работу не делает.
    """
    logger.info("%s: проход раз в %.0f с", name, interval)
    allowed = interval + PASS_BUDGET_SEC
    failures = 0
    while True:
        beat(name, allowed=allowed, failures=failures)
        try:
            await work()
            failures = 0
        except Exception:
            failures += 1
            logger.exception("%s: проход не удался, повтор через %.0f с", name, interval)
        beat(name, allowed=allowed, failures=failures)
        await asyncio.sleep(interval)
