"""Ссылки со страниц чужих сайтов: кривая ссылка — отказ одной страницы.

23.09.2026 боевой проход контактов на 150 доменах оборвался целиком:
на одном сайте ссылка «hhttps://…/privacy-policy/», защита адресов
справедливо отказала, а исключение пролетело наверх. Страницы чужих
сайтов пишут люди, и опечатка в одной не должна стоить остальных.
"""

from __future__ import annotations

import httpx
import pytest
from backend.config import contacts as cfg
from backend.features.contacts.pages import (
    FetchedPage,
    PageFetcher,
    _kind_of,
    language_hint,
    slug_urls,
)
from backend.features.contacts.slugs import LINK_MARKERS
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


class TestLanguageOfTheSite:
    """Слаги на языке площадки и то, ради чего они введены.

    Проверяется не только «русский слаг нашёлся», но и главное: что он
    попадает в бюджет попыток. Список, до которого обход не доходит,
    выглядит как поддержка языка, но ею не является.
    """

    def test_tld_says_the_language(self) -> None:
        assert language_hint("<html><body></body></html>", "sport.ru") == "ru"

    def test_declared_language_wins_over_the_zone(self) -> None:
        """`lang` — прямое свидетельство, зона — догадка."""
        assert language_hint('<html lang="id-ID"></html>', "news.ru") == "id"

    @pytest.mark.parametrize("host", ["news.com", "site.be", "site.ch", "site.co"])
    def test_nothing_to_guess_is_a_normal_answer(self, host: str) -> None:
        """Родовая и многоязычные зоны молчат: угадывание там вредит."""
        assert language_hint('<html lang="en"></html>', host) is None

    @pytest.mark.parametrize(
        ("language", "expected"),
        [
            ("id", {"iklan", "hubungi-kami", "tentang-kami", "syarat-ketentuan"}),
            ("ru", {"reklama", "kontakty", "o-nas", "politika-konfidencialnosti"}),
        ],
    )
    def test_local_slugs_come_first(self, language: str, expected: set[str]) -> None:
        first_round = [
            url for url, _ in list(slug_urls("https://site.test", language=language))[:4]
        ]
        assert {url.rstrip("/").rsplit("/", 1)[-1] for url in first_round} == expected

    def test_local_slugs_fit_the_attempt_budget(self) -> None:
        """Ради этого всё и сделано: 47 слагов против 24 попыток на домен.

        `hubungi-kami` лежит десятым в основном списке контактов, то есть
        за пределами бюджета: круговой обход доходит примерно до шестого
        слага каждого вида. С подсказкой языка он первый.
        """
        budget = cfg.MAX_ATTEMPTS_PER_DOMAIN
        within = [url for url, _ in list(slug_urls("https://site.test", language="id"))[:budget]]
        assert any(url.endswith("/hubungi-kami/") for url in within)
        assert any(url.endswith("/redaksi/") for url in within)

        without = [url for url, _ in list(slug_urls("https://site.test"))[:budget]]
        assert not any(url.endswith("/hubungi-kami/") for url in without)

    def test_percent_encoded_path_is_recognised(self) -> None:
        """Арабский слаг уезжает в запрос процентными последовательностями.

        Без раскодирования страница контактов считалась бы главной, и её
        адрес получил бы вес в четыре раза меньше заслуженного.
        """
        encoded = "https://x.ae/%D8%A7%D8%AA%D8%B5%D9%84-%D8%A8%D9%86%D8%A7/"
        assert _kind_of(encoded) is PageKind.CONTACT

    def test_link_markers_know_local_words(self) -> None:
        """Ссылку с главной ищем по тексту — это дешевле любого слага."""
        for word in ("контакты", "реклама", "hubungi", "iklan", "اتصل", "ติดต่อ"):
            assert word in LINK_MARKERS
