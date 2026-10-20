"""Лестница: причина «адреса нет» у непрочитанного сайта, адрес формы и поиск глубже.

Живой замер 20.10.2026 на двенадцати донорах с исходом «адреса нет»: у двух главная —
страница проверки AWS WAF с ответом 202, у двух — 403, у одного — 451. Лестница писала
им «адреса нет», будто сайт прочитан. Здесь — что теперь: страница проверки не
считается страницей сайта, у исхода есть причина словами, браузер зовётся тем, кому
поможет. Сайты — синтетические, сеть не трогается, браузер — заглушка.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from backend.config import contacts as cfg
from backend.features.contacts import mx
from backend.features.contacts.ladder import (
    NO_MAIL_REASON,
    ContactLadder,
    DeepLadder,
    LadderResult,
)
from backend.features.contacts.pages import CHALLENGE, is_challenge
from backend.features.contacts.section_map import pick_sections
from backend.features.core.domain import ContactStatus, PageKind

HOST = "news.example.test"

AWS_WAF = """<!DOCTYPE html><html><head><title></title>
<script>window.awsWafCookieDomainList = []; window.gokuProps = {"key":"x"};</script>
<script src="https://abc.token.awswaf.com/abc/challenge.js"></script></head>
<body><div id="challenge-container"></div>
<script>AwsWafIntegration.getToken().then(() => window.location.reload(true));</script>
<noscript>verify that you're not a robot</noscript></body></html>"""
CLOUDFLARE = "<html><head><title>Just a moment...</title></head><body>cf-chl-opt</body></html>"
EMPTY = "<html><body><p>Новости дня</p></body></html>"
WITH_ADDRESS = '<html><body><a href="mailto:editor@news.example.test">почта</a></body></html>'
CONTACT_FORM = (
    '<html><body><form action="/send"><input name="email"><textarea name="message">'
    "</textarea></form></body></html>"
)


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


class Site:
    """Путь → (код, тело) или тело с кодом 200. Хост — любой: апекс и `www.` одинаковы."""

    def __init__(self, pages: dict[str, tuple[int, str] | str]) -> None:
        self.pages = pages
        self.requested: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requested.append(str(request.url))
        found = self.pages.get(request.url.path)
        if found is None:
            return httpx.Response(404, text="нет")
        status, body = found if isinstance(found, tuple) else (200, found)
        kind = "application/xml" if request.url.path.endswith(".xml") else "text/html"
        return httpx.Response(status, text=body, headers={"content-type": kind})


class Renderer:
    """Браузер-заглушка: адрес → HTML; считает открытия."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages
        self.opened: list[str] = []

    async def render(self, url: str) -> str | None:
        self.opened.append(url)
        return self.pages.get(url)


async def _find(
    site: Site, *, deep: bool = False, **ladder: object
) -> tuple[LadderResult, ContactLadder]:
    async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
        stairs = (DeepLadder if deep else ContactLadder)(http, **ladder)  # type: ignore[arg-type]
        return await stairs.find(HOST), stairs


class TestUnreadSite:
    """Сайт не прочитан — «адреса нет» с причиной, а не молча."""

    pytestmark = pytest.mark.asyncio

    async def test_challenge_page_is_not_the_home_page(self) -> None:
        result, ladder = await _find(Site({"/": (202, AWS_WAF)}))

        assert result.status is ContactStatus.NOT_FOUND
        assert result.reason == CHALLENGE
        assert result.pages_read == 0
        assert ladder.counters.pages_blocked == 1

    async def test_challenge_sends_the_site_to_the_browser(self) -> None:
        """Проверку «не робот» браузер иногда проходит — обычный проход его зовёт."""
        renderer = Renderer({f"https://{HOST}/": WITH_ADDRESS})
        result, ladder = await _find(Site({"/": (202, AWS_WAF)}), renderer=renderer)

        assert result.status is ContactStatus.FOUND
        assert ladder.counters.browser_entered == 1

    async def test_browser_that_sees_the_challenge_too_reads_nothing(self) -> None:
        renderer = Renderer({f"https://{HOST}/": AWS_WAF})
        result, _ = await _find(Site({"/": (202, AWS_WAF)}), renderer=renderer)

        assert result.status is ContactStatus.NOT_FOUND
        assert result.reason == CHALLENGE

    async def test_forbidden_is_named_with_its_code(self) -> None:
        result, _ = await _find(Site({"/": (403, "<html><body>Forbidden</body></html>")}))

        assert result.status is ContactStatus.NOT_FOUND
        assert result.reason == "сайт не пустил нас (403)"

    async def test_forbidden_challenge_is_named_a_challenge(self) -> None:
        """Cloudflare отвечает на проверку кодом 403 и страницей «Just a moment»: причина —
        проверка, а не голый код, и сайт уходит к браузеру."""
        renderer = Renderer({f"https://{HOST}/": WITH_ADDRESS})
        result, ladder = await _find(Site({"/": (403, CLOUDFLARE)}), renderer=renderer)

        assert result.status is ContactStatus.FOUND
        assert ladder.counters.pages_blocked == 1
        assert ladder.counters.browser_entered == 1

    async def test_closed_for_the_country_does_not_call_the_browser(self) -> None:
        """451 — закрыт для страны сервера: браузер с того же адреса его не откроет."""
        renderer = Renderer({f"https://{HOST}/": WITH_ADDRESS})
        result, ladder = await _find(Site({"/": (451, "<p>недоступно</p>")}), renderer=renderer)

        assert result.status is ContactStatus.NOT_FOUND
        assert result.reason == "сайт закрыт для страны нашего сервера (451)"
        assert ladder.counters.browser_entered == 0
        assert renderer.opened == []

    async def test_read_site_without_address_has_no_reason(self) -> None:
        result, _ = await _find(Site({"/": EMPTY}))

        assert result.status is ContactStatus.NOT_FOUND
        assert result.reason == ""
        assert result.pages_read >= 1

    async def test_domain_without_mail_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def no_mail(_host: str, **_kwargs: object) -> mx.MailRoute:
            return mx.MailRoute.NONE

        monkeypatch.setattr("backend.features.contacts.ladder.mail_route", no_mail)
        result, _ = await _find(Site({"/": WITH_ADDRESS}))

        assert result.status is ContactStatus.NOT_FOUND
        assert result.reason == NO_MAIL_REASON


class TestChallengeRecognition:
    def test_real_page_that_mentions_the_protection_is_a_page(self) -> None:
        """Подвал «защищено DDoS-Guard» у настоящей главной — не проверка: в ней ссылки."""
        links = "".join(f'<a href="/p{n}">статья {n}</a>' for n in range(20))
        page = f"<html><body>{links}<footer>Protected by DDoS-Guard</footer></body></html>"

        assert not is_challenge(page)

    def test_big_page_with_a_cloudflare_script_is_a_page(self) -> None:
        """Cloudflare вставляет скрипт проверки и в обычные страницы — по нему не судим."""
        page = (
            "<html><body><script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'></script>"
            + "<p>текст</p>" * 10_000
            + "</body></html>"
        )

        assert not is_challenge(page)

    def test_challenge_pages_are_recognized(self) -> None:
        assert is_challenge(AWS_WAF)
        assert is_challenge(CLOUDFLARE)


class TestFormAddress:
    pytestmark = pytest.mark.asyncio

    async def test_contact_page_form_wins_over_a_home_page_form(self) -> None:
        home = (
            '<html><body><a href="/contact/">Контакты</a><form><textarea name="msg"></textarea>'
            "</form></body></html>"
        )
        result, _ = await _find(Site({"/": home, "/contact/": CONTACT_FORM}))

        assert result.status is ContactStatus.FORM_ONLY
        assert result.form_url == f"https://{HOST}/contact/"

    async def test_found_address_keeps_the_form_too(self) -> None:
        home = '<html><body><a href="/contact/">Контакты</a></body></html>'
        contact = CONTACT_FORM.replace("</form>", "</form><p>editor@news.example.test</p>")
        result, _ = await _find(Site({"/": home, "/contact/": contact}))

        assert result.status is ContactStatus.FOUND
        assert result.has_form
        assert result.form_url == f"https://{HOST}/contact/"

    async def test_overlong_form_address_is_not_kept(self) -> None:
        """Адрес страницы длиннее разумного — это трекинг, а не страница: форма есть,
        ссылки на неё нет."""
        tail = "x" * 1100
        home = f'<html><body><a href="/p/42/?ref={tail}">Контакты</a></body></html>'
        result, _ = await _find(Site({"/": home, "/p/42/": CONTACT_FORM}))

        assert result.status is ContactStatus.FORM_ONLY
        assert result.form_url is None


#: Раздел, на который главная не ссылается и чей адрес не угадать слагом.
HIDDEN_SECTION = "/info/contact-the-team/"
SITEMAP = f"""<?xml version="1.0"?><urlset>
<url><loc>https://{HOST}/2024/05/how-to-contact-your-bank-without-waiting/</loc></url>
<url><loc>https://{HOST}{HIDDEN_SECTION}</loc></url>
<url><loc>https://other.example.test/contact/</loc></url>
</urlset>"""


class TestDeeper:
    pytestmark = pytest.mark.asyncio

    async def test_sitemap_section_is_read_only_deeper(self) -> None:
        pages: dict[str, tuple[int, str] | str] = {
            "/": EMPTY,
            "/sitemap.xml": SITEMAP,
            HIDDEN_SECTION: WITH_ADDRESS,
        }
        usual, _ = await _find(Site(pages))
        deeper, ladder = await _find(Site(pages), deep=True)

        assert usual.status is ContactStatus.NOT_FOUND
        assert deeper.status is ContactStatus.FOUND
        assert deeper.contact is not None
        assert deeper.contact.page_url == f"https://{HOST}{HIDDEN_SECTION}"
        assert ladder.counters.sitemap_sections == 1

    async def test_sitemap_index_reads_the_pages_map_first(self) -> None:
        index = f"""<sitemapindex>
<sitemap><loc>https://{HOST}/post-sitemap.xml</loc></sitemap>
<sitemap><loc>https://{HOST}/category-sitemap.xml</loc></sitemap>
<sitemap><loc>https://{HOST}/page-sitemap.xml</loc></sitemap></sitemapindex>"""
        site = Site(
            {
                "/": EMPTY,
                "/sitemap_index.xml": index,
                "/page-sitemap.xml": SITEMAP,
                HIDDEN_SECTION: WITH_ADDRESS,
            }
        )
        result, _ = await _find(site, deep=True)

        assert result.status is ContactStatus.FOUND
        assert f"https://{HOST}/page-sitemap.xml" in site.requested
        assert f"https://{HOST}/category-sitemap.xml" not in site.requested

    async def test_deeper_budget_is_wider(self) -> None:
        site = Site({"/": EMPTY})
        _, usual = await _find(site)
        _, deeper = await _find(Site({"/": EMPTY}), deep=True)

        assert usual.counters.pages_fetched <= cfg.MAX_ATTEMPTS_PER_DOMAIN
        assert deeper.counters.pages_fetched > usual.counters.pages_fetched

    async def test_browser_opens_an_open_site_only_deeper(self) -> None:
        """Обычный проход не зовёт браузер к открытому сайту, глубже — зовёт."""
        js_home = {f"https://{HOST}/": WITH_ADDRESS}
        usual, usual_ladder = await _find(Site({"/": EMPTY}), renderer=Renderer(js_home))
        renderer = Renderer(js_home)
        deeper, ladder = await _find(Site({"/": EMPTY}), renderer=renderer, deep=True)

        assert usual.status is ContactStatus.NOT_FOUND
        assert usual_ladder.counters.browser_entered == 0
        assert deeper.status is ContactStatus.FOUND
        assert ladder.counters.browser_pages >= 1
        assert len(renderer.opened) <= cfg.DEEP_BROWSER_PAGES

    async def test_sitemap_requests_count_and_respect_the_ceiling(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Запрос к карте — запрос к сайту: в счёт итога и в потолок попыток (ревью
        20.10.2026). Карта на десяток дочерних не выходит за потолок."""
        monkeypatch.setattr(cfg, "DEEP_MAX_ATTEMPTS", 4)
        index = "<sitemapindex>" + "".join(
            f"<sitemap><loc>https://{HOST}/page-sitemap-{n}.xml</loc></sitemap>" for n in range(10)
        )
        site = Site({"/": EMPTY, "/sitemap.xml": index + "</sitemapindex>"})

        _, ladder = await _find(site, deep=True)

        assert len(site.requested) <= 4
        assert ladder.counters.pages_fetched == len(site.requested)

    async def test_page_redrawn_by_the_browser_is_read_once(self) -> None:
        """Главная, которую браузер перерисовал после обхода, — та же страница: итог глубже
        не удваивает прочитанное."""
        renderer = Renderer({f"https://{HOST}/": EMPTY})
        result, ladder = await _find(Site({"/": EMPTY}), renderer=renderer, deep=True)

        assert ladder.counters.browser_pages >= 1
        assert result.pages_read == 1

    async def test_deeper_stops_at_its_time_limit(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(cfg, "DEEP_TIMEOUT_SEC", 0.2)

        async def slow(request: httpx.Request) -> httpx.Response:
            await asyncio.sleep(2)
            return httpx.Response(200, text=EMPTY, headers={"content-type": "text/html"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as http:
            result = await DeepLadder(http).find(HOST)

        assert result.status is ContactStatus.NO_ANSWER
        assert "не уложился" in result.reason


class TestSectionPicking:
    def test_only_short_sections_of_the_site(self) -> None:
        urls = [
            f"https://{HOST}/about-us/",
            f"https://{HOST}/2024/05/how-to-contact-your-bank-without-waiting/",
            f"https://{HOST}/a/b/c/contact/",
            "https://other.example.test/contact/",
            f"https://{HOST}/",
            f"https://{HOST}/advertise/",
        ]
        picked = pick_sections(urls, HOST)

        assert picked == [
            (f"https://{HOST}/advertise/", PageKind.MONEY),
            (f"https://{HOST}/about-us/", PageKind.ABOUT),
        ]

    def test_own_site_is_one_name_in_both_spellings(self) -> None:
        """Карта пишет имя Юникодом, хост в базе бывает и так, и в punycode (ревью 20.10.2026)."""
        unicode, punycode = "пекарня-пример.рф", "xn----7sbocojrfofidd7r.xn--p1ai"

        assert pick_sections([f"https://{unicode}/kontakty/"], punycode) == [
            (f"https://{unicode}/kontakty/", PageKind.CONTACT)
        ]
        assert pick_sections([f"https://{punycode}/kontakty/"], unicode) == [
            (f"https://{punycode}/kontakty/", PageKind.CONTACT)
        ]
