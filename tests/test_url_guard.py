"""Защита исходящих запросов.

Главная проверка здесь — редирект. Адрес, проверенный на входе, ничего
не гарантирует: чужой сервер отвечает `302` и уводит клиент куда хочет,
уже мимо всякой проверки. Поэтому проверяется не «мы отказались ходить
на плохой адрес», а «мы отказались ходить туда даже когда нас туда
отправили».
"""

from __future__ import annotations

import httpx
import pytest
from backend.shared.net.url_guard import (
    GuardedTransport,
    UnsafeUrlError,
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
