"""Оснастка `_no_real_network` (`tests/conftest.py`): тест не выходит в сеть, петля открыта.

Ревью 20.10.2026: тесты прогона и меню главных открывали `good.com` по-настоящему. Адреса
здесь — из RFC 2606 и RFC 5737: даже если оснастку снимут, запрос не уйдёт чужому сайту.
"""

from __future__ import annotations

import asyncio
import socket

import httpx
import pytest
from backend.shared.net.url_guard import guarded_client


class TestOutside:
    def test_name_is_not_resolved(self) -> None:
        with pytest.raises(socket.gaierror, match=r"тест не ходит в сеть: имя 'example\.com'"):
            socket.getaddrinfo("example.com", 443)

    def test_address_is_not_connected(self) -> None:
        with (
            socket.socket() as sock,
            pytest.raises(OSError, match="тест не ходит в сеть: соединение с"),
        ):
            sock.connect(("192.0.2.1", 80))

    def test_address_is_not_connected_without_an_exception_either(self) -> None:
        with (
            socket.socket() as sock,
            pytest.raises(OSError, match="тест не ходит в сеть: соединение с"),
        ):
            sock.connect_ex(("192.0.2.1", 80))

    async def test_crawl_client_gets_no_answer_instead_of_a_real_site(self) -> None:
        """Обход чужих сайтов в тесте видит машину без сети, а не ответ настоящего сайта."""
        async with guarded_client(timeout=1) as http:
            with pytest.raises(httpx.ConnectError, match="тест не ходит в сеть"):
                await http.get("https://example.com/")


class TestLoopback:
    async def test_server_of_the_test_answers_by_name_and_by_address(self) -> None:
        """База, Redis и серверы самих тестов живут на петле — им оснастка не мешает."""

        async def answer(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            writer.write(b"ok")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(answer, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        try:
            for host in ("127.0.0.1", "localhost"):
                reader, writer = await asyncio.open_connection(host, port)
                assert await reader.read() == b"ok", host
                writer.close()
                await writer.wait_closed()
        finally:
            server.close()
            await server.wait_closed()
