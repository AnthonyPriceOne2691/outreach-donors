"""Страница проверки «вы не робот» — только страница, с которой нечего взять.

Ревью 20.10.2026: признаки служб защиты есть и на обычных страницах — скрипт Imperva,
SDK AWS WAF, `cf-chl-` у виджета Turnstile при форме, «verify you are human» у поля
формы, «защищено DDoS-Guard» в подвале. По ним одним маленький сайт с адресом читался бы
закрытым и получал «адреса нет: сайт показал проверку». Поэтому проверка — страница без
адреса, без формы и почти без текста, и только с признаком самой страницы проверки или
с ответом, каким служба отвечает на проверку (202, 403, 429, 503), и признаком службы.

Сайты — синтетические, сеть не трогается.
"""

from __future__ import annotations

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.ladder import ContactLadder, LadderResult
from backend.features.contacts.pages import CHALLENGE, is_challenge
from backend.features.core.domain import ContactStatus

HOST = "site.example.test"
OWN = f"hello@{HOST}"
NAV = '<nav><a href="/">Главная</a> <a href="/about">О нас</a></nav>'

#: Настоящие маленькие страницы со скриптом или словами службы защиты — и с адресом.
REAL = {
    "скрипт Imperva": (
        '<html><head><script src="/_Incapsula_Resource?SWJIYLWA=719d34d3"></script></head>'
        f"<body><h1>Студия</h1><p>Пишите: {OWN}</p></body></html>"
    ),
    "капча у формы": (
        f"<html><body><h1>Студия</h1><p>Почта: {OWN}</p><form><input name='email'>"
        "<textarea></textarea><p>Please verify you are human</p></form></body></html>"
    ),
    "DDoS-Guard в подвале": (
        f"<html><body><p>{OWN}</p><footer>Protected by DDoS-Guard</footer></body></html>"
    ),
    "SDK AWS WAF": (
        '<html><head><script src="https://abc123.edge.sdk.awswaf.com/abc123/jsapi.js"></script>'
        f"<script>AwsWafIntegration.getToken()</script></head><body><p>{OWN}</p></body></html>"
    ),
    "Turnstile у формы контактов": (
        f"<html><body>{NAV}<h1>Контакты</h1><p>{OWN}</p><form><textarea></textarea>"
        '<div class="cf-turnstile"><iframe id="cf-chl-widget-a1b2c" '
        'src="https://challenges.cloudflare.com/x"></iframe></div></form></body></html>'
    ),
    "каркас SPA за Imperva, адрес в JSON-LD": (
        '<html><head><script type="application/ld+json">'
        f'{{"@type":"Organization","email":"{OWN}"}}</script>'
        '<script src="/_Incapsula_Resource?SWJIYLWA=1"></script></head>'
        '<body><div id="root"></div></body></html>'
    ),
}

#: Страницы самих проверок: код ответа и тело.
CHALLENGES = {
    "Cloudflare «Just a moment», 403": (
        403,
        "<html><head><title>Just a moment...</title></head><body><noscript>Enable "
        "JavaScript and cookies to continue</noscript>"
        "<script>window._cf_chl_opt={cvId:'3'}</script></body></html>",
    ),
    "Cloudflare, 503": (
        503,
        "<html><head><title>Just a moment...</title></head><body>Checking if the site "
        "connection is secure</body></html>",
    ),
    "AWS WAF, 202": (
        202,
        '<html><head><script>window.gokuProps = {"key":"x"};</script>'
        '<script src="https://abc.token.awswaf.com/abc/challenge.js"></script></head>'
        '<body><div id="challenge-container"></div></body></html>',
    ),
    "Imperva, отказ": (
        200,
        "<html><body>Request unsuccessful. Incapsula incident ID: 123-456</body></html>",
    ),
    "служба защиты с кодом проверки": (
        202,
        "<html><head><script>AwsWafIntegration.checkForceRefresh()</script></head>"
        "<body></body></html>",
    ),
}


@pytest.fixture(autouse=True)
def _mail_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


async def _find(status: int, home: str) -> LadderResult:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in ("", "/"):
            return httpx.Response(status, text=home, headers={"content-type": "text/html"})
        return httpx.Response(404, text="нет")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await ContactLadder(http).find(HOST)


@pytest.mark.parametrize("name", list(REAL), ids=list(REAL))
def test_real_page_with_a_protection_script_is_a_page(name: str) -> None:
    assert not is_challenge(REAL[name])
    assert not is_challenge(REAL[name], 403)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(REAL), ids=list(REAL))
async def test_address_on_such_a_page_is_found(name: str) -> None:
    result = await _find(200, REAL[name])

    assert result.status is ContactStatus.FOUND
    assert result.contact is not None
    assert result.contact.email == OWN


@pytest.mark.parametrize("name", list(CHALLENGES), ids=list(CHALLENGES))
def test_challenge_pages_are_recognized(name: str) -> None:
    status, html = CHALLENGES[name]

    assert is_challenge(html, status)


@pytest.mark.asyncio
@pytest.mark.parametrize("name", list(CHALLENGES), ids=list(CHALLENGES))
async def test_challenge_is_the_reason_of_no_address(name: str) -> None:
    status, html = CHALLENGES[name]

    result = await _find(status, html)

    assert result.status is ContactStatus.NOT_FOUND
    assert result.reason == CHALLENGE
    assert result.pages_read == 0


def test_protection_script_on_an_empty_page_with_an_ordinary_answer_is_a_page() -> None:
    """Пустая страница со скриптом службы и ответом 200 — пустая страница, а не проверка:
    по коду служба на проверку так не отвечает, а признака самой проверки нет."""
    html = CHALLENGES["служба защиты с кодом проверки"][1]

    assert not is_challenge(html, 200)


def test_long_page_with_the_challenge_title_is_a_page() -> None:
    """Заголовок «Just a moment» у статьи с текстом — статья: с неё есть что взять."""
    html = "<html><head><title>Just a moment of calm</title></head><body>"
    html += "<p>Длинная статья о тишине и покое в большом городе.</p>" * 40 + "</body></html>"

    assert not is_challenge(html, 200)
