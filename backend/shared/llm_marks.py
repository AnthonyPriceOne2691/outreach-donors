"""След вызовов модели в Redis — для сторожа модели (`features/ops/model_watch.py`).

23.11.2026 в 07:03 МСК на счёте OpenAI кончились деньги, и сервис никому не сказал: отказ «нет
кредитов» видел только процесс, где он случился, — строкой журнала и словами своего экрана. Модель
зовут многие процессы — воркеры, обходчик (судья), разбор (ночной судья), сервер, — а сторож живёт
в одном, в процессе разбора. Поэтому след общий и лежит в Redis, как отметки обходчиков
(`crawl_marks.py`): Redis здесь — общая доска, а не очередь. Пишет каждый вызов `llm.post_chat`,
читает сторож.

- **Удача** — хэш «модель → когда она последний раз ответила» (`ANSWERED_KEY`).
- **Пустой счёт** — когда был последний отказ «нет кредитов» и что сказал провайдер (`BROKE_KEY`).
- **Отказ** прочих видов — список последних: когда, какая модель и почему (`REFUSED_KEY`, не
  длиннее `KEEP`, живёт сутки с последнего). Частоту (429 без «нет кредитов») вызывающий не пишет:
  она проходит сама.

Время — секунды эпохи (`clock`): след делят процессы, и монотонные часы одного для него не годятся.

**Redis не ответил — вызов модели идёт своим чередом.** След нужен сторожу, и его молчание не
роняет ни судью, ни письмо: строка журнала, и только — не чаще раза в минуту на процесс
(`COMPLAINT_EVERY_SEC`), иначе её писал бы каждый вызов. Redis ждём секунду, а не пять, как
очередь: его ждёт каждый вызов модели. Клиент синхронный, поэтому запись и чтение — в потоке
(`asyncio.to_thread`): зависший Redis не держит цикл событий сервера и судьи.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import cache

from redis import Redis
from redis.exceptions import RedisError

from backend.config import storage

logger = logging.getLogger(__name__)

#: Когда каждая модель ответила последний раз: хэш «модель → секунды эпохи».
ANSWERED_KEY = "outreach:model:answered"
#: Последний отказ «нет кредитов»: хэш `at`, `model`, `why`.
BROKE_KEY = "outreach:model:broke"
#: Последние отказы прочих видов, новые первыми: список JSON `{"at", "model", "why"}`.
REFUSED_KEY = "outreach:model:refused"

#: Сколько последних отказов помнить: сторожу нужно десять подряд у одной модели, а моделей у
#: сервиса несколько, и поток отказов одной не должен сразу вытеснить остальные.
KEEP = 200
#: Сколько живёт список отказов после последнего: дольше сторож отказы и не судит
#: (`model_watch.HOLD_SEC` — час).
REFUSED_TTL_SEC = 86_400
#: Сколько знаков ответа провайдера хранить: столько же отказ несёт в журнал (`llm._message_of`).
WHY_CHARS = 320
#: Сколько ждать Redis — подключения и ответа.
REDIS_TIMEOUT_SEC = 1.0
#: Строка журнала о молчании Redis — не чаще раза в столько секунд на процесс.
COMPLAINT_EVERY_SEC = 60.0


@cache
def connection() -> Redis:
    """Клиент Redis следа — один на процесс: пишет каждый вызов модели, и новое соединение на вызов
    стоило бы лишнего круга. Отдельной функцией — тесты подменяют её хранилищем в памяти
    (`tests/conftest.py`)."""
    return Redis.from_url(
        storage.REDIS_URL,
        socket_connect_timeout=REDIS_TIMEOUT_SEC,
        socket_timeout=REDIS_TIMEOUT_SEC,
    )


def clock() -> float:
    """Нынешнее время, секунды эпохи. Отдельной функцией — тесты ставят свои часы."""
    return time.time()


@dataclass
class Complaints:
    """Когда последний раз писали о молчании Redis: строка — не чаще `COMPLAINT_EVERY_SEC`."""

    last: float = -math.inf

    def warning(self, what: str, exc: Exception) -> None:
        """Строка журнала о молчании Redis — не чаще раза в минуту. Методом с именем уровня: гейт
        глухих обработчиков (`scripts/lint/ast_rules.py`) видит в нём запись в журнал."""
        moment = time.monotonic()
        if moment - self.last >= COMPLAINT_EVERY_SEC:
            self.last = moment
            logger.warning(
                "след модели: Redis не ответил — %s, вызовы модели идут своим чередом",
                what,
                extra={"error": str(exc)},
            )


#: Строки о молчании Redis этого процесса; тесты начинают с пустых (`tests/conftest.py`).
complaints = Complaints()


async def answered(model: str) -> None:
    """Модель ответила."""
    at = clock()
    await asyncio.to_thread(
        _put, "удача модели не записана", lambda redis: redis.hset(ANSWERED_KEY, model, repr(at))
    )


async def broke(model: str, why: str) -> None:
    """Отказ «нет кредитов»; `why` — что сказал провайдер."""
    at, said = repr(clock()), why[:WHY_CHARS]
    await asyncio.to_thread(
        _put,
        "отказ «нет кредитов» не записан",
        lambda redis: redis.hset(BROKE_KEY, mapping={"at": at, "model": model, "why": said}),
    )


async def refused(model: str, why: str) -> None:
    """Отказ прочего вида — не частота и не пустой счёт; `why` — почему."""
    entry = json.dumps({"at": clock(), "model": model, "why": why[:WHY_CHARS]}, ensure_ascii=False)
    await asyncio.to_thread(_put, "отказ модели не записан", lambda redis: _pushed(redis, entry))


def _put(what: str, write: Callable[[Redis], object]) -> None:
    """Записать след. Redis не ответил — строка журнала (`complaints`), а вызов модели идёт
    дальше."""
    try:
        write(connection())
    except (RedisError, OSError) as exc:
        complaints.warning(what, exc)


def _pushed(redis: Redis, entry: str) -> object:
    """Отказ — в начало списка; список — не длиннее `KEEP` и живёт сутки с последнего отказа."""
    with redis.pipeline() as pipe:
        pipe.lpush(REFUSED_KEY, entry)
        pipe.ltrim(REFUSED_KEY, 0, KEEP - 1)
        pipe.expire(REFUSED_KEY, REFUSED_TTL_SEC)
        return pipe.execute()


@dataclass(frozen=True, slots=True)
class Refused:
    """Отказ модели в следе: когда, какой модели и почему."""

    at: float
    model: str
    why: str


@dataclass(frozen=True, slots=True)
class Trace:
    """След на эту минуту: удачи по моделям, последний пустой счёт и последние отказы."""

    #: Когда каждая модель ответила последний раз.
    answered: dict[str, float]
    #: Когда был последний отказ «нет кредитов» (`None` — не было) и что сказал провайдер.
    broke_at: float | None
    broke_why: str
    #: Последние отказы прочих видов, новые первыми.
    refused: tuple[Refused, ...]

    @property
    def last_answer(self) -> float | None:
        """Когда ответила хоть какая-то модель: счёт один на все модели ключа."""
        return max(self.answered.values(), default=None)


async def read() -> Trace | None:
    """След из Redis; `None` — Redis не ответил (строка журнала)."""
    return await asyncio.to_thread(_read)


def _read() -> Trace | None:
    try:
        redis = connection()
        answers, broken = redis.hgetall(ANSWERED_KEY), redis.hgetall(BROKE_KEY)
        refusals = redis.lrange(REFUSED_KEY, 0, -1)
    except (RedisError, OSError) as exc:
        complaints.warning("след модели не прочитан", exc)
        return None
    times = {model: _seconds(at) for model, at in _strings(answers).items()}
    mark = _strings(broken)
    entries = [_entry(raw) for raw in (refusals if isinstance(refusals, list) else [])]
    return Trace(
        answered={model: at for model, at in times.items() if at is not None},
        broke_at=_seconds(mark.get("at")),
        broke_why=mark.get("why", ""),
        refused=tuple(one for one in entries if one is not None),
    )


def _strings(found: object) -> dict[str, str]:
    """Хэш из ответа `hgetall` строками; не словарь — пустой. Чужие байты — со знаками замены, а
    не падение прохода."""
    if not isinstance(found, dict):
        return {}
    return {_decoded(name): _decoded(value) for name, value in found.items()}


def _decoded(raw: object) -> str:
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


def _seconds(raw: str | None) -> float | None:
    """Время из следа; нет его — `None`, битое — `None` и строка журнала."""
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError:
        logger.warning("след модели: время не число (%s) — пропускаю", raw[:40])
        return None


def _entry(raw: object) -> Refused | None:
    """Отказ из списка; битая запись — `None` и строка журнала: сторож судит по остальным."""
    try:
        found = json.loads(_decoded(raw))
        return Refused(at=float(found["at"]), model=str(found["model"]), why=str(found["why"]))
    except (ValueError, TypeError, KeyError) as exc:
        logger.warning("след модели: отказ не разобран (%s) — пропускаю", exc)
        return None
