"""Лестница контактов: маршрут, а не только результат.

Главная проверка здесь — счёт вызовов. Если переставить ступени местами,
адрес всё равно найдётся, и проверка результата ничего не заметит; заметно
станет только по счёту обращений к платной ступени, а это деньги.
"""

from __future__ import annotations

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.ladder import ContactLadder
from backend.features.contacts.provider import (
    Candidate,
    ProviderQuotaError,
    ProviderRateLimitError,
    Quota,
)
from backend.features.core.domain import ContactSource, ContactStatus, PageKind

pytestmark = pytest.mark.asyncio

HOME = """
<html><body>
  <a href="/write-for-us/">Write for us</a>
  <a href="/contact/">Contact</a>
  <p>О сайте</p>
</body></html>
"""
MONEY_PAGE = '<html><body><a href="mailto:ads@site.com">напишите</a></body></html>'
CONTACT_PAGE = '<html><body><a href="mailto:info@site.com">напишите</a></body></html>'
FORM_PAGE = '<html><body><form action="/send"><input name="email"></form></body></html>'
EMPTY_PAGE = "<html><body>адреса нет</body></html>"


@pytest.fixture(autouse=True)
def _mx_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    """По умолчанию домен принимает почту: ступень 0 проверяется отдельно."""

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


class Site:
    """Сайт-заглушка: отдаёт заданные страницы и считает запросы.

    Всё, чего нет в карте, отвечает 404 — как настоящий сайт на половину
    угадываемых слагов.
    """

    def __init__(self, pages: dict[str, str], *, rdap: object | None = None) -> None:
        self.pages = pages
        self.rdap = rdap
        self.requested: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requested.append(url)

        if "rdap.org" in url:
            if self.rdap is None:
                return httpx.Response(404, json={"errorCode": 404})
            return httpx.Response(200, json=self.rdap)

        path = request.url.path
        body = self.pages.get(path)
        if body is None:
            return httpx.Response(404, text="нет такой страницы")
        return httpx.Response(200, text=body, headers={"content-type": "text/html"})

    @property
    def pages_requested(self) -> list[str]:
        return [u for u in self.requested if "rdap.org" not in u]


class FakeProvider:
    """Платный сервис. Считает обращения — по ним видно, за что платим."""

    name = "fake"

    def __init__(self, emails: list[str] | None = None, error: Exception | None = None) -> None:
        self.emails = emails or []
        self.error = error
        self.calls: list[str] = []

    async def quota(self) -> Quota:
        return Quota(used=0, available=100)

    async def find_emails(self, host: str) -> list[Candidate]:
        self.calls.append(host)
        if self.error is not None:
            raise self.error
        return [
            Candidate(email=e, source=ContactSource.PROVIDER, confidence=90) for e in self.emails
        ]


def _client(site: Site) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(site))


class TestRoute:
    async def test_paid_step_is_not_reached_when_pages_answer(self) -> None:
        """Ради этого выбран порядок: за домен, который нашёлся сам, не платим."""
        site = Site({"/": HOME, "/write-for-us/": MONEY_PAGE})
        provider = FakeProvider(["paid@site.com"])

        async with _client(site) as http:
            ladder = ContactLadder(http, provider=provider)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.FOUND
        assert result.contact is not None
        assert result.contact.email == "ads@site.com"
        assert provider.calls == []  # платная ступень домена не видела
        assert "rdap.org" not in " ".join(site.requested)  # и RDAP тоже
        assert ladder.counters.provider_entered == 0
        assert ladder.counters.pages_found == 1

    async def test_paid_step_sees_only_the_remainder(self) -> None:
        site = Site({"/": EMPTY_PAGE})
        provider = FakeProvider(["editor@site.com"])

        async with _client(site) as http:
            ladder = ContactLadder(http, provider=provider)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.FOUND
        assert result.source is ContactSource.PROVIDER
        assert provider.calls == ["site.com"]
        assert ladder.counters.provider_found == 1

    async def test_rdap_before_the_paid_step(self) -> None:
        rdap_body = {
            "entities": [
                {
                    "roles": ["registrant"],
                    "vcardArray": ["vcard", [["email", {}, "text", "owner@site.com"]]],
                }
            ]
        }
        site = Site({"/": EMPTY_PAGE}, rdap=rdap_body)
        provider = FakeProvider(["paid@site.com"])

        async with _client(site) as http:
            ladder = ContactLadder(http, provider=provider)
            result = await ladder.find("site.com")

        assert result.contact is not None
        assert result.contact.email == "owner@site.com"
        assert provider.calls == []

    async def test_no_mail_stops_everything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Домен не принимает почту — ни страниц, ни RDAP, ни денег."""

        async def no_mail(_host: str, **_kwargs: object) -> mx.MailRoute:
            return mx.MailRoute.NONE

        monkeypatch.setattr("backend.features.contacts.ladder.mail_route", no_mail)
        site = Site({"/": HOME, "/contact/": CONTACT_PAGE})
        provider = FakeProvider(["paid@site.com"])

        async with _client(site) as http:
            ladder = ContactLadder(http, provider=provider)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.NOT_FOUND
        assert site.requested == []
        assert provider.calls == []
        assert ladder.counters.mx_stopped == 1

    async def test_dns_silence_is_not_a_refusal(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Отсутствие данных — не отказ: домен идёт дальше по лестнице."""

        async def unknown(_host: str, **_kwargs: object) -> mx.MailRoute:
            return mx.MailRoute.UNKNOWN

        monkeypatch.setattr("backend.features.contacts.ladder.mail_route", unknown)
        site = Site({"/": HOME, "/contact/": CONTACT_PAGE})

        async with _client(site) as http:
            result = await ContactLadder(http).find("site.com")

        assert result.status is ContactStatus.FOUND


class TestPageOrder:
    async def test_money_page_wins_over_contact_page(self) -> None:
        """Со страницы «write for us» адрес ведёт к тому, кто называет цену."""
        site = Site({"/": HOME, "/write-for-us/": MONEY_PAGE, "/contact/": CONTACT_PAGE})

        async with _client(site) as http:
            result = await ContactLadder(http).find("site.com")

        assert result.contact is not None
        assert result.contact.email == "ads@site.com"
        assert result.contact.page_kind is PageKind.MONEY

    async def test_page_budget_is_respected(self) -> None:
        site = Site({"/": EMPTY_PAGE})

        async with _client(site) as http:
            ladder = ContactLadder(http)
            await ladder.find("site.com")

        assert ladder.counters.pages_fetched <= 6


class TestOutcomes:
    async def test_form_without_address_goes_to_the_manual_queue(self) -> None:
        site = Site({"/": FORM_PAGE})

        async with _client(site) as http:
            ladder = ContactLadder(http, manual_queue_left=2)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.FORM_ONLY
        assert ladder.manual_queue_left == 1
        assert ladder.counters.manual_queued == 1

    async def test_manual_queue_cap_holds(self) -> None:
        """Сверх потолка домен всё равно form_only, но руки на него не тратим."""
        site = Site({"/": FORM_PAGE})

        async with _client(site) as http:
            ladder = ContactLadder(http, manual_queue_left=0)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.FORM_ONLY
        assert ladder.counters.manual_queued == 0
        assert ladder.counters.form_only == 1

    async def test_nothing_anywhere_is_not_found(self) -> None:
        site = Site({"/": EMPTY_PAGE})

        async with _client(site) as http:
            ladder = ContactLadder(http)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.NOT_FOUND
        assert ladder.counters.not_found == 1

    @pytest.mark.parametrize(
        ("error", "status"),
        [
            (ProviderQuotaError("кончилась"), ContactStatus.NO_QUOTA),
            (ProviderRateLimitError("частота"), ContactStatus.RATE_LIMITED),
        ],
    )
    async def test_paid_failure_is_not_absence_of_contact(
        self, error: Exception, status: ContactStatus
    ) -> None:
        """Слить «не заплатили» в «контакта нет» значит похоронить домен."""
        site = Site({"/": EMPTY_PAGE})
        provider = FakeProvider(error=error)

        async with _client(site) as http:
            result = await ContactLadder(http, provider=provider).find("site.com")

        assert result.status is status
        assert result.status is not ContactStatus.NOT_FOUND

    async def test_ladder_works_without_a_paid_provider(self) -> None:
        """Отладочный режим: три бесплатные ступени и честный not_found."""
        site = Site({"/": EMPTY_PAGE})

        async with _client(site) as http:
            ladder = ContactLadder(http, provider=None)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.NOT_FOUND
        assert ladder.counters.provider_entered == 0

    async def test_rejected_addresses_are_counted_with_reasons(self) -> None:
        """Отсев виден: без причин фильтр не настроить."""
        page = "<html><body><p>noreply@site.com и hello@example.com</p></body></html>"
        site = Site({"/": page})

        async with _client(site) as http:
            ladder = ContactLadder(http)
            result = await ladder.find("site.com")

        assert result.status is ContactStatus.NOT_FOUND
        reasons = dict(result.rejected)
        assert "не принимает ответов" in reasons["noreply@site.com"]
        assert "домен сервиса" in reasons["hello@example.com"]
        assert ladder.counters.rejected_emails == 2


class TestCounters:
    async def test_report_has_every_step(self) -> None:
        site = Site({"/": HOME, "/write-for-us/": MONEY_PAGE})

        async with _client(site) as http:
            ladder = ContactLadder(http)
            await ladder.find("site.com")

        report = ladder.counters.as_report()
        for key in ("mx_checked", "pages_entered", "pages_found", "rdap_entered", "not_found"):
            assert key in report
        assert report["mx_checked"] == 1
        assert report["pages_found"] == 1
        assert report["rdap_entered"] == 0
