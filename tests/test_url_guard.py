"""Защита исходящих запросов.

Главная проверка здесь — редирект. Адрес, проверенный на входе, ничего
не гарантирует: чужой сервер отвечает `302` и уводит клиент куда хочет,
уже мимо всякой проверки. Поэтому проверяется не «мы отказались ходить
на плохой адрес», а «мы отказались ходить туда даже когда нас туда
отправили».
"""

from __future__ import annotations

import asyncio
import gzip
import threading
import tracemalloc
import zlib
from collections.abc import AsyncIterator
from typing import Any

import httpcore
import httpx
import pytest
from backend.shared.net.url_guard import (
    INFLATE_STEP,
    GuardedTransport,
    UnboundedEncodingError,
    UnsafeUrlError,
    _Inflater,
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

    @pytest.mark.parametrize("host", ["100.64.0.1", "100.100.100.200", "carrier.example.test"])
    async def test_shared_carrier_network_is_inner(
        self, host: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`is_private` не считает 100.64.0.0/10 внутренней, а в ней живёт адрес метаданных
        облака: буквальное имя он запрещал, а имя, ведущее на него, проходило."""

        def carrier(_host: str, *_args: object, **_kw: object) -> list[object]:
            return [(2, 1, 6, "", ("100.100.100.200", 0))]

        monkeypatch.setattr("backend.shared.net.url_guard.socket.getaddrinfo", carrier)

        with pytest.raises(UnsafeUrlError):
            assert_safe_url(f"http://{host}/")

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


class _Body(httpx.AsyncByteStream):
    """Тело кусками, как с сети. `httpx.Response(content=…)` читает и распаковывает тело
    прямо в конструкторе — заглушка с ним сама развернула бы бомбу мимо транспорта."""

    def __init__(self, *chunks: bytes) -> None:
        self._chunks = chunks

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


def _gzip_bomb(size: int) -> bytes:
    """Нули длиной `size` в gzip — без того, чтобы держать их в памяти целиком."""
    packer = zlib.compressobj(9, zlib.DEFLATED, zlib.MAX_WBITS | 16)
    step = 1_000_000
    parts = [packer.compress(b"\0" * step) for _ in range(size // step)]
    return b"".join([*parts, packer.flush()])


def _compressed(body: bytes, coding: str, *, asked: list[str] | None = None) -> httpx.MockTransport:
    """Сайт, отвечающий сжатым телом; что у него просили — в `asked`."""

    def handler(request: httpx.Request) -> httpx.Response:
        if asked is not None:
            asked.append(request.headers.get("accept-encoding", ""))
        headers = {"content-type": "text/html", "content-encoding": coding}
        return httpx.Response(200, headers=headers, stream=_Body(body))

    return httpx.MockTransport(handler)


class TestCompressedAnswer:
    """Потолок считает распакованные байты: 29 КБ gzip разворачиваются в 30 МБ, а до 20.11.2026
    распаковку делал httpx выше транспорта и ничем её не ограничивал."""

    async def test_gzip_bomb_is_cut_at_the_limit(self) -> None:
        bomb = _gzip_bomb(30_000_000)
        assert len(bomb) < 50_000

        transport = GuardedTransport(_compressed(bomb, "gzip"), max_response_bytes=1_000_000)
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.com/")

        assert len(response.content) == 1_000_000

    async def test_each_step_and_memory_stay_small(self) -> None:
        """Каждая выдача распаковки — не больше шага, а память не растёт до размера бомбы:
        без защиты пик был бы больше 30 МБ."""
        bomb = _gzip_bomb(30_000_000)
        transport = GuardedTransport(_compressed(bomb, "gzip"))
        sizes: list[int] = []

        tracemalloc.start()
        try:
            async with (
                httpx.AsyncClient(transport=transport) as client,
                client.stream("GET", "https://example.com/") as response,
            ):
                async for chunk in response.aiter_raw():
                    sizes.append(len(chunk))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert max(sizes) <= INFLATE_STEP
        assert sum(sizes) == 10 * 1024 * 1024
        assert peak < 5_000_000

    async def test_step_is_not_above_a_small_limit(self) -> None:
        bomb = _gzip_bomb(2_000_000)
        transport = GuardedTransport(_compressed(bomb, "gzip"), max_response_bytes=1000)
        async with (
            httpx.AsyncClient(transport=transport) as client,
            client.stream("GET", "https://example.com/") as response,
        ):
            sizes = [len(chunk) async for chunk in response.aiter_raw()]

        assert max(sizes) <= 1000
        assert sum(sizes) == 1000

    async def test_inflater_leaves_the_rest_packed_until_asked(self) -> None:
        """Одна выдача — один шаг: остальной вход ждёт в `unconsumed_tail`."""
        packed = zlib.compress(b"\0" * 5_000_000, 9)
        inflater = _Inflater("deflate", INFLATE_STEP)

        first = next(inflater.feed(packed))

        assert len(first) == INFLATE_STEP
        assert inflater._zlib.unconsumed_tail

    async def test_ordinary_gzip_page_is_read_whole(self) -> None:
        page = ("<html><body>" + "реклама: ads@site.com " * 5000 + "</body></html>").encode()
        asked: list[str] = []
        transport = GuardedTransport(_compressed(gzip.compress(page), "gzip", asked=asked))
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.com/")

        assert response.content == page
        assert "content-encoding" not in response.headers
        assert "content-length" not in response.headers
        assert asked == ["gzip, deflate"]

    @pytest.mark.parametrize("wbits", [zlib.MAX_WBITS, -zlib.MAX_WBITS], ids=["zlib", "raw"])
    async def test_deflate_with_and_without_zlib_wrapper(self, wbits: int) -> None:
        page = "страница контактов: ads@site.com".encode() * 100
        packer = zlib.compressobj(6, zlib.DEFLATED, wbits)
        packed = packer.compress(page) + packer.flush()
        transport = GuardedTransport(_compressed(packed, "deflate"))
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.com/")

        assert response.content == page

    async def test_uncompressed_answer_keeps_its_headers(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-type": "text/plain"}, stream=_Body(b"ok"))

        transport = GuardedTransport(httpx.MockTransport(handler))
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.com/")

        assert response.text == "ok"
        assert response.headers["content-type"] == "text/plain"

    @pytest.mark.parametrize("coding", ["br", "zstd", "gzip, gzip", "compress"])
    async def test_unasked_encoding_is_refused_in_words(self, coding: str) -> None:
        """br и zstd распаковать с пределом нечем. Отказ — обычная ошибка httpx: обход
        и поиск адресов ловят `httpx.HTTPError` и пишут причину."""
        transport = GuardedTransport(_compressed(b"\x1b\x00\x00", coding))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(UnboundedEncodingError, match="сжал ответ") as caught:
                await client.get("https://example.com/")

        assert isinstance(caught.value, httpx.HTTPError)
        assert coding in str(caught.value)

    async def test_unasked_encoding_with_empty_body_passes(self) -> None:
        """Редирект или HEAD с `Content-Encoding: br` и пустым телом — распаковывать нечего."""
        transport = GuardedTransport(_compressed(b"", "br"))
        async with httpx.AsyncClient(transport=transport) as client:
            response = await client.get("https://example.com/")

        assert response.content == b""

    async def test_broken_gzip_is_a_decoding_error(self) -> None:
        transport = GuardedTransport(_compressed(b"\x1f\x8b not gzip at all", "gzip"))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.DecodingError, match="не распаковывается"):
                await client.get("https://example.com/")

    @pytest.mark.parametrize(
        ("asked", "sent"),
        [
            ("gzip, deflate, br, zstd", "gzip, deflate"),
            ("br;q=1.0, gzip;q=0.5", "gzip;q=0.5"),
            ("identity", "identity"),
            ("br", "gzip, deflate"),
        ],
    )
    async def test_only_bounded_encodings_are_asked(self, asked: str, sent: str) -> None:
        seen: list[str] = []
        transport = GuardedTransport(_compressed(b"", "identity", asked=seen))
        async with httpx.AsyncClient(transport=transport) as client:
            await client.get("https://example.com/", headers={"Accept-Encoding": asked})

        assert seen == [sent]


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
