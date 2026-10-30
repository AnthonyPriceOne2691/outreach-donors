"""Защита исходящих запросов.

Главная проверка здесь — редирект. Адрес, проверенный на входе, ничего
не гарантирует: чужой сервер отвечает `302` и уводит клиент куда хочет,
уже мимо всякой проверки. Поэтому проверяется не «мы отказались ходить
на плохой адрес», а «мы отказались ходить туда даже когда нас туда
отправили».
"""

from __future__ import annotations

import asyncio
import threading
from typing import Any

import httpcore
import httpx
import pytest
from backend.shared.net.url_guard import (
    GuardedTransport,
    UnsafeUrlError,
    _PublicPeerOnly,
    assert_safe_url,
    guarded_client,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _no_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Имена в тестах не разрешаются по-настоящему: `example.com` считается
    внешним, `internal.test` — внутренним."""

    def fake_getaddrinfo(host: str, *_args: object, **_kwargs: object) -> list[object]:
        address = "10.1.2.3" if host.endswith("internal.test") else "93.184.216.34"
        return [(2, 1, 6, "", (address, 0))]

    monkeypatch.setattr("backend.shared.net.url_guard.socket.getaddrinfo", fake_getaddrinfo)


class TestSingleUrl:
    @pytest.mark.parametrize(
        "url",
        [
            "http://169.254.169.254/latest/meta-data/",  # метаданные облака
            "http://localhost/admin",
            "http://127.0.0.1:8000/",
            "http://10.0.0.5/",
            "http://192.168.1.1/",
            "http://[::1]/",
            "http://metadata.google.internal/",
            "http://site.internal.test/contact",  # имя ведёт внутрь
        ],
    )
    async def test_internal_addresses_are_refused(self, url: str) -> None:
        with pytest.raises(UnsafeUrlError):
            assert_safe_url(url)

    @pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://site.com/x", "gopher://site.com"])
    async def test_foreign_schemes_are_refused(self, url: str) -> None:
        with pytest.raises(UnsafeUrlError, match="схема"):
            assert_safe_url(url)

    async def test_ordinary_site_passes(self) -> None:
        assert_safe_url("https://example.com/contact/")

    async def test_refusal_says_what_is_wrong(self) -> None:
        with pytest.raises(UnsafeUrlError, match="внутренний адрес"):
            assert_safe_url("http://10.0.0.5/")

    async def test_unresolvable_name_is_allowed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Сбой DNS не должен останавливать сбор по живым донорам. Настоящую
        атаку это не пропускает: в ней имя обязано разрешиться."""

        def boom(*_args: object, **_kwargs: object) -> list[object]:
            raise OSError("DNS молчит")

        monkeypatch.setattr("backend.shared.net.url_guard.socket.getaddrinfo", boom)
        assert_safe_url("https://example.com/")


class TestRedirect:
    async def test_redirect_to_internal_is_refused(self) -> None:
        """Ради этого проверка живёт в транспорте: публичный сайт отвечает
        302 на внутренний адрес, и без неё клиент туда пойдёт."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "http://169.254.169.254/creds"})
            return httpx.Response(200, text="секреты облака")

        transport = GuardedTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            with pytest.raises(UnsafeUrlError):
                await client.get("https://example.com/")

    async def test_ordinary_redirect_still_works(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(301, headers={"location": "https://example.com/new"})
            return httpx.Response(200, text="страница контактов")

        transport = GuardedTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            response = await client.get("https://example.com/old")

        assert response.status_code == 200
        assert "контактов" in response.text


class TestResponseSize:
    async def test_huge_answer_is_cut(self) -> None:
        """Чужой сайт может отдавать гигабайты. Без потолка это память
        сервера, а не страница с контактами."""
        huge = b"x" * 5000

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=huge)

        transport = GuardedTransport(httpx.MockTransport(handler), max_response_bytes=1000)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.com/")

        assert len(response.content) <= 1000

    async def test_normal_answer_is_untouched(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="адрес: ads@site.com")

        transport = GuardedTransport(httpx.MockTransport(handler), max_response_bytes=1000)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.com/")

        assert response.text == "адрес: ads@site.com"


class TestClientFactory:
    async def test_factory_returns_a_guarded_client(self) -> None:
        async with guarded_client(timeout=5.0) as client:
            assert isinstance(client._transport, GuardedTransport)


class TestNameChangesBeforeConnecting:
    """Подмена DNS: проверке имя отвечает публичным адресом, соединению — внутренним.
    Сокет настоящий: на заглушке транспорта окна нет вовсе."""

    async def test_request_does_not_reach_the_inner_address(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        heard: list[bytes] = []

        async def inner_service(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            heard.append(await reader.read(1024))
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(inner_service, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        answers = iter(["93.184.216.34", "127.0.0.1"])

        def flips(host: object, wanted: int | None = None, *_args: object, **_kw: object) -> Any:
            return [(2, 1, 6, "", (next(answers), wanted or 0))]

        monkeypatch.setattr("backend.shared.net.url_guard.socket.getaddrinfo", flips)

        async with guarded_client(timeout=2.0) as client:
            with pytest.raises(UnsafeUrlError, match=r"127\.0\.0\.1"):
                await client.get(f"http://rebound.example.test:{port}/secret")

        server.close()
        await server.wait_closed()
        assert b"".join(heard) == b""

    async def test_public_peer_gets_the_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Тот же путь с разрешённым адресом на том конце: обычное соединение защита не режет."""

        async def site(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.read(1024)
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            await writer.drain()
            writer.close()
            await writer.wait_closed()

        server = await asyncio.start_server(site, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        monkeypatch.setattr("backend.shared.net.url_guard._is_private", lambda _address: False)

        async with guarded_client(timeout=2.0) as client:
            response = await client.get(f"http://127.0.0.1:{port}/")

        server.close()
        await server.wait_closed()
        assert response.text == "ok"

    async def test_connection_that_names_no_address_is_closed_and_refused(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        closed: list[bool] = []

        class Nameless:
            def get_extra_info(self, _info: str) -> None:
                return None

            async def aclose(self) -> None:
                closed.append(True)

        async def connected(*_args: object, **_kw: object) -> Nameless:
            return Nameless()

        monkeypatch.setattr(httpcore.AnyIOBackend, "connect_tcp", connected)

        with pytest.raises(UnsafeUrlError, match="неизвестен"):
            await _PublicPeerOnly().connect_tcp("example.com", 80)

        assert closed == [True]


class TestNameLookupOffTheLoop:
    """`getaddrinfo` блокирует. До 14.10.2026 он шёл прямо в транспорте, и пока
    система искала имя одного сайта, стояли все параллельные запросы процесса."""

    async def test_loop_keeps_running_while_a_name_is_looked_up(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        released = threading.Event()

        def slow_lookup(host: str, *_args: object, **_kwargs: object) -> list[object]:
            # Событие ставит корутина того же цикла: цикл стоит — она не успеет.
            assert released.wait(timeout=2), "цикл событий стоял, пока искалось имя"
            return [(2, 1, 6, "", ("93.184.216.34", 0))]

        monkeypatch.setattr("backend.shared.net.url_guard.socket.getaddrinfo", slow_lookup)

        async def release() -> None:
            await asyncio.sleep(0)
            released.set()

        transport = GuardedTransport(httpx.MockTransport(lambda _r: httpx.Response(200)))
        async with httpx.AsyncClient(transport=transport) as client:
            response, _ = await asyncio.gather(client.get("https://example.com/"), release())

        assert response.status_code == 200

    async def test_internal_name_found_off_the_loop_is_still_refused(self) -> None:
        """Проверка та же: имя, которое ведёт внутрь, отказ и из потока."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "example.com":
                return httpx.Response(302, headers={"location": "http://db.internal.test/"})
            return httpx.Response(200, text="внутренняя база")

        transport = GuardedTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
            with pytest.raises(UnsafeUrlError, match="внутренний адрес"):
                await client.get("https://example.com/")
