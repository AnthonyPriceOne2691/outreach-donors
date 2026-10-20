"""Лестница адреса сбора: главная с `mailto:` на своём домене, у домена есть MX — адрес находится.

Разбор прогона сбора: у части сайтов с исходом «адреса нет» на главной есть `mailto:` на своём
домене, и у домена есть MX. Синтетика проверяет, что лестница (общий `contacts/*`, вход
`search_contacts(queue=…)` — тот же, что у прохода прогона) такой адрес находит: ссылкой как есть,
с темой письма, заглавными, процентной кодировкой, мнемониками и пробелами вокруг; главные — разных
ниш, рынков и письменностей. И что ступень MX решает: домен без почты адреса не даёт.

Домены — разных зон и письменностей, IDN — в punycode, как их хранит база: лестница общая для
любого домена, правил под зону, нишу или язык в ней нет. Домен IDN, записанный в `mailto:`
Юникодом, и адрес в подвале главной длиннее предела страницы она находит с правки общего кода
(`contacts/*`, «Формы» и «Искать глубже»): до неё оба теста стояли `xfail`.

Сайты — `httpx.MockTransport`, DNS — подменой ступени MX. Домены выдуманы.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.ladder import ContactLadder
from backend.features.contacts.search import search_contacts
from backend.features.core.domain import ContactStatus
from backend.features.core.models.donor import ContactModel
from backend.features.sales import cleaning
from backend.features.sales.segment_leads import make_leads
from backend.features.sales.segment_models import Lane
from backend.features.sales.segment_queue import SegmentContactQueue
from backend.features.sales.verifier import FixtureVerifier
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_rejudge import site
from tests.test_sales_segment_run import hypothesis, make_run

#: Главные разных ниш, зон и письменностей; адрес — ссылкой `mailto:` на своём домене. Домены IDN —
#: punycode: bäckerei-beispiel.de, 茶舗-サンプル.jp, مكتبة-مثال.امارات, стоматология-пример.рф.
SITES = {
    "xn--bckerei-beispiel-vnb.de": "<h1>Bäckerei am Markt</h1><p>Frisches Brot täglich.</p>",
    "xn----ifuwe8csby996gnwb.jp": "<h1>日本茶の専門店</h1><p>静岡から直送します。</p>",
    "xn----zmcbedj7noabf.xn--mgbaam7a8h": "<h1 dir='rtl'>مكتبة الحي</h1><p dir='rtl'>كتب جديدة</p>",
    "xn----7sbjjnbrggmcbqodbrd6w.xn--p1ai": "<h1>Стоматология на Садовой</h1><p>Запись.</p>",
    "vinhos-sample.com.br": "<h1>Vinhos do Douro</h1><p>Entregas em todo o país.</p>",
}

#: Как ссылка записана на главной → какой адрес лестница отдаёт.
LINKS = {
    "как есть": ('<a href="mailto:info@{host}">info@{host}</a>', "info@{host}"),
    "с темой письма": ('<a href="mailto:hello@{host}?subject=Anfrage&body=Hallo">✉</a>', "hello@{host}"),
    "заглавными": ('<a href="MAILTO:Office@{upper}">Почта</a>', "office@{host}"),
    "процентной кодировкой": ('<a href="mailto:kontakt%40{host}">E-Mail</a>', "kontakt@{host}"),
    "мнемониками": ('<a href="mailto:&#115;&#104;&#111;&#112;&#64;{host}">メール</a>', "shop@{host}"),
    "с пробелами": ('<a href="  mailto: contato@{host} ">contato</a>', "contato@{host}"),
}  # fmt: skip


def _home(host: str, link: str) -> str:
    text = SITES[host]
    return f"<html><body>{text}<footer>{link}</footer></body></html>"


class Homes:
    """Главная по хосту (`www.` — тот же сайт); остальное — 404."""

    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def __call__(self, request: httpx.Request) -> httpx.Response:
        # Хост — как он ушёл в запрос: IDN — в punycode (`raw_host`), как его хранит база.
        host = request.url.raw_host.decode("ascii").removeprefix("www.")
        if request.url.path in ("", "/") and host in self.pages:
            return httpx.Response(200, html=self.pages[host], request=request)
        return httpx.Response(404, text="нет", request=request)


@pytest.fixture(autouse=True)
def routes(monkeypatch: pytest.MonkeyPatch) -> dict[str, mx.MailRoute]:
    """У доменов есть MX, если тест не сказал иного."""
    routes: dict[str, mx.MailRoute] = {}

    async def route(host: str, **_kwargs: object) -> mx.MailRoute:
        return routes.get(host, mx.MailRoute.MX)

    async def no_paid(_http: httpx.AsyncClient, _report: object) -> None:
        return None

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    monkeypatch.setattr(cleaning, "mail_route", route)
    monkeypatch.setattr("backend.features.contacts.search.paid_step", no_paid)
    return routes


@pytest.mark.parametrize("host", sorted(SITES))
@pytest.mark.parametrize("written", sorted(LINKS))
async def test_a_mailto_on_its_own_domain_with_mx_is_found(host: str, written: str) -> None:
    link, expected = LINKS[written]
    page = _home(host, link.format(host=host, upper=host.upper()))
    async with httpx.AsyncClient(transport=httpx.MockTransport(Homes({host: page}))) as http:
        found = await ContactLadder(http).find(host)

    assert found.status is ContactStatus.FOUND, (written, found.rejected)
    assert found.contact is not None
    assert found.contact.email == expected.format(host=host)


async def test_a_domain_without_mail_gives_no_address(routes: dict[str, mx.MailRoute]) -> None:
    """Ступень MX решает: домен не принимает почту — адрес на нём отбился бы, лестница его не
    отдаёт. Значит, «адреса нет» у сайта с адресом на главной — не MX, если MX у домена есть."""
    host = "xn--bckerei-beispiel-vnb.de"
    routes[host] = mx.MailRoute.NONE
    page = _home(host, f'<a href="mailto:info@{host}">info</a>')
    async with httpx.AsyncClient(transport=httpx.MockTransport(Homes({host: page}))) as http:
        found = await ContactLadder(http).find(host)

    assert found.status is ContactStatus.NOT_FOUND


async def test_the_run_path_finds_it_and_makes_the_lead(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Тот же вход, что у прохода прогона: очередь кандидатов, поиск по пачке, адрес — в общие
    `contacts`, лид — из него."""
    pages = {host: _home(host, f'<a href="mailto:info@{host}">info@{host}</a>') for host in SITES}
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(Homes(pages))),
    )
    run = await make_run(session, await hypothesis(session))
    for host in SITES:
        await site(session, run, host, lane=Lane.ACCEPT)

    report = await search_contacts(
        session, limit=10, queue=SegmentContactQueue(session, run.id), no_paid=True
    )
    made: dict[str, Any] = await make_leads(session, run, FixtureVerifier())

    assert (report.pending, report.saved) == (len(SITES), len(SITES))
    saved = set((await session.scalars(select(ContactModel.email))).all())
    assert saved == {f"info@{host}" for host in SITES}
    assert made["created"] == len(SITES)


# --- находки для общего кода: закрыты правкой общего `contacts/*` ------------------------------------


async def test_a_mailto_with_an_idn_domain_written_in_unicode_is_found() -> None:
    host = "xn----7sbjjnbrggmcbqodbrd6w.xn--p1ai"
    page = _home(host, '<a href="mailto:info@стоматология-пример.рф">почта</a>')
    async with httpx.AsyncClient(transport=httpx.MockTransport(Homes({host: page}))) as http:
        found = await ContactLadder(http).find(host)

    assert found.status is ContactStatus.FOUND, found.rejected
    assert found.contact is not None
    assert found.contact.email == f"info@{host}"


async def test_a_mailto_in_the_footer_of_a_home_longer_than_the_page_limit_is_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("backend.config.contacts.MAX_PAGE_BYTES", 20_000)
    host = "vinhos-sample.com.br"
    catalogue = "<p>" + "Vinho tinto do Douro, safra 2019. " * 1_000 + "</p>"
    page = f"<html><body>{catalogue}<footer><a href='mailto:loja@{host}'>loja</a></footer></body></html>"
    async with httpx.AsyncClient(transport=httpx.MockTransport(Homes({host: page}))) as http:
        found = await ContactLadder(http).find(host)

    assert found.status is ContactStatus.FOUND
