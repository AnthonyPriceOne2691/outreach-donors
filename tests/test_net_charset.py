"""Кодировка страницы без charset в заголовке — из её `<meta>`.

httpx 0.28 без charset в `Content-Type` читает тело как utf-8: страница в cp1251, где
кодировка названа только в `<meta>`, доходила до модели «��������». Заголовок с charset
по-прежнему главнее, а незнакомое имя в `<meta>` не роняет чтение.
"""

from __future__ import annotations

import httpx
import pytest
from backend.features.contacts.pages import PageFetcher
from backend.features.core.domain import PageKind
from backend.shared.net.charset import SNIFF_BYTES, page_encoding
from backend.shared.net.url_guard import guarded_client

TEXT = "Реклама на сайте: пишите на ads@site.ru"


def _page(head: str, *, encoding: str = "cp1251") -> bytes:
    return f"<html><head>{head}<title>Контакты</title></head><body>{TEXT}</body></html>".encode(
        encoding
    )


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> list[httpx.Response]:
    """`guarded_client` целиком, но сайт — заглушка: ответ кладёт тест, сеть и DNS не нужны."""
    answers: list[httpx.Response] = []

    def fake_getaddrinfo(*_args: object, **_kwargs: object) -> list[object]:
        return [(2, 1, 6, "", ("93.184.216.34", 0))]

    monkeypatch.setattr("backend.shared.net.url_guard.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(
        "backend.shared.net.url_guard._public_only_transport",
        lambda: httpx.MockTransport(lambda _request: answers.pop(0)),
    )
    return answers


async def _read(site: list[httpx.Response], body: bytes, content_type: str = "text/html") -> str:
    site.append(httpx.Response(200, headers={"content-type": content_type}, content=body))
    async with guarded_client(timeout=5.0) as client:
        response = await client.get("https://example.com/")
    return response.text


class TestMetaCharset:
    @pytest.mark.parametrize(
        "head",
        [
            '<meta charset="windows-1251">',
            "<meta charset=windows-1251>",
            '<meta http-equiv="Content-Type" content="text/html; charset=windows-1251">',
            "<META CONTENT='text/html; charset=cp1251' HTTP-EQUIV='content-type'>",
        ],
        ids=["charset", "charset-bare", "http-equiv", "http-equiv-reversed"],
    )
    async def test_cp1251_named_only_in_meta(self, site: list[httpx.Response], head: str) -> None:
        assert TEXT in await _read(site, _page(head))

    async def test_header_charset_wins_over_meta(self, site: list[httpx.Response]) -> None:
        body = _page('<meta charset="windows-1251">', encoding="utf-8")
        assert TEXT in await _read(site, body, "text/html; charset=utf-8")

    async def test_no_charset_anywhere_is_utf8(self, site: list[httpx.Response]) -> None:
        assert TEXT in await _read(site, _page("", encoding="utf-8"))

    @pytest.mark.parametrize("name", ["no-such-charset", "base64", "zlib", "rot13", "undefined"])
    async def test_junk_name_reads_as_utf8(self, site: list[httpx.Response], name: str) -> None:
        """Незнакомое имя и нетекстовые кодеки (`zlib` к тому же распаковывает) — utf-8."""
        body = _page(f'<meta charset="{name}">', encoding="utf-8")
        assert TEXT in await _read(site, body)

    async def test_page_fetcher_sees_cp1251_text(self, site: list[httpx.Response]) -> None:
        """Ради этого и правка: разбор страниц (`pages.PageFetcher`) читает `response.text`."""
        site.append(
            httpx.Response(
                200,
                headers={"content-type": "text/html"},
                content=_page('<meta charset="windows-1251">'),
            )
        )
        async with guarded_client(timeout=5.0) as client:
            page = await PageFetcher(client).get("https://example.com/contact", PageKind.CONTACT)

        assert page is not None
        assert TEXT in page.html


class TestPageEncoding:
    @pytest.mark.parametrize(
        ("head", "expected"),
        [
            ('<meta charset="koi8-r">', "koi8-r"),
            ('<meta charset="UTF-16">', "utf-8"),  # прочитано как ASCII — значит, не utf-16
            ('<meta name="viewport" content="width=device-width">', "utf-8"),
        ],
    )
    def test_name_from_meta(self, head: str, expected: str) -> None:
        assert page_encoding(_page(head)) == expected

    def test_meta_past_the_sniffed_head_is_not_seen(self) -> None:
        body = b" " * SNIFF_BYTES + b'<meta charset="windows-1251">'
        assert page_encoding(body) == "utf-8"

    async def test_caller_keeps_own_default_encoding(self) -> None:
        async with guarded_client(timeout=5.0, default_encoding="koi8-r") as own:
            assert own._default_encoding == "koi8-r"
        async with guarded_client(timeout=5.0) as default:
            assert default._default_encoding is page_encoding
