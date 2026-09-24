"""Здоровье фоновых процессов для healthcheck докера.

    python -m backend.workers.health beats    # циклы: разбор прогонов, добивки
    python -m backend.workers.health worker   # воркер очереди

**Зачем, если докер и так перезапускает упавший процесс.** Упавший — да.
Зависший — нет: процесс жив, контейнер «Up», а добивки не уходят, и узнать
об этом можно только по отсутствию того, чего никто не ждёт. Проверка
превращает такую тишину в `unhealthy` в `docker compose ps`.

⚠ Докер вне swarm сам НЕ перезапускает `unhealthy`: проверка показывает,
а не лечит. Убивать себя по зависанию процесс тоже не должен — прерванная
посреди отправки добивка на повторе ушла бы донору второй раз.

Вывод — одна строка: её показывает `docker inspect`, и при отказе она
называет, что именно не так.
"""

from __future__ import annotations

import json
import logging
import socket
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue, Worker
from rq.defaults import DEFAULT_WORKER_TTL

from backend.shared.queue import QUEUE_NAME, connection
from backend.workers.ticker import BEATS_DIR

logger = logging.getLogger(__name__)

#: Столько проходов подряд с ошибкой — цикл крутится, но работу не делает.
FAILURES_UNHEALTHY = 3

#: Отметка воркера старше этого — он завис. rq обновляет её не реже чем
#: раз в `ttl - 15` с в простое и каждые 30 с под задачей; ключ воркера
#: живёт `ttl + 60`. Замерено на rq 2.12.
WORKER_STALE_SEC = DEFAULT_WORKER_TTL + 60


def beat_problems(directory: Path = BEATS_DIR, *, now: float | None = None) -> list[str]:
    """Что не так с циклами процесса. Пусто — здоров."""
    moment = time.time() if now is None else now
    files = sorted(directory.glob("*.json")) if directory.is_dir() else []
    if not files:
        return ["ни одной отметки о жизни — цикл не начался или пишет не туда"]
    problems = []
    for path in files:
        name = path.stem
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            age, allowed, failures = moment - data["at"], data["allowed"], data["failures"]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning("здоровье: отметку %s не прочитать — %r", path, exc)
            problems.append(f"{name}: отметку не прочитать ({exc})")
            continue
        if age > allowed:
            problems.append(
                f"{name}: последний круг {age:.0f} с назад при допуске {allowed:.0f} — завис"
            )
        elif failures >= FAILURES_UNHEALTHY:
            problems.append(f"{name}: {failures} прохода подряд с ошибкой — причина в журнале")
    return problems


def worker_problems(
    redis: Redis, queue_name: str, *, hostname: str | None = None, now: datetime | None = None
) -> list[str]:
    """Что не так с воркером ЭТОГО контейнера: он опознаётся по имени хоста."""
    host = hostname or socket.gethostname()
    moment = now or datetime.now(UTC)
    try:
        mine = [
            w for w in Worker.all(queue=Queue(queue_name, connection=redis)) if w.hostname == host
        ]
    except RedisError as exc:
        logger.warning("здоровье: очередь не ответила — %r", exc)
        return [f"очередь не отвечает: {exc}"]
    if not mine:
        return ["воркер этого контейнера не отмечен в очереди — не запущен или потерял Redis"]
    beats = [w.last_heartbeat for w in mine if w.last_heartbeat is not None]
    if not beats:
        return ["воркер отмечен, но ни разу не подал признак жизни"]
    age = (moment - max(beats)).total_seconds()
    if age > WORKER_STALE_SEC:
        return [f"последний признак жизни {age:.0f} с назад при допуске {WORKER_STALE_SEC} — завис"]
    return []


def main(argv: list[str]) -> int:
    kind = argv[0] if argv else ""
    if kind == "beats":
        problems = beat_problems()
    elif kind == "worker":
        problems = worker_problems(connection(), QUEUE_NAME)
    else:
        print("Что проверять: beats (циклы) или worker (воркер очереди)")
        return 2
    print("; ".join(problems) if problems else "здоров")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
