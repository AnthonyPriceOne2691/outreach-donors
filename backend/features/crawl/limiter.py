"""Ограничитель на домен: одна операция на хост и пауза между ними.

Без него обход ведёт себя как небольшая атака: два десятка корутин
уходят на один сайт одновременно, сайт отвечает 429 и закрывается —
и дальше мы честно платим прокси за доступ, который сами же и потеряли.

**Пауза меряется от конца предыдущей операции, а не от её начала.**
Разница видна там, где она дороже всего: сайт отвечает восемь секунд,
пауза секунда. От начала — следующий запрос уходит сразу, потому что
секунда «уже прошла» внутри ответа, и медленному сайту достаётся вдвое
больший темп. От конца — пауза всегда настоящая.

**Ограничитель общий на все операции с хостом**, а не на обход:
robots.txt, sitemap и страницы — это запросы к одной машине, и считать
их по разным счётчикам значит утроить темп в тот момент, когда обход
только начинается.

**Живёт в памяти процесса.** При нескольких воркерах предел умножается
на их число — тот же признанный долг, что у ограничителя внешних
сервисов и у счёта попыток входа. Здесь он мягче: воркер берёт донора
целиком, и два воркера на одном хосте означают двух доноров на одном
хосте, что само по себе редкость.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field

from backend.config import crawl as cfg

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _HostState:
    """Замок хоста и время, когда с ним закончили в прошлый раз."""

    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    free_at: float = 0.0
    delay: float = 0.0


class DomainLimiter:
    """Не больше одной операции на хост одновременно плюс пауза.

    Пауза по умолчанию берётся из настроек; `Crawl-delay` сайта сильнее,
    если он больше, и обрезается потолком: значения в минуты встречаются,
    и слепо им подчиняться значит потратить бюджет времени на один сайт.
    """

    def __init__(self, *, delay_sec: float | None = None, max_delay_sec: float | None = None):
        self._default = delay_sec if delay_sec is not None else cfg.DELAY_SEC
        self._max = max_delay_sec if max_delay_sec is not None else cfg.MAX_DELAY_SEC
        self._hosts: defaultdict[str, _HostState] = defaultdict(_HostState)
        self.waited_sec = 0.0  # сколько всего прождали: попадает в отчёт

    def set_delay(self, host: str, requested: float | None) -> float:
        """Запомнить паузу для хоста. Возвращает ту, что применится.

        Меньше нашей не ставим: `Crawl-delay: 0` — это разрешение
        не ждать, а не требование не ждать, и терять вежливость
        по просьбе сайта незачем.
        """
        state = self._hosts[host]
        delay = max(self._default, requested or 0.0)
        if delay > self._max:
            logger.info(
                "обход: %s просит паузу %.1f с — берём потолок %.1f с", host, delay, self._max
            )
            delay = self._max
        state.delay = delay
        return delay

    def slow_down(self, host: str) -> float:
        """Вдвое медленнее — ответ на долю отказов выше порога.

        Порог и смысл — в `docs/CRAWL.md`; здесь только исполнение,
        чтобы решение «замедлиться» и умение замедлиться не разъехались.
        """
        state = self._hosts[host]
        state.delay = min(self._max, max(state.delay, self._default) * 2)
        return state.delay

    def delay_for(self, host: str) -> float:
        state = self._hosts[host]
        return state.delay or self._default

    def slot(self, host: str) -> _Slot:
        """Занять хост: `async with limiter.slot(host): ...`."""
        return _Slot(self, host)

    async def _acquire(self, host: str) -> None:
        state = self._hosts[host]
        await state.lock.acquire()
        left = state.free_at - time.monotonic()
        if left > 0:
            self.waited_sec += left
            await asyncio.sleep(left)

    def _release(self, host: str) -> None:
        state = self._hosts[host]
        state.free_at = time.monotonic() + (state.delay or self._default)
        state.lock.release()


class _Slot:
    """Занятый хост. Отдельный объект, а не `asynccontextmanager`:
    так замок освобождается и при отмене задачи, и при исключении."""

    __slots__ = ("_host", "_limiter")

    def __init__(self, limiter: DomainLimiter, host: str) -> None:
        self._limiter = limiter
        self._host = host

    async def __aenter__(self) -> None:
        await self._limiter._acquire(self._host)

    async def __aexit__(self, *_exc: object) -> None:
        self._limiter._release(self._host)
