"""Прокси браузера: защита исходящих на уровне сети (ревью 20.10.2026).

Перехват маршрутов вкладки (`browser._guarded`) видит не всё. Мимо него идут запросы
SharedWorker и сервис-воркеров, WebRTC (UDP) и preconnect: ревьюер на настоящем Chromium
довёл XHR воркера до «внутреннего» адреса. Поэтому браузер ходит в сеть только через этот
прокси. Прокси работает в том же процессе, на петлевом адресе, и проверяет каждое соединение
так же, как обычный обход (`url_guard.checked_addresses`): `CONNECT` — для HTTPS и сокетов,
обычный запрос — для HTTP. Соединяется он с проверенным адресом, без второго распознавания
имени: иначе подмена DNS между проверкой и соединением увела бы его внутрь.

Chromium запускается с этим прокси и флагами `CHROMIUM_ARGS`: петлевые адреса — тоже через
прокси (`<-loopback>`, без него они шли бы мимо), UDP вне прокси выключен (WebRTC), QUIC
выключен. Перехват маршрутов остаётся вторым слоем.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from backend.shared.net.url_guard import UnsafeUrlError, checked_addresses

logger = logging.getLogger(__name__)

#: Имя → проверенный адрес для соединения; отказ — `UnsafeUrlError`. В тестах — подмена.
Resolve = Callable[[str], Awaitable[str]]

#: Флаги Chromium к прокси: петлевые адреса — тоже через него, UDP мимо него нет.
CHROMIUM_ARGS = (
    "--proxy-bypass-list=<-loopback>",
    "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
    "--disable-quic",
)

#: Заголовок запроса к прокси длиннее — не браузер (предел потока `asyncio`, 64 КБ).
#: Сколько ждать заголовка запроса и соединения с сайтом.
HEAD_TIMEOUT = 15.0
#: Сколько туннель ждёт данных, прежде чем закрыться.
IDLE_TIMEOUT = 60.0
_CHUNK = 64 * 1024

#: Заголовки одного звена: прокси их не передаёт, а соединение с сайтом закрывает сам.
_HOP_HEADERS = frozenset({"proxy-connection", "keep-alive", "proxy-authorization", "connection"})

_REFUSED = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_UNREACHABLE = b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_BAD = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
_TUNNEL = b"HTTP/1.1 200 Connection established\r\n\r\n"

Streams = tuple[asyncio.StreamReader, asyncio.StreamWriter]


async def checked(host: str) -> str:
    """Первый адрес имени из прошедших проверку (`url_guard.checked_addresses`)."""
    addresses = await asyncio.to_thread(checked_addresses, host)
    return addresses[0]


def _host_port(target: str, default: int) -> tuple[str, int]:
    """`host:port` цели `CONNECT` — с адресом IPv6 в скобках."""
    parts = urlsplit(f"//{target}")
    return parts.hostname or "", parts.port or default


async def _pipe(source: asyncio.StreamReader, target: asyncio.StreamWriter) -> None:
    """Переложить поток в одну сторону, пока он не кончится или не замолчит."""
    try:
        while chunk := await asyncio.wait_for(source.read(_CHUNK), IDLE_TIMEOUT):
            target.write(chunk)
            await target.drain()
    except (ConnectionError, TimeoutError) as exc:
        logger.debug("контакты: туннель прокси браузера закрыт — %r", exc)
    finally:
        target.close()


async def _tunnel(client: Streams, upstream: Streams) -> None:
    """Оба направления, пока одно не кончится: закрытие одной стороны закрывает другую."""
    await asyncio.gather(_pipe(client[0], upstream[1]), _pipe(upstream[0], client[1]))


def _name(line: str) -> str:
    return line.split(":", 1)[0].strip().lower()


def _forwarded(method: str, target: str, version: str, headers: list[str]) -> bytes:
    """Обычный HTTP-запрос к сайту: путь вместо полного адреса, без заголовков звена и с
    `Connection: close` — следующий запрос браузера придёт новым соединением и проверкой.
    Запрос на смену протокола (веб-сокет) оставляет свой `Connection: Upgrade`."""
    parts = urlsplit(target)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    lines = [line for line in headers if ":" in line]
    connection = next((line for line in lines if _name(line) == "connection"), "")
    kept = [line for line in lines if _name(line) not in _HOP_HEADERS]
    kept.append(connection if "upgrade" in connection.lower() else "Connection: close")
    return "\r\n".join([f"{method} {path} {version}", *kept, "", ""]).encode("latin-1")


class BrowserProxy:
    """Прокси на петлевом адресе: `CONNECT` и обычный HTTP, каждое соединение — через
    проверку адреса цели (`checked`)."""

    def __init__(self, resolve: Resolve = checked) -> None:
        self._resolve = resolve
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.StreamWriter] = set()

    async def start(self) -> str:
        """Поднять прокси; адрес — для `proxy` браузера."""
        self._server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        return f"http://127.0.0.1:{port}"

    async def close(self) -> None:
        """Закрыть прокси и все его соединения: без этого ожидание закрытия сервера ждало
        бы, пока браузер сам отпустит свои соединения."""
        if self._server is None:
            return
        self._server.close()
        for writer in list(self._clients):
            writer.close()
        await self._server.wait_closed()
        self._server = None

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._clients.add(writer)
        try:
            await self._serve((reader, writer))
        except (ConnectionError, TimeoutError, asyncio.IncompleteReadError) as exc:
            logger.debug("контакты: соединение браузера с прокси оборвалось — %r", exc)
        except asyncio.LimitOverrunError as exc:
            logger.info("контакты: прокси браузера получил заголовок длиннее предела — %r", exc)
        finally:
            self._clients.discard(writer)
            writer.close()

    async def _serve(self, client: Streams) -> None:
        reader, writer = client
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT)
        line, *headers = head.decode("latin-1").split("\r\n")
        method, target, version = [*line.split(" ", 2), "", "", ""][:3]
        if method.upper() == "CONNECT":
            host, port = _host_port(target, 443)
        elif urlsplit(target).scheme == "http":
            parts = urlsplit(target)
            host, port = parts.hostname or "", parts.port or 80
        else:
            writer.write(_BAD)
            await writer.drain()
            return
        upstream = await self._open(host, port, writer)
        if upstream is None:
            await writer.drain()
            return
        if method.upper() == "CONNECT":
            writer.write(_TUNNEL)
            await writer.drain()
        else:
            upstream[1].write(_forwarded(method, target, version, headers))
            await upstream[1].drain()
        await _tunnel(client, upstream)

    async def _open(self, host: str, port: int, writer: asyncio.StreamWriter) -> Streams | None:
        """Соединение с проверенным адресом цели; отказ или недоступность — ответом браузеру."""
        try:
            address = await self._resolve(host)
        except UnsafeUrlError as exc:
            logger.info("контакты: прокси браузера не пустил к %s — %s", host, exc)
            writer.write(_REFUSED)
            return None
        try:
            return await asyncio.wait_for(asyncio.open_connection(address, port), HEAD_TIMEOUT)
        except (OSError, TimeoutError) as exc:
            logger.debug(
                "контакты: прокси браузера не дозвонился до %s (%s) — %r", host, address, exc
            )
            writer.write(_UNREACHABLE)
            return None
