"""Ручная очередь форм рекламодателей: «только форма, нет email — заполняем вручную».

Сверка ТЗ 11.10.2026: очередь была только у доноров, и рекламодатель с одной формой
не получал оффера никогда. Проверяется то, ради чего очередь: в ней — те, кому пишем;
вписанный руками адрес доходит до сборки оффера; ориентир темпа считает его, как
адрес донора, — один на обе очереди; число меню «Формы» видит обе очереди.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.config import contacts as contacts_cfg
from backend.features.contacts import forms, manual
from backend.features.core.domain import (
    AuditAction,
    ContactStatus,
    RunStatus,
    Stage,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.run import RunModel
from backend.features.donors.verdict import Thresholds
from backend.features.letters.niche_recipients import NicheRecipients
from backend.features.letters.recipients import Recipients
from backend.features.ops.overview import overview, work
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.runs.repository import RunRepository
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor
from tests.stage2_rows import addressed, promoted

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

DONOR = "donor.example.test"
FORM_ONLY = ContactStatus.FORM_ONLY

#: Маршруты очереди и право, без которого они отказывают.
FORM_ROUTES = [
    ("GET", "/api/advertisers/forms", None, "view"),
    ("POST", "/api/advertisers/forms/{id}/filled", {"email": "ads@x.example.test"}, "run"),
    ("POST", "/api/advertisers/forms/{id}/give-up", {"reason": None}, "run"),
]


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("формы-рекл@site.com", role=UserRole.OPERATOR)
    return await sign_in("формы-рекл@site.com")


@pytest.fixture
async def with_form(session: AsyncSession) -> AdvertiserModel:
    """Рекламодатель по ссылке на доноре со свежей ценой: лестница нашла одну форму."""
    donor = await make_donor(session, DONOR)
    row = await session.scalar(select(DonorModel).where(DonorModel.domain_id == donor.id))
    assert row is not None
    row.last_price, row.last_price_currency = Decimal("150"), "USD"
    row.last_price_at = datetime.now(UTC) - timedelta(days=5)
    advertiser = await promoted(session, "shop.example.test", contact_status=FORM_ONLY)
    advertiser.contact_attempted_at = datetime(2026, 10, 1, tzinfo=UTC)
    await session.flush()
    return advertiser


class TestWhoWaits:
    async def test_only_those_we_write_to_with_a_form_and_no_address(
        self, session: AsyncSession, with_form: AdvertiserModel
    ) -> None:
        """По ссылке — всякий; бизнес ниши — после «пишем»; адрес в базе — не ждёт."""
        await promoted(session, "strong.example.test", points=9, contact_status=FORM_ONLY)
        await promoted(session, "kept.example.test", contact_status=FORM_ONLY)
        await addressed(session, "kept.example.test")
        await promoted(
            session,
            "yes.example.test",
            source="niche",
            confirmed=True,
            decided=True,
            contact_status=FORM_ONLY,
        )
        await promoted(session, "waits.example.test", source="niche", contact_status=FORM_ONLY)
        await promoted(
            session, "no.example.test", source="niche", decided=True, contact_status=FORM_ONLY
        )
        await promoted(session, "none.example.test", contact_status=ContactStatus.NOT_FOUND)

        rows = await forms.advertiser_queue(session)

        assert [row.host for row in rows] == [
            "strong.example.test",
            "shop.example.test",
            "yes.example.test",
        ]
        assert await forms.advertiser_total(session) == 3
        niche = rows[-1]
        assert (niche.source, niche.donor_host) == ("niche", None)
        assert rows[1].donor_host == DONOR

    async def test_the_menu_counts_both_queues(
        self, session: AsyncSession, with_form: AdvertiserModel
    ) -> None:
        """Число у «Форм» и плитка «Заполнить формы» — обе очереди; «и N с формой» в
        воронке доноров — про доноров, и рекламодатели названы на главной отдельно."""
        donor = await make_donor(session, "form-donor.example.test")
        row = await session.scalar(select(DonorModel).where(DonorModel.domain_id == donor.id))
        assert row is not None
        row.contact_status = FORM_ONLY
        await session.flush()

        view = await overview(session, stages=EVERY_STAGE)

        assert (await work(session, stages=EVERY_STAGE)).forms == view.waiting.forms == 2
        assert view.waiting.advertiser_forms == 1
        assert view.donors.form_only == 1
        assert await forms.waiting(session) == 2


class TestTabs:
    async def test_each_tab_knows_the_other_count(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        with_form: AdvertiserModel,
    ) -> None:
        """Число соседней вкладки — в ответе своей, без второго запроса; ориентир
        темпа — один у обеих."""
        await session.commit()

        donors = (await client.get("/api/contacts/forms", headers=bearer(token))).json()
        advertisers = (await client.get("/api/advertisers/forms", headers=bearer(token))).json()

        assert (donors["total"], donors["advertisers_waiting"]) == (0, 1)
        assert (advertisers["total"], advertisers["donors_waiting"]) == (1, 0)
        assert advertisers["rows"][0]["host"] == "shop.example.test"
        assert advertisers["rows"][0]["donor_host"] == DONOR
        assert donors["monthly_left"] == advertisers["monthly_left"]
        assert advertisers["limit"] == forms.PAGE_SIZE
        # Через сколько дней «Не вышло» вернётся в поиск — подтверждение говорит это числом.
        assert donors["retry_days"] == advertisers["retry_days"] == contacts_cfg.CONTACT_TTL_DAYS


class TestFilled:
    async def test_the_address_reaches_the_offer(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        with_form: AdvertiserModel,
    ) -> None:
        """Вписанный руками адрес — строка в `contacts` домена с отметкой «руками»; сборка
        оффера берёт его, как найденный лестницей."""
        await session.commit()
        recipients = Recipients(session)
        assert await recipients.advertiser_candidates(limit=10) == []

        response = await client.post(
            f"/api/advertisers/forms/{with_form.id}/filled",
            json={"email": " Sales@Shop.example.test "},
            headers=bearer(token),
        )

        assert response.status_code == 200, response.text
        assert response.json()["host"] == "shop.example.test"
        await session.refresh(with_form)
        assert with_form.contact_status is ContactStatus.FOUND
        contact = await session.scalar(
            select(ContactModel).where(ContactModel.domain_id == with_form.domain_id)
        )
        assert contact is not None
        assert (contact.email, contact.verification_status) == (
            "sales@shop.example.test",
            manual.BY_HAND,
        )
        assert await forms.advertiser_queue(session) == []
        offers = await recipients.advertiser_candidates(limit=10)
        assert [(one.host, one.email) for one in offers] == [
            ("shop.example.test", "sales@shop.example.test")
        ]

    async def test_the_pace_guide_counts_it_like_a_donor_address(
        self, session: AsyncSession, with_form: AdvertiserModel
    ) -> None:
        """Ориентир темпа — про руки, а руки одни: адрес рекламодателя, вписанный с
        «Форм», тратит тот же ориентир, что адрес донора."""
        before = await forms.monthly_left(session)

        await forms.advertiser_filled(session, with_form.id, email="ads@shop.example.test")
        await session.flush()

        assert await forms.monthly_left(session) == before - 1

    async def test_the_journal_says_who_and_where_from(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        with_form: AdvertiserModel,
    ) -> None:
        await session.commit()

        await client.post(
            f"/api/advertisers/forms/{with_form.id}/filled",
            json={"email": "ads@shop.example.test"},
            headers=bearer(token),
        )

        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.CONTACT_ADDED)
        )
        assert entry is not None
        assert entry.target == f"advertiser:{with_form.id}"
        assert entry.details == {
            "рекламодатель": "shop.example.test",
            "адрес": "ads@shop.example.test",
            "откуда": "форма, заполнена руками",
        }

    async def test_a_bad_address_is_refused_in_words(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        with_form: AdvertiserModel,
    ) -> None:
        await session.commit()

        response = await client.post(
            f"/api/advertisers/forms/{with_form.id}/filled",
            json={"email": "not an address"},
            headers=bearer(token),
        )

        assert response.status_code == 400
        assert "не похож на адрес почты" in response.json()["detail"]
        assert await forms.advertiser_total(session) == 1


class TestNicheBusiness:
    async def test_the_address_reaches_the_niche_offer(self, session: AsyncSession) -> None:
        """Бизнес ниши после «пишем», у которого нашлась одна форма: вписанный адрес
        доходит до сборки его оффера — «разместим вас у таких-то» (`niche_recipients`)."""
        await make_donor(session, DONOR)
        donor = await session.scalar(
            select(DonorModel).join(DomainModel).where(DomainModel.host == DONOR)
        )
        assert donor is not None
        donor.last_price, donor.last_price_at = Decimal("150"), datetime.now(UTC)
        settings = await RunRepository(session).create_settings(
            Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300),
            geo_top_n=5,
            geo_min_share=0.2,
            metrics_ttl_days=90,
            price_ttl_days=150,
            units_cap=100_000,
        )
        run = RunModel(
            stage=Stage.DONORS,
            settings_id=settings.id,
            status=RunStatus.DONE,
            keywords=["sports betting"],
            country="de",
            candidates={"hosts": ["bookie.example.test", DONOR]},
        )
        session.add(run)
        await session.flush()
        business = await promoted(
            session,
            "bookie.example.test",
            source="niche",
            confirmed=True,
            decided=True,
            contact_status=FORM_ONLY,
        )
        business.found_run_id = run.id
        await session.flush()

        await forms.advertiser_filled(session, business.id, email="partners@bookie.example.test")
        await session.flush()

        offers = await NicheRecipients(session).niche_candidates(limit=10)
        assert [(one.host, one.email) for one in offers] == [
            ("bookie.example.test", "partners@bookie.example.test")
        ]
        assert offers[0].niche is not None
        assert offers[0].niche.example_host == DONOR


class TestGaveUp:
    async def test_the_advertiser_leaves_the_queue_without_an_address(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        with_form: AdvertiserModel,
    ) -> None:
        await session.commit()

        response = await client.post(
            f"/api/advertisers/forms/{with_form.id}/give-up",
            json={"reason": "форма требует регистрации"},
            headers=bearer(token),
        )

        assert response.status_code == 200, response.text
        await session.refresh(with_form)
        assert with_form.contact_status is ContactStatus.NOT_FOUND
        # Отметка попытки цела: срок годности от прошлого поиска вернёт его в поиск.
        assert with_form.contact_attempted_at == datetime(2026, 10, 1, tzinfo=UTC)
        assert await forms.advertiser_total(session) == 0

    async def test_the_journal_says_who_closed_it_and_why(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        with_form: AdvertiserModel,
    ) -> None:
        await session.commit()

        await client.post(
            f"/api/advertisers/forms/{with_form.id}/give-up",
            json={"reason": "форма требует регистрации"},
            headers=bearer(token),
        )

        entry = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.CONTACT_ADDED)
        )
        assert entry is not None
        assert entry.user_id is not None
        assert entry.target == f"advertiser:{with_form.id}"
        assert entry.details == {
            "рекламодатель": "shop.example.test",
            "исход": "форма не вышла",
            "почему": "форма требует регистрации",
        }


class TestRefusals:
    @pytest.mark.parametrize("number", ["999", "99999999999"])
    async def test_not_in_the_queue_is_a_state_not_a_crash(
        self, client: AsyncClient, token: str, number: str
    ) -> None:
        """Номер, которого нет, и номер больше столбца — «нет в очереди», а не пятисотка."""
        response = await client.post(
            f"/api/advertisers/forms/{number}/filled",
            json={"email": "a@b.example.test"},
            headers=bearer(token),
        )

        assert response.status_code == 409
        assert "нет в ручной очереди" in response.json()["detail"]

    async def test_a_niche_business_without_write_is_not_filled(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Бизнесу ниши форму заполняют после «пишем»: до решения — работа впустую."""
        waits = await promoted(
            session, "waits.example.test", source="niche", contact_status=FORM_ONLY
        )
        await session.commit()

        response = await client.post(
            f"/api/advertisers/forms/{waits.id}/filled",
            json={"email": "ads@waits.example.test"},
            headers=bearer(token),
        )

        assert response.status_code == 409


class TestWhoIsLetIn:
    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/advertisers/forms")
            for method in methods
        }

        assert in_app == {
            (method, path.replace("{id}", "{advertiser_id}")) for method, path, _, _ in FORM_ROUTES
        }

    @pytest.mark.parametrize(("method", "path", "body", "permission"), FORM_ROUTES)
    async def test_without_the_right_refused(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        with_form: AdvertiserModel,
        method: str,
        path: str,
        body: dict[str, object] | None,
        permission: str,
    ) -> None:
        await make_user("без-права@site.com", permissions={permission: False})
        refused = await sign_in("без-права@site.com")
        where = path.replace("{id}", str(with_form.id))

        assert (await client.request(method, where, json=body)).status_code == 403
        response = await client.request(method, where, json=body, headers=bearer(refused))
        assert response.status_code == 403

    @pytest.mark.parametrize(("method", "path", "body", "permission"), FORM_ROUTES)
    async def test_operator_does_the_work(
        self,
        client: AsyncClient,
        token: str,
        with_form: AdvertiserModel,
        session: AsyncSession,
        method: str,
        path: str,
        body: dict[str, object] | None,
        permission: str,
    ) -> None:
        await session.commit()

        response = await client.request(
            method, path.replace("{id}", str(with_form.id)), json=body, headers=bearer(token)
        )

        assert response.status_code == 200, response.text
