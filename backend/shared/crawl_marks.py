"""Отметки обходчиков в Redis для сторожа нагрузки (`workers/load_gate.py`) и экрана.

Две отметки: «ждёт» — копия, которую сторож держит без работы, с причиной (её читает
панель «Обход доноров»), и «только что начала» — копия взяла обход, и остальные до конца
отметки новых не берут. Отдельно от `shared/queue.py`: это не очередь задач, а знание
копий друг о друге, и Redis здесь — общая доска, а не брокер.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from redis import Redis
from redis.exceptions import RedisError

from backend.shared.queue import connection

logger = logging.getLogger(__name__)


#: Обходчики, которых держит сторож нагрузки (`workers/load_gate.py`): имя копии → когда
#: отметилась и почему ждёт. Один хэш, а не ключ на копию: экрану — одно чтение. Отметку
#: обновляет каждый опрос сторожа и снимает копия, дождавшаяся места; отметка копии, умершей
#: с ней, старше `HELD_FRESH_SEC` и не считается — её убирает следующая отметка любой копии.
CRAWL_HELD_KEY = "outreach:crawl:held"
HELD_FRESH_SEC = 120.0


@dataclass(frozen=True, slots=True)
class Held:
    """Сколько обходчиков ждёт места на машине и почему — словами самой свежей отметки."""

    count: int
    words: str | None = None


def _held_marks(redis: Redis) -> list[tuple[str, float, str]]:
    """Отметки «ждёт»: (копия, когда, почему). Битая запись — не отметка."""
    marks = []
    for raw_name, raw in redis.hgetall(CRAWL_HELD_KEY).items():
        name = raw_name.decode() if isinstance(raw_name, bytes) else str(raw_name)
        try:
            mark = json.loads(raw)
            marks.append((name, float(mark["at"]), str(mark["words"])))
        except (ValueError, TypeError, KeyError):
            logger.debug("сторож нагрузки: битая отметка копии %s — пропускаю", name)
    return marks


def mark_held(
    name: str, words: str, *, redis: Redis | None = None, now: float | None = None
) -> None:
    """Отметить копию ждущей и убрать отметки, которые никто не обновляет. Redis не ответил —
    строка журнала, и только: отметка — для экрана, ждать сторож будет и без неё."""
    moment = time.time() if now is None else now
    client = redis or connection()
    try:
        stale = [mark for mark, at, _ in _held_marks(client) if moment - at > HELD_FRESH_SEC]
        client.hset(CRAWL_HELD_KEY, name, json.dumps({"at": moment, "words": words}))
        if stale:
            client.hdel(CRAWL_HELD_KEY, *stale)
    except RedisError as exc:
        logger.warning("сторож нагрузки: отметку «ждёт» не записали — %s", exc)


def clear_held(name: str, *, redis: Redis | None = None) -> None:
    """Снять отметку копии: место нашлось или копия выходит."""
    try:
        (redis or connection()).hdel(CRAWL_HELD_KEY, name)
    except RedisError as exc:
        logger.warning("сторож нагрузки: отметку «ждёт» не сняли — %s", exc)


def crawlers_held(redis: Redis | None = None, *, now: float | None = None) -> Held | None:
    """Сколько обходчиков сейчас держит сторож нагрузки; `None` — не спросили."""
    moment = time.time() if now is None else now
    try:
        fresh = [m for m in _held_marks(redis or connection()) if moment - m[1] <= HELD_FRESH_SEC]
    except RedisError as exc:
        logger.warning("очередь: не удалось спросить, кого держит сторож нагрузки — %s", exc)
        return None
    if not fresh:
        return Held(0)
    return Held(len(fresh), max(fresh, key=lambda mark: mark[1])[2])


#: Копия только что взяла обход (`workers/load_gate.took_work`): пока ключ живёт, остальные
#: новых не берут — нагрузка начатого обхода должна успеть попасть в их замер машины.
CRAWL_STARTED_KEY = "outreach:crawl:started"


def mark_started(seconds: float, *, redis: Redis | None = None) -> None:
    """Отметка «только что начала» на `seconds`. Redis не ответил — строка журнала: без
    отметки старты не разнесены, но обходы идут."""
    try:
        (redis or connection()).set(CRAWL_STARTED_KEY, "1", px=max(int(seconds * 1000), 1))
    except RedisError as exc:
        logger.warning("сторож нагрузки: отметку «только что начала» не записали — %s", exc)


def started_recently(*, redis: Redis | None = None) -> bool:
    """Начала ли какая-то копия обход только что. Redis не ответил — «нет»: держать копию
    из-за молчания Redis значило бы остановить обходы целиком."""
    try:
        return bool((redis or connection()).exists(CRAWL_STARTED_KEY))
    except RedisError as exc:
        logger.warning("сторож нагрузки: отметку «только что начала» не прочитали — %s", exc)
        return False
