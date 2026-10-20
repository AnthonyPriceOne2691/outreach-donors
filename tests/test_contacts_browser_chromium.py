"""Настоящий Chromium: сеть браузера — только через прокси с проверкой (ревью 20.10.2026).

Ревьюер довёл XHR SharedWorker до «внутреннего» адреса мимо перехвата маршрутов вкладки.
Здесь — регресс на настоящем браузере: страница разрешённого сайта заводит SharedWorker, а
он и страница идут к «внутреннему» серверу запросом, XHR и веб-сокетом, в том числе по
петлевому адресу. Ни один не доходит: сеть браузера — только через прокси
(`contacts/browser_proxy.py`), петлевые адреса — тоже. Оба сервера — локальные, проверка
цели подменена (`site.example.test` — сайт, остальное — внутреннее), DNS не нужен.

Где Playwright или Chromium нет (CI), тест пропускается.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator

import pytest

pytest.importorskip("playwright.async_api")

from backend.config import contacts as cfg
from backend.features.contacts.browser import PlaywrightRenderer
from backend.shared.net.url_guard import UnsafeUrlError

pytestmark = pytest.mark.asyncio

logger = logging.getLogger(__name__)

SITE = "site.example.test"


async def _resolve(host: str) -> str:
    if host == SITE:
        return "127.0.0.1"
    raise UnsafeUrlError(f"внутренний адрес {host} запрещён")


async def _http(pages: dict[str, tuple[str, str]], hits: list[str]) -> asyncio.Server:
    """Сервер на петлевом адресе: путь → (тип, тело); каждый запрос — в `hits`."""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, ConnectionError) as exc:
            # Браузер открыл соединение заранее и закрыл без запроса.
            logger.debug("тестовый сервер: соединение без запроса — %r", exc)
            writer.close()
            return
        path = head.split(b" ", 2)[1].decode()
        hits.append(path)
        kind, body = pages.get(path.split("?")[0], ("text/plain", "нет"))
        data = body.encode()
        writer.write(
            f"HTTP/1.1 200 OK\r\nContent-Type: {kind}\r\nContent-Length: {len(data)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + data
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(answer, "127.0.0.1", 0)


@pytest.fixture
async def inside() -> AsyncIterator[tuple[int, list[str]]]:
    """«Внутренний» сервер: сюда не должен дойти ни один запрос браузера."""
    hits: list[str] = []
    server = await _http({}, hits)
    yield server.sockets[0].getsockname()[1], hits
    server.close()
    await server.wait_closed()


async def test_worker_and_socket_do_not_reach_an_internal_address(
    inside: tuple[int, list[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    port, inside_hits = inside
    targets = (f"http://127.0.0.1:{port}", f"http://internal.example.test:{port}")
    worker = (
        f"fetch('{targets[0]}/worker-fetch').catch(() => {{}});"
        f"const x = new XMLHttpRequest(); x.open('GET', '{targets[1]}/worker-xhr'); x.send();"
        "onconnect = () => {};"
    )
    page = (
        "<html><body><p>сайт открылся</p><script>"
        "new SharedWorker('/sx.js');"
        f"try {{ new WebSocket('ws://127.0.0.1:{port}/socket'); }} catch (e) {{}}"
        f"fetch('{targets[0]}/page-fetch').catch(() => {{}});"
        "</script></body></html>"
    )
    site_hits: list[str] = []
    site = await _http(
        {"/": ("text/html; charset=utf-8", page), "/sx.js": ("application/javascript", worker)},
        site_hits,
    )
    site_port = site.sockets[0].getsockname()[1]
    monkeypatch.setattr(cfg, "BROWSER_SETTLE_SEC", 2.5)
    try:
        async with PlaywrightRenderer(resolve=_resolve) as renderer:
            if renderer is None:
                pytest.skip("Chromium не запускается на этой машине")
            html = await renderer.render(f"http://{SITE}:{site_port}/")
    finally:
        site.close()
        await site.wait_closed()

    assert html is not None
    assert "сайт открылся" in html
    assert "/sx.js" in site_hits, "воркер не завёлся — проверять было бы нечего"
    assert inside_hits == []
