"""Защита исходящих запросов: чужой адрес не должен увести нас внутрь сети.

Сервис ходит за страницами по адресам, которые пришли снаружи: из выдачи,
из ссылок на чужой странице, а с появлением интерфейса — из поля ввода
оператора. Без проверки это дыра: адрес вида `http://169.254.169.254/`
заставляет сервер сходить к метаданным облака и принести оттуда ключи
доступа. На ноутбуке такого адреса нет, на сервере он есть всегда.

**Редирект опаснее прямого адреса.** Проверить адрес на входе мало:
публичный сайт отвечает `302` на внутренний адрес, и клиент послушно идёт
туда — уже без всякой проверки. Поэтому проверка живёт в транспорте и
срабатывает на каждом шаге цепочки, а не один раз перед запросом.

**Размер ответа тоже ограничен.** Чужой сайт может отдавать гигабайты;
без потолка это память сервера, а не наша страница с контактами.

**Отказ распознавания имени считается безопасным.** Имя не разрешилось —
пропускаем: иначе временный сбой DNS остановил бы сбор по живым донорам.
Настоящую атаку это не пропускает: в ней имя обязано разрешиться
во внутренний адрес, иначе соединение не состоится.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

#: Сколько байт ответа читаем максимум. Страница контактов в этот размер
#: укладывается с большим запасом.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: Имена, за которыми стоят служебные адреса. Часть из них не разрешается
#: обычным способом, часть подменяется настройками машины — поэтому список
#: проверяется до распознавания имени.
BLOCKED_HOSTS = frozenset(
    {
        "localhost",
        "metadata",
        "metadata.google.internal",
        "metadata.goog",
        "169.254.169.254",  # метаданные в облаках трёх крупнейших провайдеров
        "100.100.100.200",
        "fd00:ec2::254",
    }
)


class UnsafeUrlError(ValueError):
    """Адрес не прошёл проверку: чужая схема или внутренний адрес."""


def _is_private(address: str) -> bool:
    """Внутренний ли это адрес. Не адрес вовсе — значит, это имя, и его
    проверит распознавание."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        logger.debug("url-guard: %r не похоже на адрес — проверяем как имя", address)
        return False
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolved_addresses(host: str) -> list[str]:
    """Все адреса имени. Пустой список — имя не разрешилось."""
    try:
        return [str(info[4][0]) for info in socket.getaddrinfo(host, None)]
    except (OSError, UnicodeError) as exc:
        logger.debug("url-guard: имя %s не разрешилось (%r) — считаем безопасным", host, exc)
        return []


def assert_safe_url(url: str | httpx.URL) -> None:
    """Проверить один адрес. Бросает `UnsafeUrlError`, если ходить туда нельзя."""
    parsed = urlparse(str(url))
    scheme = (parsed.scheme or "").lower()
    if scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"схема {scheme or 'без схемы'} не разрешена: {url}")

    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise UnsafeUrlError(f"в адресе нет хоста: {url}")
    if host in BLOCKED_HOSTS:
        raise UnsafeUrlError(f"служебное имя {host} запрещено")
    if _is_private(host):
        raise UnsafeUrlError(f"внутренний адрес {host} запрещён")

    for address in _resolved_addresses(host):
        if _is_private(address):
            raise UnsafeUrlError(f"имя {host} ведёт на внутренний адрес {address}")


class _CappedStream(httpx.AsyncByteStream):
    """Поток ответа с потолком: лишнее не читается, а не отбрасывается после."""

    def __init__(self, stream: httpx.AsyncByteStream, limit: int) -> None:
        self._stream = stream
        self._limit = limit

    async def __aiter__(self) -> AsyncIterator[bytes]:
        read = 0
        async for chunk in self._stream:
            read += len(chunk)
            if read > self._limit:
                logger.info("url-guard: ответ обрезан на %d байт", self._limit)
                yield chunk[: self._limit - read]
                return
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


class GuardedTransport(httpx.AsyncBaseTransport):
    """Транспорт, проверяющий КАЖДЫЙ запрос, включая шаги редиректа.

    Проверка на входе фетчера защищает только от прямого адреса; шаг
    редиректа идёт мимо неё, потому что адрес там подставляет чужой сервер.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport | None = None,
        *,
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._inner = inner or httpx.AsyncHTTPTransport(verify=False)
        self._limit = max_response_bytes

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        assert_safe_url(request.url)
        response = await self._inner.handle_async_request(request)
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            stream=_CappedStream(response.stream, self._limit),  # type: ignore[arg-type]
            extensions=response.extensions,
            request=request,
        )

    async def aclose(self) -> None:
        await self._inner.aclose()


def guarded_client(*, timeout: httpx.Timeout | float, **kwargs: object) -> httpx.AsyncClient:
    """Клиент для походов по чужим адресам.

    Проверка сертификата выключена намеренно: у доноров она протухает
    постоянно, а читаем мы публичные страницы и ничего им не передаём.
    Защита здесь от другого — от похода во внутреннюю сеть.
    """
    return httpx.AsyncClient(transport=GuardedTransport(), timeout=timeout, **kwargs)  # type: ignore[arg-type]
