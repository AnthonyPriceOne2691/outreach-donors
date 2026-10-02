"""Заново найти адрес для названных доменов (`contacts-refind`).

Ложный адрес из голого «at» (#137) общий поиск не перепроверит: домен
с найденным адресом он не берёт, а письмо уходит на старший адрес домена.
Команда идёт тем же поиском, без `--yes` только показывает, а при записи
снимает прежние адреса лестницы — кроме тех, по которым уже есть переписка.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime

import httpx
import pytest
from backend.cli import contact_refind
from backend.features.contacts import mx
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.provider import ProviderQuotaError, Quota
from backend.features.contacts.quality import Candidate
from backend.features.contacts.refind import NamedDomains, Refound
from backend.features.contacts.search import SearchReport, search_contacts
from backend.features.core.domain import ContactSource, ContactStatus, MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

CONTACT_PAGE = '<html><body><a href="mailto:info@site.com">почта</a></body></html>'


class Paid:
    """Платная ступень: считает обращения; `error` — чем отказать."""

    name = "fake"

    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[str] = []

    async def quota(self) -> Quota:
        return Quota(used=0, available=100)

    async def find_emails(self, host: str) -> list[Candidate]:
        self.calls.append(host)
        if self.error is not None:
            raise self.error
        return []


@pytest.fixture
def paid() -> Paid:
    return Paid()


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch, paid: Paid) -> dict[str, str]:
    """Сайт site.com: путь → страница; остальное 404. Сеть, MX и платная — подменены."""
    pages: dict[str, str] = {"/": "<html><body>адреса нет</body></html>"}

    def site(request: httpx.Request) -> httpx.Response:
        body = pages.get(request.url.path) if request.url.host == "site.com" else None
        if body is None:
            return httpx.Response(404, text="нет", request=request)
        return httpx.Response(200, html=body, request=request)

    async def route(_host: str, **_kwargs: object) -> mx.MailRoute:
        return mx.MailRoute.MX

    async def paid_step(_http: httpx.AsyncClient, _report: object) -> Paid:
        return paid

    monkeypatch.setattr("backend.features.contacts.ladder.mail_route", route)
    monkeypatch.setattr("backend.features.contacts.search._paid_step", paid_step)
    monkeypatch.setattr(
        "backend.features.contacts.search.guarded_client",
        lambda **_kw: httpx.AsyncClient(transport=httpx.MockTransport(site)),
    )
    return pages


async def _false_address(session: AsyncSession, host: str = "site.com") -> DomainModel:
    """Донор с ложным адресом, найденным лестницей."""
    domain = await make_donor(session, host)
    session.add(ContactModel(domain_id=domain.id, email="here@site.com", source=ContactSource.PAGE))
    await session.execute(
        update(DonorModel)
        .where(DonorModel.domain_id == domain.id)
        .values(contact_status=ContactStatus.FOUND, contact_attempted_at=datetime.now(UTC))
    )
    await session.flush()
    return domain


async def _addresses(session: AsyncSession, domain: DomainModel) -> list[str]:
    rows = await session.execute(
        select(ContactModel.email)
        .where(ContactModel.domain_id == domain.id)
        .order_by(ContactModel.id)
    )
    return list(rows.scalars().all())


async def _refind(session: AsyncSession, *hosts: str, write: bool) -> NamedDomains:
    queue = NamedDomains(session, hosts, write=write)
    await search_contacts(session, limit=len(hosts), no_paid=not write, queue=queue)
    return queue


class TestShowAndWrite:
    async def test_show_writes_nothing_and_does_not_pay(
        self, session: AsyncSession, web: dict[str, str], paid: Paid
    ) -> None:
        web["/contact/"] = CONTACT_PAGE
        domain = await _false_address(session)

        queue = await _refind(session, "https://www.site.com/", write=False)

        found = queue.domains["site.com"]
        assert found.before == ["here@site.com"]
        assert found.result is not None
        assert found.result.contact is not None
        assert found.result.contact.email == "info@site.com"
        assert found.removed == ["here@site.com"]
        assert await _addresses(session, domain) == ["here@site.com"]
        assert paid.calls == []

    async def test_write_replaces_the_false_address(
        self, session: AsyncSession, web: dict[str, str]
    ) -> None:
        web["/contact/"] = CONTACT_PAGE
        domain = await _false_address(session)

        await _refind(session, "site.com", write=True)

        assert await _addresses(session, domain) == ["info@site.com"]

    async def test_write_pays_like_the_general_search(
        self, session: AsyncSession, web: dict[str, str], paid: Paid
    ) -> None:
        """Адреса на сайте нет — платная ступень спрашивается, как в общем поиске;
        ложный адрес снимается, исход — «адреса нет»."""
        domain = await _false_address(session)

        await _refind(session, "site.com", write=True)

        assert paid.calls == ["site.com"]
        assert await _addresses(session, domain) == []
        donor = (
            await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id))
        ).scalar_one()
        assert donor.contact_status is ContactStatus.NOT_FOUND


class TestWhatStays:
    async def test_address_with_letters_stays(
        self, session: AsyncSession, web: dict[str, str]
    ) -> None:
        web["/contact/"] = CONTACT_PAGE
        domain = await _false_address(session)
        await session.execute(
            update(ContactModel)
            .where(ContactModel.domain_id == domain.id)
            .values(last_contacted_at=datetime.now(UTC))
        )

        queue = await _refind(session, "site.com", write=True)

        assert queue.domains["site.com"].kept[0][0] == "here@site.com"
        assert await _addresses(session, domain) == ["here@site.com", "info@site.com"]

    async def test_letters_to_a_kept_address_are_named(
        self, session: AsyncSession, web: dict[str, str]
    ) -> None:
        """Письмо, уже собранное на ложный адрес, уйдёт, когда снимут
        предохранитель: команда называет его и говорит, где остановить."""
        web["/contact/"] = CONTACT_PAGE
        domain = await _false_address(session)
        (contact_id,) = (
            await session.execute(
                select(ContactModel.id).where(ContactModel.domain_id == domain.id)
            )
        ).scalars()
        campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
        session.add(campaign)
        await session.flush()
        for number, status in enumerate((MessageStatus.QUEUED, MessageStatus.SENT)):
            session.add(
                MessageModel(
                    campaign_id=campaign.id,
                    domain_id=domain.id,
                    contact_id=contact_id,
                    status=status,
                    idempotency_key=f"test:refind:{number}",
                )
            )
        await session.flush()

        queue = await _refind(session, "site.com", write=False)

        (kept,) = queue.domains["site.com"].kept
        assert kept[0] == "here@site.com"
        assert "В очереди писем: 1, ушло: 1." in kept[1]
        assert "остановить на экране «Письма»" in kept[1]

    async def test_manual_address_skips_the_domain(
        self, session: AsyncSession, web: dict[str, str]
    ) -> None:
        """Вписанный руками адрес лучше всего, что найдёт лестница, — как в общем поиске."""
        domain = await _false_address(session)
        session.add(
            ContactModel(domain_id=domain.id, email="owner@site.com", source=ContactSource.MANUAL)
        )
        await session.flush()

        queue = await _refind(session, "site.com", write=True)

        assert queue.domains["site.com"].skipped.startswith("адрес вписан человеком")
        assert await _addresses(session, domain) == ["here@site.com", "owner@site.com"]

    async def test_quota_is_not_an_answer(
        self, session: AsyncSession, web: dict[str, str], paid: Paid
    ) -> None:
        """Квота платной ступени — «не спросили»: прежний адрес не снимается."""
        paid.error = ProviderQuotaError("квота кончилась")
        domain = await _false_address(session)

        queue = await _refind(session, "site.com", write=True)

        assert queue.domains["site.com"].removed == []
        assert await _addresses(session, domain) == ["here@site.com"]

    async def test_unknown_and_foreign_domains_are_named(
        self, session: AsyncSession, web: dict[str, str]
    ) -> None:
        session.add(DomainModel(host="lonely.com"))
        await session.flush()

        queue = await _refind(session, "nowhere.com", "lonely.com", "n/a", write=False)

        assert queue.domains["nowhere.com"].skipped == "домена нет в базе"
        assert queue.domains["lonely.com"].skipped == "домен в базе не донор и не рекламодатель"
        assert queue.domains["n/a"].skipped == "не похоже на домен"


class TestTheCommand:
    def test_a_domain_is_printed_before_and_after(self, capsys: pytest.CaptureFixture[str]) -> None:
        found = Refound(host="site.com", before=["here@site.com"], removed=["here@site.com"])
        found.kept.append(("old@site.com", "По этому адресу уже есть письма"))
        contact_refind._print_domain(found, write=False)
        said = capsys.readouterr().out
        assert "было:      here@site.com" in said
        assert "поиск не дошёл до домена" in said

    async def test_show_says_it_wrote_nothing(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def search(_session: object, **kwargs: object) -> SearchReport:
            assert kwargs["no_paid"] is True, "показ не должен платить"
            return SearchReport()

        monkeypatch.setattr(contact_refind, "search_contacts", search)
        monkeypatch.setattr(contact_refind, "check_storage", lambda: None)
        args = argparse.Namespace(hosts=["n/a"], yes=False, browser=False)

        assert await contact_refind.cmd_contacts_refind(args) == 0

        said = capsys.readouterr().out
        assert "пропущен: не похоже на домен" in said
        assert "Это показ — в базе ничего не изменилось" in said

    def test_a_found_address_and_its_page_are_printed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        contact = Candidate(
            "info@site.com", ContactSource.PAGE, page_url="https://site.com/contact/"
        )
        result = LadderResult(host="site.com", status=ContactStatus.FOUND, contact=contact)
        found = Refound(
            host="site.com", before=["here@site.com"], result=result, removed=["here@site.com"]
        )
        contact_refind._print_domain(found, write=True)
        said = capsys.readouterr().out
        assert "станет:    info@site.com (страница: https://site.com/contact/)" in said
        assert "снят:   here@site.com" in said

    async def test_write_says_what_was_written(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def search(_session: object, **kwargs: object) -> SearchReport:
            assert kwargs["no_paid"] is False, "запись платит, как общий поиск"
            return SearchReport(walked=1, saved=1)

        monkeypatch.setattr(contact_refind, "search_contacts", search)
        monkeypatch.setattr(contact_refind, "check_storage", lambda: None)
        args = argparse.Namespace(hosts=["site.com"], yes=True, browser=False)

        assert await contact_refind.cmd_contacts_refind(args) == 0

        assert "Записано: доменов 1, с адресом 1." in capsys.readouterr().out


class TestLastTry:
    async def test_the_queue_knows_the_last_try(self, session: AsyncSession) -> None:
        """Очередь команды — очередь общего поиска: последний шанс сайту
        ответить она называет так же (`attempts.last_tries`)."""
        domain = await make_donor(session, "site.com")
        await session.execute(
            update(DonorModel)
            .where(DonorModel.domain_id == domain.id)
            .values(
                contact_status=ContactStatus.NO_ANSWER,
                contact_tries=3,
                contact_attempted_at=datetime.now(UTC),
            )
        )
        queue = NamedDomains(session, ["site.com"], write=False)
        assert await queue.last_tries(["site.com"]) == {"site.com"}
