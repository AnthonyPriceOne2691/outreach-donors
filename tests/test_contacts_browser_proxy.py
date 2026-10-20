"""Прокси браузера: каждое соединение — через проверку адреса цели (ревью 20.10.2026).

Перехват маршрутов вкладки не видит запросов SharedWorker, WebRTC и preconnect, поэтому сеть
браузера идёт только через этот прокси (`contacts/browser_proxy.py`). Здесь — его логика без
браузера: «сайт» — локальный сервер, а проверка цели подменена. Имя `site.example.test`
ведёт на петлевой адрес сервера, остальное — «внутреннее», и проверка отказывает: настоящая
отказала бы и петлевому адресу, а сеть тест не трогает.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

import pytest
from backend.features.contacts import browser_proxy
from backend.features.contacts.browser_proxy import BrowserProxy
from backend.shared.net import url_guard
from backend.shared.net.url_guard import UnsafeUrlError

pytestmark = pytest.mark.asyncio

SITE = "site.example.test"


async def _resolve(host: str) -> str:
    """Подмена проверки: сайт — на петлевом адресе сервера, остальное — внутреннее."""
    if host == SITE:
        return "127.0.0.1"
    raise UnsafeUrlError(f"внутренний адрес {host} запрещён")


@dataclass
class Upstream:
    """«Сайт»: первые байты каждого соединения и сколько соединений было."""

    port: int = 0
    seen: list[bytes] = field(default_factory=list)


async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, up: Upstream) -> None:
    data = await reader.read(4096)
    up.seen.append(data)
    if data.startswith((b"GET ", b"POST ")):
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
    else:
        writer.write(data)  # туннель: эхо
    await writer.drain()
    writer.close()


@pytest.fixture
async def upstream() -> AsyncIterator[Upstream]:
    up = Upstream()
    server = await asyncio.start_server(lambda r, w: _echo(r, w, up), "127.0.0.1", 0)
    up.port = server.sockets[0].getsockname()[1]
    yield up
    server.close()
    await server.wait_closed()


@pytest.fixture
async def proxy() -> AsyncIterator[tuple[str, int]]:
    guard = BrowserProxy(_resolve)
    url = await guard.start()
    host, port = url.removeprefix("http://").split(":")
    yield host, int(port)
    await guard.close()


async def _ask(proxy: tuple[str, int], head: bytes, then: bytes = b"") -> bytes:
    reader, writer = await asyncio.open_connection(*proxy)
    writer.write(head)
    await writer.drain()
    answer = await reader.readuntil(b"\r\n\r\n")
    if then:
        writer.write(then)
        await writer.drain()
    answer += await reader.read(4096)
    writer.close()
    await writer.wait_closed()
    return answer


class TestTunnel:
    async def test_external_target_is_tunnelled(
        self, proxy: tuple[str, int], upstream: Upstream
    ) -> None:
        """Внешняя цель — туннель: прокси соединяется с проверенным адресом — имя
        `site.example.test` в сети не распознаётся, — и байты идут в обе стороны."""
        head = f"CONNECT {SITE}:{upstream.port} HTTP/1.1\r\nHost: {SITE}\r\n\r\n".encode()

        answer = await _ask(proxy, head, then=b"ping")

        assert answer.startswith(b"HTTP/1.1 200 Connection established")
        assert answer.endswith(b"ping")
        assert upstream.seen == [b"ping"]

    async def test_internal_target_is_refused_without_a_connection(
        self, proxy: tuple[str, int], upstream: Upstream
    ) -> None:
        head = f"CONNECT 127.0.0.1:{upstream.port} HTTP/1.1\r\n\r\n".encode()

        answer = await _ask(proxy, head)

        assert answer.startswith(b"HTTP/1.1 403")
        assert upstream.seen == []


class TestPlainHttp:
    async def test_request_goes_to_the_checked_address_without_hop_headers(
        self, proxy: tuple[str, int], upstream: Upstream
    ) -> None:
        head = (
            f"GET http://{SITE}:{upstream.port}/kontakty/?a=1 HTTP/1.1\r\n"
            f"Host: {SITE}:{upstream.port}\r\nProxy-Connection: keep-alive\r\n"
            "Connection: keep-alive\r\nAccept: text/html\r\n\r\n"
        ).encode()

        answer = await _ask(proxy, head)

        assert answer.startswith(b"HTTP/1.1 200 OK")
        [sent] = upstream.seen
        assert sent.startswith(b"GET /kontakty/?a=1 HTTP/1.1\r\n")
        assert b"Accept: text/html" in sent
        assert b"Proxy-Connection" not in sent
        assert b"Connection: close" in sent

    async def test_internal_target_is_refused(
        self, proxy: tuple[str, int], upstream: Upstream
    ) -> None:
        head = f"GET http://metadata.internal:{upstream.port}/ HTTP/1.1\r\n\r\n".encode()

        answer = await _ask(proxy, head)

        assert answer.startswith(b"HTTP/1.1 403")
        assert upstream.seen == []

    async def test_request_that_is_not_for_a_proxy_is_refused(self, proxy: tuple[str, int]) -> None:
        answer = await _ask(proxy, b"GET /page HTTP/1.1\r\nHost: x\r\n\r\n")

        assert answer.startswith(b"HTTP/1.1 400")


class TestTheCheck:
    """Проверка цели — одним распознаванием имени (`url_guard.checked_addresses`)."""

    async def test_name_leading_inside_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(url_guard, "_resolved_addresses", lambda _host: ["10.0.0.7"])

        with pytest.raises(UnsafeUrlError, match=r"внутренний адрес 10\.0\.0\.7"):
            await browser_proxy.checked("site.example.test")

    async def test_unresolved_name_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Обходу нераспознанное имя не опасно — запрос не уйдёт; прокси соединяться не с чем."""
        monkeypatch.setattr(url_guard, "_resolved_addresses", lambda _host: [])

        with pytest.raises(UnsafeUrlError, match="не распознаётся"):
            await browser_proxy.checked("site.example.test")

    async def test_public_name_gives_the_address_to_connect_to(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(url_guard, "_resolved_addresses", lambda _host: ["93.184.216.34"])

        assert await browser_proxy.checked("site.example.test") == "93.184.216.34"

    @pytest.mark.parametrize("host", ["localhost", "169.254.169.254", "[::1]", "127.0.0.1"])
    async def test_service_and_loopback_addresses_are_refused(self, host: str) -> None:
        with pytest.raises(UnsafeUrlError):
            await browser_proxy.checked(host)
