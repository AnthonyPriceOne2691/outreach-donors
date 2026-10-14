"""Экраны, без которых нужен инженер: контакты, ручные формы, выгрузка.

Приёмка веб-слоя требует, чтобы сотрудник доводил донора до цены,
ни разу не обратившись к инженеру. До этого среза между «прогон нашёл
доноров» и «собрать письма» стояла консольная команда.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.features.contacts import forms, manual
from backend.features.core.domain import (
    AuditAction,
    ContactSource,
    ContactStatus,
    DonorStatus,
    RunStatus,
    Stage,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.run import RunCandidateModel
from backend.features.donors import probe
from backend.features.donors.browse import DonorBrowser, DonorFilters
from backend.features.donors.export import to_csv
from backend.features.ops.overview import overview, work
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.replies import redirect
from backend.features.replies.repository import ReplyRepository
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]
#: Как у донора появился адрес без рук человека.
Arrival = Callable[[AsyncSession, DonorModel], Awaitable[None]]
NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, job: str, *args: Any, **_: Any) -> object:
        self.calls.append((job, args))
        return type("Job", (), {"id": "job-контакты"})()


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


@pytest.fixture
def queue(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.contacts.routes.contacts_queue", lambda: fake)
    monkeypatch.setattr("backend.api.contacts.routes.remember_contacts_job", lambda _: None)
    monkeypatch.setattr("backend.api.contacts.routes.contacts_job_id", lambda: None)
    monkeypatch.setattr("backend.api.contacts.routes.workers_alive", lambda **_: 1)
    return fake


@pytest.fixture
async def waiting(session: AsyncSession) -> DonorModel:
    """Донор, которому нужен контакт."""
    domain = await make_donor(session, "waiting.example.test", dr=40)
    return (
        (await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id)))
        .scalars()
        .one()
    )


@pytest.fixture
async def with_form(session: AsyncSession) -> DonorModel:
    """Донор с формой вместо адреса — строка ручной очереди."""
    domain = await make_donor(session, "form.example.test", dr=55)
    donor = (
        (await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id)))
        .scalars()
        .one()
    )
    donor.contact_status = ContactStatus.FORM_ONLY
    donor.contact_attempted_at = NOW
    await session.flush()
    return donor


class TestContactsFromTheScreen:
    async def test_state_says_how_many_wait(
        self, client: AsyncClient, admin_token: str, waiting: DonorModel, queue: FakeQueue
    ) -> None:
        response = await client.get("/api/contacts", headers=bearer(admin_token))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["pending"] == 1
        assert body["running"] is False

    async def test_search_goes_to_the_queue(
        self, client: AsyncClient, admin_token: str, waiting: DonorModel, queue: FakeQueue
    ) -> None:
        """Сотня доменов идёт минутами: держать запрос всё это время
        значит потерять работу, если человек закрыл вкладку."""
        response = await client.post(
            "/api/contacts", json={"limit": 50}, headers=bearer(admin_token)
        )

        assert response.status_code == 200, response.text
        assert queue.calls == [("backend.workers.jobs.find_contacts", (50, False, False))]

    async def test_without_the_right_nobody_searches(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        waiting: DonorModel,
        queue: FakeQueue,
    ) -> None:
        """Лестница доходит до платной ступени — это трата, как прогон."""
        await make_user("гость@site.com", role=UserRole.OPERATOR, permissions={"run": False})
        token = await sign_in("гость@site.com")

        response = await client.post("/api/contacts", json={}, headers=bearer(token))

        assert response.status_code == 403
        assert queue.calls == []


class TestTheFormQueue:
    async def test_queue_shows_the_strongest_first(
        self, session: AsyncSession, with_form: DonorModel
    ) -> None:
        rows = await forms.queue(session)

        assert [row.host for row in rows] == ["form.example.test"]
        assert rows[0].dr == 55

    async def test_filled_form_makes_an_ordinary_donor(
        self, client: AsyncClient, admin_token: str, session: AsyncSession, with_form: DonorModel
    ) -> None:
        response = await client.post(
            f"/api/contacts/forms/{with_form.id}/filled",
            json={"email": "Editor@Form.example.test"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200, response.text
        contact = (
            (
                await session.execute(
                    select(ContactModel).where(ContactModel.domain_id == with_form.domain_id)
                )
            )
            .scalars()
            .one()
        )
        assert contact.email == "editor@form.example.test"
        assert contact.source is ContactSource.MANUAL
        await session.refresh(with_form)
        assert with_form.contact_status is ContactStatus.FOUND

    async def test_the_author_of_the_address_is_written_down(
        self, client: AsyncClient, admin_token: str, session: AsyncSession, with_form: DonorModel
    ) -> None:
        """«Откуда у нас этот адрес» спросит либо донор, либо юрист."""
        await client.post(
            f"/api/contacts/forms/{with_form.id}/filled",
            json={"email": "editor@form.example.test"},
            headers=bearer(admin_token),
        )

        entry = (
            (
                await session.execute(
                    select(AuditLogModel).where(AuditLogModel.action == AuditAction.CONTACT_ADDED)
                )
            )
            .scalars()
            .one()
        )
        assert entry.user_id is not None
        assert entry.details is not None
        assert entry.details["адрес"] == "editor@form.example.test"

    async def test_giving_up_takes_the_donor_out_of_the_queue(
        self, client: AsyncClient, admin_token: str, session: AsyncSession, with_form: DonorModel
    ) -> None:
        """Висеть в очереди вечно он не должен: срок годности вернёт его,
        если что-то изменится."""
        response = await client.post(
            f"/api/contacts/forms/{with_form.id}/give-up",
            json={"reason": "форма требует регистрации"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200, response.text
        await session.refresh(with_form)
        assert with_form.contact_status is ContactStatus.NOT_FOUND
        assert await forms.queue(session) == []

    async def test_stranger_donor_is_refused(
        self, client: AsyncClient, admin_token: str, waiting: DonorModel
    ) -> None:
        response = await client.post(
            f"/api/contacts/forms/{waiting.id}/filled",
            json={"email": "a@b.test"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 409
        assert "нет в ручной очереди" in response.json()["detail"]

    async def test_monthly_cap_counts_hands_not_a_stored_number(
        self, session: AsyncSession, with_form: DonorModel
    ) -> None:
        """Счётчик, который некому обнулять, однажды застревает —
        этот урок в сервисе уже оплачен дневным лимитом ящиков."""
        before = await forms.monthly_left(session)

        await forms.filled(session, with_form.id, email="editor@form.example.test")
        await session.flush()

        assert await forms.monthly_left(session) == before - 1


async def _form_only(session: AsyncSession, host: str, *, review: str | None) -> DonorModel:
    """Запись с формой вместо адреса и решением человека `review`."""
    domain = await make_donor(session, host, dr=90, review=review)
    donor = (
        (await session.execute(select(DonorModel).where(DonorModel.domain_id == domain.id)))
        .scalars()
        .one()
    )
    donor.contact_status = ContactStatus.FORM_ONLY
    donor.contact_attempted_at = NOW
    await session.flush()
    return donor


class TestOnlyDonorsWaitForHands:
    """Проверка прода 10.10.2026: три из пяти строк «Форм» были кандидатами, которых
    человек не принимал, — первыми, по DR; меню и главная звали их «Формы 5» рядом
    с «2 с формой». Очередь, её число и месячный потолок — только доноры."""

    @pytest.fixture
    async def candidate(self, session: AsyncSession) -> DonorModel:
        """Кандидат, ждущий решения в очереди прогона, — с формой и сильнее донора."""
        repository = RunRepository(session)
        settings = await repository.create_settings(
            defaults(),
            geo_top_n=5,
            geo_min_share=0.2,
            metrics_ttl_days=90,
            price_ttl_days=150,
            units_cap=100_000,
        )
        run = await repository.create_run(
            stage=Stage.DONORS,
            settings_id=settings.id,
            keywords=["garden blog"],
            country="us",
            status=RunStatus.DONE,
        )
        donor = await _form_only(session, "candidate.example.test", review=None)
        session.add(RunCandidateModel(run_id=run.id, domain_id=donor.domain_id, status="pending"))
        await session.flush()
        return donor

    async def test_candidate_with_a_form_is_neither_in_the_queue_nor_in_the_count(
        self,
        client: AsyncClient,
        admin_token: str,
        session: AsyncSession,
        candidate: DonorModel,
        with_form: DonorModel,
    ) -> None:
        await _form_only(session, "rejected.example.test", review="rejected")
        await session.commit()

        response = await client.get("/api/contacts/forms", headers=bearer(admin_token))

        assert [row["host"] for row in response.json()["rows"]] == ["form.example.test"]
        assert response.json()["total"] == await forms.total(session) == 1
        # Меню, плитка «Заполнить формы» и «с формой» под «С адресом» — то же число.
        view = await overview(session, stages=EVERY_STAGE)
        assert (
            (await work(session, stages=EVERY_STAGE)).forms
            == view.waiting.forms
            == view.donors.form_only
            == 1
        )

    async def test_candidate_form_is_not_filled_from_the_queue(
        self, client: AsyncClient, admin_token: str, session: AsyncSession, candidate: DonorModel
    ) -> None:
        """Заполнить форму кандидату — работа до решения «берём ли»: отказ словами,
        адреса нет, потолок месяца цел."""
        before = await forms.monthly_left(session)
        await session.commit()

        response = await client.post(
            f"/api/contacts/forms/{candidate.id}/filled",
            json={"email": "editor@candidate.example.test"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 409
        assert "нет в ручной очереди" in response.json()["detail"]
        assert await forms.monthly_left(session) == before

    async def test_donor_with_an_address_does_not_wait_for_the_form(
        self, session: AsyncSession, with_form: DonorModel
    ) -> None:
        """Форма — вместо адреса. Адрес в базе есть (донор ответил с другого ящика,
        повторный поиск записал «только форма» рядом с прежним адресом) — заполнять
        нечего; на главной «и N с формой» не пересекается с «С адресом»."""
        session.add(
            ContactModel(
                domain_id=with_form.domain_id,
                email="editor@form.example.test",
                source=ContactSource.PAGE,
            )
        )
        await session.flush()

        assert await forms.queue(session) == []
        view = await overview(session, stages=EVERY_STAGE)
        assert (await forms.total(session), view.donors.form_only) == (0, 0)
        assert view.donors.with_email == 1

    async def test_monthly_cap_is_spent_only_on_donors(
        self, session: AsyncSession, candidate: DonorModel, with_form: DonorModel
    ) -> None:
        """Адрес, вписанный кандидату с карточки, и адрес, с которого ответил сайт без
        записи донора (рекламодатель, лид), — не формы доноров: потолок их не считает."""
        before = await forms.monthly_left(session)
        await manual.add(session, candidate, "editor@candidate.example.test")
        stranger = DomainModel(host="brand.example.test")
        session.add(stranger)
        await session.flush()
        session.add(
            ContactModel(
                domain_id=stranger.id, email="ceo@brand.example.test", source=ContactSource.MANUAL
            )
        )
        await session.flush()

        assert await forms.monthly_left(session) == before

        await forms.filled(session, with_form.id, email="editor@form.example.test")
        await session.flush()

        assert await forms.monthly_left(session) == before - 1


async def _answered_from(session: AsyncSession, donor: DonorModel) -> None:
    """Донор ответил с другого ящика (`replies/repository.py`)."""
    await ReplyRepository(session).remember_answering_address(
        domain_id=donor.domain_id, email="editor@form.example.test", now=NOW
    )


async def _named_by_an_autoreply(session: AsyncSession, donor: DonorModel) -> None:
    """Мёртвый ящик донора назвал, куда писать дальше, — строка такая, какой её пишет
    `redirect.follow`. Самим приёмом адрес на `*.example.test` ушёл бы человеку: зоны
    `.test` нет в списке суффиксов, и «тот же сайт» не узнаётся (`redirect.same_site`)."""
    session.add(
        ContactModel(
            domain_id=donor.domain_id,
            email="editor@form.example.test",
            source=ContactSource.MANUAL,
            verification_status=redirect.FROM_AUTOREPLY,
            verification_score=manual.MANUAL_SCORE,
        )
    )


async def _probe_inbox(session: AsyncSession, donor: DonorModel) -> None:
    """Свой ящик проверяющего — адресом липового донора (`donors/probe.py`)."""
    domain = await session.get(DomainModel, donor.domain_id)
    assert domain is not None
    await probe.own_address(session, domain, "inbox@probe.example.test")


class TestTheGuidelineCountsHands:
    """Решение Anthony 11.10.2026: ориентир «Форм» считает только адреса, вписанные
    руками, — на «Формах» и в карточке донора. Источник `manual` у адреса бывает и без
    рук, и до правки ориентир тратился на них."""

    @pytest.mark.parametrize(
        "arrived",
        [_answered_from, _named_by_an_autoreply, _probe_inbox],
        ids=["ответ донора", "автоответ мёртвого ящика", "ящик проверки"],
    )
    async def test_address_that_came_without_hands_is_not_counted(
        self, session: AsyncSession, with_form: DonorModel, arrived: Arrival
    ) -> None:
        before = await forms.monthly_left(session)

        await arrived(session, with_form)
        await session.flush()

        assert await forms.monthly_left(session) == before

    async def test_address_typed_on_the_donor_card_is_counted(
        self, client: AsyncClient, admin_token: str, session: AsyncSession, with_form: DonorModel
    ) -> None:
        before = await forms.monthly_left(session)
        await session.commit()

        response = await client.post(
            f"/api/contacts/donors/{with_form.id}/addresses",
            json={"email": "editor@form.example.test"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200, response.text
        assert await forms.monthly_left(session) == before - 1


class TestExport:
    def test_header_is_the_screen_order(self) -> None:
        body = to_csv([]).decode("utf-8-sig")

        header = body.splitlines()[0].split(";")
        assert header[:4] == ["домен", "вердикт", "причина отсева", "DR"]
        # Новая колонка — сразу за прежними: они не сдвигаются.
        assert header[header.index("метрики от") + 1] == "почему не проверен"

    def test_excel_gets_what_it_needs(self) -> None:
        """Точка с запятой и метка порядка байтов — уступки Excel:
        без них он показывает одну колонку и кракозябры."""
        body = to_csv([])

        assert body.startswith(b"\xef\xbb\xbf")
        assert b";" in body

    async def test_rows_match_the_filter(
        self, client: AsyncClient, admin_token: str, session: AsyncSession
    ) -> None:
        await make_donor(session, "good.example.test", dr=40)
        weak = await make_donor(session, "weak.example.test", dr=5)
        donor = (
            (await session.execute(select(DonorModel).where(DonorModel.domain_id == weak.id)))
            .scalars()
            .one()
        )
        donor.status = DonorStatus.UNSUITABLE
        await session.commit()

        response = await client.get(
            "/api/donors/export", params={"status": "suitable"}, headers=bearer(admin_token)
        )

        assert response.status_code == 200, response.text
        assert response.headers["content-type"].startswith("text/csv")
        body = response.content.decode("utf-8-sig")
        assert "good.example.test" in body
        assert "weak.example.test" not in body

    async def test_export_is_the_same_rows_as_the_screen(self, session: AsyncSession) -> None:
        await make_donor(session, "one.example.test", dr=40)
        page = await DonorBrowser(session).page(DonorFilters(limit=100, offset=0))

        body = to_csv(page.rows).decode("utf-8-sig")

        assert "one.example.test" in body
        assert len(body.strip().splitlines()) == len(page.rows) + 1
