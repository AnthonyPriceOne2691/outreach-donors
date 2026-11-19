"""Транспорт прокси обхода — с защитой исходящих (аудит 19.11.2026).

Тесты пула прокси подставляют свой транспорт (`MockTransport`, голый `AsyncHTTPTransport`), и
защиту исходящих у прокси не исполнял ни один: правка `proxy_transport`, снявшая
`GuardedTransport`, прошла бы зелёной, а адреса страниц обхода приходят снаружи. Здесь — боевой
транспорт пула, без подмены. Прокси — свой, на петле: он записывает, что услышал. Имена
распознаются подставным ответом, сети нет: внутрь ведёт `intranet.example.test`, наружу —
`public.example.test`.
"""

from __future__ import annotations

import asyncio
import re
import socket
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from backend.features.crawl.proxies import ProxyPool, proxy_transport
from backend.shared.net import url_guard
from backend.shared.net.url_guard import GuardedTransport, UnsafeUrlError

#: Внутренний адрес, куда ведёт имя, и публичный — у обычного сайта.
INNER = "10.0.0.7"
PUBLIC = "93.184.216.34"


@pytest.fixture(autouse=True)
def _names(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подставное распознавание поверх запрета сети из `conftest.py`: два имени сайтов —
    ответом из словаря, остальное (петля прокси) — как было."""
    before = socket.getaddrinfo
    known = {"intranet.example.test": INNER, "public.example.test": PUBLIC}

    def resolve(host: object, port: object = None, *args: Any, **kwargs: Any) -> Any:
        if host in known:
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (known[str(host)], port or 0))]
        return before(host, port, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(url_guard.socket, "getaddrinfo", resolve)


Answer = Callable[[bytes], bytes]


class Gate:
    """Прокси на петле: каждый запрос — в `heard`, ответ — по `answer`."""

    def __init__(self, answer: Answer) -> None:
        self.answer = answer
        self.heard: list[bytes] = []

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await reader.readuntil(b"\r\n\r\n")
        self.heard.append(request)
        writer.write(self.answer(request))
        await writer.drain()
        writer.close()
        await writer.wait_closed()


@pytest.fixture
def gate_answer() -> Answer:
    return lambda _request: b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n"


@pytest.fixture
async def gate(gate_answer: Answer) -> AsyncIterator[tuple[Gate, ProxyPool]]:
    """Прокси на петле и пул с ним — боевым транспортом пула, без подмены."""
    proxy = Gate(gate_answer)
    server = await asyncio.start_server(proxy.handle, "127.0.0.1", 0)
    pool = ProxyPool([f"http://127.0.0.1:{server.sockets[0].getsockname()[1]}"])
    try:
        yield proxy, pool
    finally:
        await pool.aclose()
        server.close()
        await server.wait_closed()


def test_the_proxy_transport_is_guarded() -> None:
    assert isinstance(proxy_transport("http://gate.example.test:7777"), GuardedTransport)


@pytest.mark.parametrize(
    "url",
    ["http://intranet.example.test/admin", f"http://{INNER}/admin"],
    ids=["имя ведёт внутрь", "внутренний адрес"],
)
async def test_inside_address_is_refused_before_the_proxy_hears_it(
    gate: tuple[Gate, ProxyPool], url: str
) -> None:
    proxy, pool = gate
    client = await pool.client()

    with pytest.raises(UnsafeUrlError, match=re.escape(INNER)):
        await client.get(url)

    assert proxy.heard == [], "запрос внутрь не дошёл даже до прокси"


@pytest.mark.parametrize(
    "gate_answer",
    [
        lambda _request: (
            f"HTTP/1.1 302 Found\r\nLocation: http://{INNER}/\r\nContent-Length: 0\r\n\r\n"
        ).encode()
    ],
    ids=["переадресация внутрь"],
)
async def test_redirect_inside_through_the_proxy_is_refused(gate: tuple[Gate, ProxyPool]) -> None:
    """Публичный сайт отвечает через прокси `302` на внутренний адрес: шаг переадресации
    проверяется так же, и второй запрос до прокси не доходит."""
    proxy, pool = gate
    client = await pool.client()

    with pytest.raises(UnsafeUrlError, match=re.escape(INNER)):
        await client.get("http://public.example.test/", follow_redirects=True)

    [first] = proxy.heard
    assert first.startswith(b"GET http://public.example.test/ HTTP/1.1"), first


async def test_an_ordinary_site_goes_through_the_proxy(gate: tuple[Gate, ProxyPool]) -> None:
    """Защита не закрывает обычный обход: публичный сайт идёт через прокси как шёл."""
    proxy, pool = gate
    client = await pool.client()

    response = await client.get("http://public.example.test/page")

    assert response.status_code == httpx.codes.NO_CONTENT
    [first] = proxy.heard
    assert first.startswith(b"GET http://public.example.test/page HTTP/1.1"), first
