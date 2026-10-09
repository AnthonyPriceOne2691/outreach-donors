"""Сеть клиентов Kommo — общее у прямого пути (`kommo_live.py`) и шлюза (`kommo_gateway.py`).

Темп запросов, разбор ответа и обрыв связи словами живут здесь одним местом, а не двумя
копиями: правка разбора или темпа, сделанная в одной копии, разошлась бы со второй молча.
Своё у каждого клиента — адрес, тело запроса, слова отказа по коду ответа и повторы:
прямой путь повторяет временное внутри запроса, шлюз — проходом передачи.

**Ключа нет ни в тексте ошибок, ни в журнале.** Текст ошибок httpx несёт адрес запроса,
а `LocalProtocolError` — значение заголовка целиком, то есть ключ; поэтому наружу идут
свои исключения с одним типом ошибки, а вызывающий поднимает их `from None` (урок вебхука
лидов, #160).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from backend.features.sales.kommo_types import (
    KommoError,
    KommoRefusedError,
    KommoUnavailableError,
    KommoUnconfirmedError,
)

logger = logging.getLogger(__name__)

#: Обрывы, при которых запрос точно не ушёл: соединения не было. Повтор такой записи
#: не заведёт в CRM вторую сделку; дошедшей — завёл бы.
NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


class Pace:
    """Не чаще одного запроса в 1/N секунды — значит, не больше N в любую секунду.

    Ровный шаг, а не окно: у передачи лида запросов два-три, пачка разом не
    нужна, а шаг держит частоту и на повторах. Счёт — в памяти клиента: Kommo
    считает по IP, но лидов единицы в день, и общий счётчик на процесс не
    окупил бы своей сложности.
    """

    def __init__(
        self,
        per_second: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._gap = 1.0 / per_second
        self._clock = clock
        self._sleep = sleep
        self._next = float("-inf")

    async def wait(self) -> None:
        now = self._clock()
        start = max(now, self._next)
        # Место занимается до сна: запрос, пришедший, пока предыдущий ждёт,
        # встаёт за ним, а не рядом.
        self._next = start + self._gap
        if start > now:
            await self._sleep(start - now)


@dataclass(frozen=True, slots=True)
class Peer:
    """С кем говорит клиент — для слов отказа: имя в трёх падежах и настройки, которые
    проверить, если запрос не собрался."""

    #: Кто не ответил: «Kommo», «шлюз Kommo».
    who: str
    #: Чей ответ потерян: «Kommo», «шлюза Kommo».
    whose: str
    #: К кому запрос: «Kommo», «шлюзу Kommo».
    to: str
    #: Что проверить, если запрос не собран, — имена настроек.
    settings: str


def unreached(exc: httpx.HTTPError, method: str, peer: Peer) -> KommoError:
    """Ответа нет. Наружу — только тип ошибки: в тексте httpx адрес запроса,
    а у `LocalProtocolError` — значение заголовка, то есть ключ."""
    kind = type(exc).__name__
    if isinstance(exc, httpx.LocalProtocolError):
        return KommoRefusedError(
            f"запрос к {peer.to} не собран ({kind}) — проверить {peer.settings}; повтор не поможет"
        )
    if method != "GET" and not isinstance(exc, NOT_SENT):
        return KommoUnconfirmedError(
            f"ответ {peer.whose} потерян после отправки ({kind}) — запись могла создаться; "
            "проверить в Kommo руками: повтор вслепую завёл бы вторую"
        )
    return KommoUnavailableError(
        f"{peer.who} не ответил: связь оборвалась ({kind}) — в CRM ничего не записано, "
        "повторим позже"
    )


def temporary(response: httpx.Response, peer: Peer) -> KommoUnavailableError | None:
    """429 и 5xx — временный отказ: повтор позже поможет, пауза — та, что назвал сервер.
    Другой код — `None`, его судит клиент своими словами."""
    code = response.status_code
    if code != httpx.codes.TOO_MANY_REQUESTS and not httpx.codes.is_server_error(code):
        return None
    asked = asked_wait(response)
    wait = f", просит подождать {asked:g} с" if asked is not None else ""
    return KommoUnavailableError(
        f"{peer.who} не принял запрос (HTTP {code}{wait}) — повторим позже", retry_after=asked
    )


def asked_wait(response: httpx.Response) -> float | None:
    """Сколько просит подождать сервер: заголовок `Retry-After` в секундах или поле
    `retry_after` тела — свою паузу на 429 Kommo кладёт в тело."""
    header = (response.headers.get("Retry-After") or "").strip()
    if header.isdecimal():
        return float(header)
    body = body_of(response)
    value = body.get("retry_after") if isinstance(body, dict) else None
    if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def body_of(response: httpx.Response) -> Any:
    """Тело как JSON; не JSON — `None`, судит вызывающий по коду и форме."""
    try:
        return response.json()
    except ValueError:
        # Не JSON — исход, а не потеря: вызывающий назовёт его словами.
        logger.debug("kommo: ответ не JSON", extra={"status": response.status_code})
        return None


def glimpse(text: str) -> str:
    """Начало ответа для слов отказа: формат поменялся — человеку видно, на что."""
    return repr(text[:120])


def number_of(value: object) -> int | None:
    """Номер сущности Kommo: целое больше нуля. `True` — тоже int, но не номер."""
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return None
