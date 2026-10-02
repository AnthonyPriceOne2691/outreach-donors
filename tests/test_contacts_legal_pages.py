"""Адреса не для писем о размещении: юристы, регуляторы, образцы на странице.

Ревью e6 после выкатки #142 (02.10.2026). Повторный поиск по ложным адресам
дал у сайта о продажах адрес хорватского ведомства по защите данных — со
страницы политики конфиденциальности. В базе разработки и на проде нашлись
и другие: ящик отдела защиты данных на испанском, образцы `unknown@…`
и `beispiel@…` на домене email.com. Домены в тестах выдуманы: настоящие
домены выборки в публичный репозиторий не идут.
"""

from __future__ import annotations

import httpx
import pytest
from backend.features.contacts import mx
from backend.features.contacts.ladder import ContactLadder
from backend.features.contacts.quality import Candidate, foreign_on_legal_page, rejection_reason
from backend.features.core.domain import ContactSource, ContactStatus, PageKind


@pytest.fixture(autouse=True)
def _mx_is_fine(monkeypatch: pytest.MonkeyPatch) -> None:
    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)


class TestNotForLetters:
    @pytest.mark.parametrize(
        "email",
        [
            "data-protection@site.com",
            "data.protection@site.com",
            "datenschutz@zeitung.de",
            "protecciondedatos@versicherung.es",
            "copyright@site.com",
            "opt-out@site.com",
            "d.p.o@site.com",
        ],
    )
    def test_legal_departments_on_any_domain(self, email: str) -> None:
        """Отдел защиты данных, прав и жалоб цену за размещение не называет —
        на каком бы языке и с какими бы разделителями он ни был записан."""
        assert rejection_reason(email) is not None

    @pytest.mark.parametrize(
        "email",
        [
            "unknown@email.com",
            "beispiel@email.com",
            "max.mustermann@firma.de",
            "first_name@site.com",
        ],
    )
    def test_samples_from_the_page(self, email: str) -> None:
        assert rejection_reason(email) is not None

    @pytest.mark.parametrize(
        "email", ["editor@site.com", "anna.mueller@zeitung.de", "dpo-team-lead@site.com"]
    )
    def test_ordinary_addresses_stay(self, email: str) -> None:
        """Обратная сторона: разделители снимаются для сверки, а не для подстроки —
        слово отдела внутри другого имени адрес не хоронит."""
        assert rejection_reason(email) is None


def _legal(email: str, kind: PageKind = PageKind.LEGAL) -> Candidate:
    return Candidate(email, ContactSource.PAGE, kind, page_url="https://site.com/privacy/")


class TestLegalPages:
    def test_foreign_address_on_a_legal_page_is_not_the_editor(self) -> None:
        assert foreign_on_legal_page(_legal("office@dataagency.hr"), site_host="site.com")

    @pytest.mark.parametrize("email", ["owner@site.com", "owner@mail.site.com", "owner@gmail.com"])
    def test_own_domain_and_free_mail_stay(self, email: str) -> None:
        """У маленьких сайтов в политике стоит ящик владельца — на своём домене
        или на бесплатной почте."""
        assert foreign_on_legal_page(_legal(email), site_host="site.com") is None

    def test_operator_on_the_about_page_stays(self) -> None:
        """Impressum и «о нас» — не такие: там сторонний адрес — обычно
        оператор сайта, материнская компания."""
        about = _legal("info@verlag.example.de", kind=PageKind.ABOUT)
        assert foreign_on_legal_page(about, site_host="site.com") is None

    def test_paid_step_addresses_are_not_judged_by_page(self) -> None:
        paid = Candidate("info@other.example.org", ContactSource.PROVIDER, PageKind.LEGAL)
        assert foreign_on_legal_page(paid, site_host="site.com") is None


class Site:
    def __init__(self, pages: dict[str, str]) -> None:
        self.pages = pages

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = self.pages.get(request.url.path) if request.url.host == "site.com" else None
        if body is None:
            return httpx.Response(404, text="нет", request=request)
        return httpx.Response(200, html=body, request=request)


def _page(*emails: str) -> str:
    links = "".join(f'<a href="mailto:{email}">{email}</a>' for email in emails)
    return f"<html><body>{links}</body></html>"


#: Главная со ссылкой на политику — как у настоящего сайта: подвал.
HOME = '<html><body><footer><a href="/privacy-policy/">Privacy Policy</a></footer></body></html>'


class TestTheLadder:
    async def test_regulator_from_the_privacy_page_is_not_a_contact(self) -> None:
        site = Site({"/": HOME, "/privacy-policy/": _page("office@dataagency.hr")})
        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
            result = await ContactLadder(http).find("site.com")
        assert result.status is ContactStatus.NOT_FOUND
        assert [email for email, _ in result.rejected] == ["office@dataagency.hr"]
        assert "регулятор или юрист" in result.rejected[0][1]

    async def test_owner_mailbox_from_the_privacy_page_is_found(self) -> None:
        site = Site({"/": HOME, "/privacy-policy/": _page("owner@gmail.com")})
        async with httpx.AsyncClient(transport=httpx.MockTransport(site)) as http:
            result = await ContactLadder(http).find("site.com")
        assert result.contact is not None
        assert result.contact.email == "owner@gmail.com"
