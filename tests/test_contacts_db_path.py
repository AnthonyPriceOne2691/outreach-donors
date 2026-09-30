"""Поиск контактов по пути базы: четыре поломки, найденные после #120.

Путь базы — это то, откуда адрес донора попадает в письмо. Каждая поломка
здесь либо даёт адрес, по которому письмо отобьётся (а отказы бьют по
репутации почтовых доменов), либо тратит бюджет обхода впустую:

1. JSON внутри `<script>` экранирует `>` как `\\u003e`; обратная косая черта
   в адрес не входит, а `u003e` входит — и со страницы уезжал
   `u003eprivacy@…` (живой прогон 30.09.2026, во всех версиях кода);
2. адрес, склеенный с текстом, проходил проверку, если зона не из
   `com|net|org`: `iklan@site.comuntuk`, `info@site.ruand` — такой зоны нет;
3. ссылка, узнанная только по тексту («Support for Ukraine grows»), при
   адресе-статье получала вид «главная» и шла раньше угаданных слагов —
   восемь таких ссылок съедали бюджет, и до `/contact/` обход не доходил;
4. кириллический раздел (`/контакты/`, `/реклама/`) типизировался как
   главная: слагов кириллицей в словаре не было, только транслит.
"""

from __future__ import annotations

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.extract import extract_emails
from backend.features.contacts.ladder import ContactLadder
from backend.features.contacts.pages import _kind_of
from backend.features.contacts.quality import rejection_reason
from backend.features.core.domain import ContactStatus, PageKind


@pytest.fixture(autouse=True)
def _mx_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    """Домен принимает почту: здесь проверяется обход страниц, а не ступень 0."""

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


class Site:
    """Сайт-заглушка: заданные страницы, остальное — 404, запросы считаются."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requested.append(request.url.path)
        if "rdap.org" in str(request.url):
            return httpx.Response(404, json={"errorCode": 404})
        body = self.pages.get(request.url.path)
        if body is None:
            return httpx.Response(404, text="нет такой страницы")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})


class TestJsonEscapesInScripts:
    """Находка 1: экранирование JSON раскодируется до поиска адресов."""

    def test_escaped_markup_does_not_leak_into_the_address(self) -> None:
        html = (
            '<html><body><script>window.__DATA__ = {"body": '
            '"\\u003cp\\u003eWrite to \\u003ca href=\\"mailto:privacy@site.com\\"'
            '\\u003eprivacy@site.com\\u003c/a\\u003e"};</script></body></html>'
        )
        assert extract_emails(html) == {"privacy@site.com"}

    def test_escaped_at_sign_is_an_address(self) -> None:
        html = '<html><body><script>{"mail": "info\\u0040site.com"}</script></body></html>'
        assert extract_emails(html) == {"info@site.com"}

    def test_hex_escape_is_decoded_too(self) -> None:
        html = "<html><body><script>var m = 'ads\\x40site.com';</script></body></html>"
        assert extract_emails(html) == {"ads@site.com"}

    @pytest.mark.parametrize(
        "email", ["u003eprivacy@site.com", "u002f995b2433a7014c708e4dcc@site.com"]
    )
    def test_escape_fragment_already_in_the_base_is_rejected(self, email: str) -> None:
        """Сохранённые раньше обрывки отсеиваются при сборке письма."""
        reason = rejection_reason(email)
        assert reason is not None
        assert "экранирован" in reason


class TestZoneMustExist:
    """Находка 2: зона адреса обязана быть в списке публичных суффиксов."""

    @pytest.mark.parametrize(
        "email",
        [
            "iklan@site.comuntuk",
            "redaksi@kompas.co.idyang",
            "contacto@site.compara",
            "info@site.ruand",
            "info@site.complease",
        ],
    )
    def test_word_glued_to_the_zone_is_rejected(self, email: str) -> None:
        assert rejection_reason(email) is not None

    @pytest.mark.parametrize(
        "email",
        [
            "info@site.com",
            "info@site.co.uk",
            "redaksi@site.co.id",
            "hello@startup.ai",
            "team@product.app",
            "ads@media.agency",
            "info@site.africa",
            "contact@monsite.ovh",
            "info@site.xn--p1ai",
            "press@company.marketing",
        ],
    )
    def test_real_zones_are_accepted(self, email: str) -> None:
        assert rejection_reason(email) is None


def _page(body: str, *, lang: str = "en") -> str:
    return f'<html lang="{lang}"><body>{body}</body></html>'


class TestTextOnlyLinksDoNotEatTheBudget:
    """Находка 3: ссылки, узнанные только по тексту, — не больше двух до догадок."""

    async def test_contact_page_is_reached_past_headline_links(self) -> None:
        headlines = "".join(
            f'<a href="/news/2024/support-for-the-local-team-number-{n}/">Support for team {n}</a>'
            for n in range(9)
        )
        articles = {
            f"/news/2024/support-for-the-local-team-number-{n}/": _page("Статья без адреса.")
            for n in range(9)
        }
        site = Site(
            {
                "/": _page(headlines),
                **articles,
                "/contact/": _page('<a href="mailto:info@site.com">write</a>'),
            }
        )

        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
            result = await ContactLadder(http).find("site.com")

        assert result.status is ContactStatus.FOUND
        assert result.contact is not None
        assert result.contact.email == "info@site.com"
        fetched_articles = [p for p in site.requested if p.startswith("/news/")]
        assert len(fetched_articles) <= 2

    async def test_unguessable_section_named_by_text_is_still_visited(self) -> None:
        """Ради этого текст ссылки и смотрится: раздел по адресу /p/12345."""
        site = Site(
            {
                "/": _page('<a href="/p/12345">Hubungi Kami</a>', lang="id"),
                "/p/12345": _page('<a href="mailto:redaksi@site.id">kirim</a>', lang="id"),
            }
        )

        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
            result = await ContactLadder(http).find("site.id")

        assert result.contact is not None
        assert result.contact.email == "redaksi@site.id"


class TestCyrillicSections:
    """Находка 4: раздел кириллицей узнаётся так же, как транслитом."""

    @pytest.mark.parametrize(
        ("url", "kind"),
        [
            ("https://site.ru/контакты/", PageKind.CONTACT),
            ("https://site.ru/%D0%BA%D0%BE%D0%BD%D1%82%D0%B0%D0%BA%D1%82%D1%8B/", PageKind.CONTACT),
            ("https://site.ru/реклама/", PageKind.MONEY),
            ("https://site.ru/размещение-рекламы/", PageKind.MONEY),
            ("https://site.ru/о-нас/", PageKind.ABOUT),
            ("https://site.ru/политика-конфиденциальности/", PageKind.LEGAL),
        ],
    )
    def test_cyrillic_section_is_typed(self, url: str, kind: PageKind) -> None:
        assert _kind_of(url) is kind

    def test_cyrillic_headline_is_still_home(self) -> None:
        assert _kind_of("https://site.ru/news/реклама-на-тв-запрещена-с-понедельника/") is (
            PageKind.HOME
        )
