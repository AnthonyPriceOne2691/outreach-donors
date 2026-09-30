"""Ссылки со страниц чужих сайтов: кривая ссылка — отказ одной страницы.

23.09.2026 боевой проход контактов на 150 доменах оборвался целиком:
на одном сайте ссылка «hhttps://…/privacy-policy/», защита адресов
справедливо отказала, а исключение пролетело наверх. Страницы чужих
сайтов пишут люди, и опечатка в одной не должна стоить остальных.
"""

from __future__ import annotations

import httpx
from backend.features.contacts.pages import FetchedPage, PageFetcher
from backend.features.core.domain import PageKind
from backend.shared.net.url_guard import UnsafeUrlError


def _page() -> FetchedPage:
    return FetchedPage(url="https://site.example.test/", kind=PageKind.HOME, html="")


class TestFollow:
    def test_typo_scheme_on_own_domain_is_not_followed(self) -> None:
        fetcher = PageFetcher(httpx.AsyncClient())
        links = {
            "hhttps://site.example.test/privacy-policy/",
            "javascript:void(0)",
            "/contact",
        }

        followed = [url for url, _ in fetcher.follow(_page(), links)]

        assert followed == ["https://site.example.test/contact"]


class RefusingTransport(httpx.AsyncBaseTransport):
    """Транспорт, на котором стоит защита адресов, — как в бою."""

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise UnsafeUrlError(f"схема не разрешена: {request.url}")


class TestGet:
    async def test_refused_address_skips_the_page_not_the_run(self) -> None:
        async with httpx.AsyncClient(transport=RefusingTransport()) as client:
            fetcher = PageFetcher(client)

            page = await fetcher.get("https://10.0.0.1/admin", PageKind.CONTACT)

        assert page is None
        assert fetcher.attempts == 1
