"""Повторы и ограничитель частоты — одни на все внешние сервисы.

Требование говорит «своя очередь на каждый внешний сервис с
ограничителем скорости и повторами с нарастающей паузой, `Retry-After`
соблюдается». До этого модуля так было устроено только у Ahrefs:
источник выдачи и модель ходили без единого повтора, и один таймаут
посреди прогона оставлял ключи без выдачи молча.

**Ограничитель тот же, что у Ahrefs**, вынесенный, а не переписанный:
две копии оконного счётчика разъезжаются на первой правке окна, и тише
всех расходится та, которую реже зовут.

**Повторяется не всё.** Отказ «неверный ключ» повтором не лечится,
и повторять его — значит втрое дольше идти к тому же ответу. Повторяем
то, что бывает временным: перегрузку, обрыв, частоту.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)

#: Пауза вынесена отдельным именем, чтобы тест гасил её здесь, а не у всего
#: процесса. `monkeypatch` по `retry.asyncio.sleep` правит сам модуль
#: `asyncio` — то есть молча ускоряет любой другой сон в проекте, включая
#: ограничитель обхода на домен и опрос отложенной выдачи. Поймано ровно
#: так: тест паузы обхода видел ноль секунд вместо пятидесяти миллисекунд.
_sleep = asyncio.sleep

#: Коды, которые проходят сами: перегрузка, обрыв у посредника, частота.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

#: Потолок ожидания между попытками. `Retry-After` бывает в минутах,
#: и слепо ему подчиняться значит уснуть на полчаса внутри прогона.
MAX_DELAY_SEC = 30.0


@dataclass(slots=True)
class RateLimiter:
    """Не больше N запросов в минуту.

    Скользящего окна достаточно: очередь у нас одна, и точность
    до десятых долей секунды не нужна. Важно лишь не влететь в 429.
    """

    per_minute: int
    _times: list[float] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._times = [t for t in self._times if now - t < 60.0]
                if len(self._times) < self.per_minute:
                    self._times.append(now)
                    return
                await _sleep(60.0 - (now - self._times[0]) + 0.01)


def delay_for(attempt: int, response: httpx.Response | None = None) -> float:
    """Сколько ждать перед повтором.

    `Retry-After` провайдера уважаем, но не безоговорочно: значение
    бывает в минутах, а прогон ждёт. Разброс в конце — чтобы два
    процесса, начавшие ждать одновременно, не пришли обратно
    в одну и ту же секунду.
    """
    base = min(MAX_DELAY_SEC, 2.0**attempt)
    if response is not None:
        raw = response.headers.get("Retry-After")
        if raw and raw.strip().isdigit():
            base = min(MAX_DELAY_SEC, float(raw.strip()))
    return base + random.uniform(0, 0.5)  # noqa: S311 — разброс, а не криптография


def _last_try(exc: httpx.HTTPError, attempt: int, attempts: int) -> bool:
    """Дальше не повторяем: попытки кончились — или запрос не собран у нас.
    До сети такой запрос не дошёл, повтор соберёт его так же, и ждать между
    попытками нечего (пустой ключ даёт заголовок `Bearer `)."""
    return attempt == attempts - 1 or isinstance(exc, httpx.LocalProtocolError)


async def with_retries(
    call: Callable[[], Awaitable[httpx.Response]],
    *,
    attempts: int,
    topic: str,
    limiter: RateLimiter | None = None,
) -> httpx.Response:
    """Позвать с повторами. Последний ответ отдаётся как есть.

    Разбирать ответ — дело вызывающего: здесь знают только про то,
    что бывает временным, и ничего про смысл ответа.
    """
    last: httpx.Response | None = None
    for attempt in range(attempts):
        if limiter is not None:
            await limiter.acquire()
        try:
            last = await call()
        except httpx.HTTPError as exc:
            if _last_try(exc, attempt, attempts):
                raise
            pause = delay_for(attempt)
            logger.warning(
                "%s: связь оборвалась (%r), повтор через %.1f с (%s из %s)",
                topic,
                exc,
                pause,
                attempt + 1,
                attempts,
            )
            await _sleep(pause)
            continue

        if last.status_code not in RETRY_STATUSES or attempt == attempts - 1:
            return last

        pause = delay_for(attempt, last)
        logger.warning(
            "%s: провайдер ответил %s, повтор через %.1f с (%s из %s)",
            topic,
            last.status_code,
            pause,
            attempt + 1,
            attempts,
        )
        await _sleep(pause)

    # Недостижимо: последняя попытка либо вернула ответ, либо подняла
    # исключение. Строка нужна типизатору и тому, кто правит цикл.
    raise RuntimeError(f"{topic}: повторы кончились без ответа")
