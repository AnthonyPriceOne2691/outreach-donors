"""Ссылки со страниц чужих сайтов: кривая ссылка — отказ одной страницы.

23.09.2026 боевой проход контактов на 150 доменах оборвался целиком:
на одном сайте ссылка «hhttps://…/privacy-policy/», защита адресов
справедливо отказала, а исключение пролетело наверх. Страницы чужих
сайтов пишут люди, и опечатка в одной не должна стоить остальных.
"""

from __future__ import annotations

import ast
import logging
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from backend.config import contacts as cfg
from backend.features.contacts.pages import (
    HEADERS,
    FetchedPage,
    PageFetcher,
    has_contact_form,
    kind_of,
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
        assert kind_of(encoded) is PageKind.CONTACT

    def test_link_markers_know_local_words(self) -> None:
        """Ссылку с главной ищем по тексту — это дешевле любого слага."""
        for word in ("контакты", "реклама", "hubungi", "iklan", "اتصل", "ติดต่อ"):
            assert word in LINK_MARKERS


class TestWhatCountsAsAContactForm:
    """Форма, через которую можно написать владельцу, — а не любая `<form>`."""

    @pytest.mark.parametrize(
        "html",
        [
            '<form role="search" action="/search"><input name="q"></form>',
            '<form action="/subscribe"><input type="email" name="email"></form>',
            '<form action="/login"><input name="user"><input type="password"></form>',
            '<form action="/wp-comments-post.php" id="commentform" class="comment-form">'
            '<textarea name="comment"></textarea></form>',
        ],
        ids=["search", "subscribe", "login", "comment"],
    )
    def test_not_a_contact_form(self, html: str) -> None:
        assert not has_contact_form(f"<html><body>{html}</body></html>")

    @pytest.mark.parametrize(
        "html",
        [
            '<form action="/send"><input name="email"><textarea name="message"></textarea></form>',
            '<form action="/send"><TEXTAREA name="message">',  # без закрывающего тега
            '<div class="wpcf7"><form action="/#wpcf7-f1"><input name="your-email"></form></div>',
        ],
        ids=["own", "unclosed", "builder"],
    )
    def test_contact_form(self, html: str) -> None:
        assert has_contact_form(f"<html><body>{html}</body></html>")


ROOT = Path(__file__).resolve().parent.parent


class Recording:
    """Сайт-подделка: любая страница открывается, заголовки каждого запроса запомнены."""

    def __init__(self) -> None:
        self.sent: list[httpx.Headers] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.sent.append(request.headers)
        return httpx.Response(
            200, html="<html><body><p>Страница</p></body></html>", request=request
        )


async def _every_request(language: str | None = None) -> list[dict[str, str]]:
    """Главная, страница и карта сайта одним загрузчиком — заголовки браузера (`HEADERS`) каждого
    запроса. Без языка загрузчик создаётся, как его создают доноры и контакты: без слова о языке."""
    site = Recording()
    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as client:
        fetcher = (
            PageFetcher(client) if language is None else PageFetcher(client, language=language)
        )
        await fetcher.home("site.example.test")
        await fetcher.get("https://site.example.test/contact/", PageKind.CONTACT)
        await fetcher.text("https://site.example.test/sitemap.xml")
    return [{name: sent[name] for name in HEADERS} for sent in site.sent]


class TestPageLanguage:
    """Язык страницы у загрузчика — необязательный: с ним `Accept-Language` на нём, без него
    заголовки прежние. Сайт с версиями по языку браузера отдаёт главную на языке из
    `Accept-Language`, и с одним английским судья сегмента продаж читал английскую главную при
    прогоне на другом языке — а человек и письмо видят сайт на языке прогона."""

    async def test_without_a_language_every_request_carries_the_old_headers_whole(self) -> None:
        """Доноры, контакты и двери языка не передают: их запросы — `HEADERS` целиком, с прежним
        английским `Accept-Language`."""
        sent = await _every_request()

        assert sent == [HEADERS] * 3
        assert HEADERS["Accept-Language"] == "en-US,en;q=0.9"

    @pytest.mark.parametrize(
        ("language", "accept"),
        [
            ("ru", "ru,en;q=0.9"),
            ("el", "el,en;q=0.9"),
            ("ar", "ar,en;q=0.9"),
            ("fil", "fil,en;q=0.9"),
            ("pt-BR", "pt,en;q=0.9"),
            ("zh_Hant", "zh,en;q=0.9"),
            (" Kk ", "kk,en;q=0.9"),
            ("en", "en"),
            ("en-US", "en"),
            ("EN_gb", "en"),
        ],
    )
    async def test_the_language_goes_first_and_english_is_a_lighter_fallback(
        self, language: str, accept: str
    ) -> None:
        """Язык — первым, английский — запасным с меньшим весом: версии на языке у сайта нет —
        английская, как раньше. Английскому — только он. Код с регионом или письменностью — основой
        кода. Меняется одно `Accept-Language`, остальные заголовки браузера те же."""
        sent = await _every_request(language)

        assert sent == [{**HEADERS, "Accept-Language": accept}] * 3

    @pytest.mark.parametrize("language", ["", "   "])
    async def test_an_empty_language_is_no_language(self, language: str) -> None:
        assert await _every_request(language) == [HEADERS] * 3

    @pytest.mark.parametrize("language", ["русский", "ru,de", "ru;q=1", "r", "x-klingon", "-ru"])
    async def test_what_is_not_a_language_code_keeps_the_old_header_and_says_so(
        self, language: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Заголовок уходит только латиницей: не код языка — другая письменность, перечень, вес —
        в запрос не идёт. Страница просится с прежним заголовком, журнал называет, что пришло."""
        with caplog.at_level(logging.WARNING, logger="backend.features.contacts.pages"):
            sent = await _every_request(language)

        assert sent == [HEADERS] * 3
        assert repr(language) in caplog.text


#: Где язык страницы выбирают или передают дальше: судья сегмента продаж просит главную на языке
#: писем прогона (`segment_text.page_home`), eval судьи дочитывает её на языке писем набора, как
#: прогон (`eval_sales_segment_home`), разбор главной передаёт язык загрузчику (`check_home`).
LANGUAGE_CALLERS = {
    "backend/features/donors/home_signals.py",
    "backend/features/sales/segment_text.py",
    "scripts/eval_sales_segment_home.py",
}
#: Чем страницы открываются: загрузчик и разбор главной поверх него.
FETCHERS = {"PageFetcher", "check_home"}


def _fetches() -> Iterator[tuple[str, ast.Call]]:
    """Вызовы загрузчика (`PageFetcher(…)`) и разбора главной (`check_home(…)`) в коде сервиса
    и скриптах."""
    for top in ("backend", "scripts"):
        for path in sorted((ROOT / top).rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if not any(f"{name}(" in source for name in FETCHERS):
                continue
            for node in ast.walk(ast.parse(source)):
                if isinstance(node, ast.Call) and _callee(node.func) in FETCHERS:
                    yield path.relative_to(ROOT).as_posix(), node


def _callee(func: ast.expr) -> str | None:
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def test_donors_and_contacts_open_pages_only_without_a_language() -> None:
    """Страницы сайтов открываются загрузчиком (`PageFetcher`) — сам или через разбор главной
    (`check_home`). Язык ему передаёт только судья продаж и его eval: доноры, контакты, двери и
    досуды зовут их без языка, и их запросы несут прежние заголовки (`TestPageLanguage`).
    Распаковка `**` в вызове — тоже язык: через неё он передался бы молча."""
    calls = list(_fetches())
    callers = {path for path, _ in calls}
    asking = {
        path
        for path, call in calls
        if any(word.arg in {"language", None} for word in call.keywords)
    }

    assert {
        "backend/features/contacts/site_steps.py",
        "backend/features/donors/judging.py",
    } <= callers, f"поиск не видит вызовов контактов и доноров: {sorted(callers)}"
    assert asking == LANGUAGE_CALLERS
