"""«Искать глубже» и «Форма и письмо» через веб-слой: права, отказы словами, журнал.

Очередь задач подменена: проверяется, что в неё кладут и чем отвечают, а не rq.
Карточка и строки списка доноров говорят, можно ли искать глубже, тем же правилом,
что маршрут (`contacts/deeper.deeper_rule`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.config import contacts as contacts_cfg
from backend.features.contacts.deeper import JOB_TIMEOUT_SEC
from backend.features.core.domain import (
    AuditAction,
    ContactSource,
    ContactStatus,
    DonorStatus,
    Stage,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.form_letter import FormLetterModel
from backend.shared.queue import DEEPER_JOB, FORM_LETTER_JOB, JOB_TIMEOUT
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

pytestmark = pytest.mark.asyncio

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

SEARCHED = datetime.now(UTC) - timedelta(days=2)
FORM_PAGE = "https://formsite.example.test/contact/"


class Enqueued:
    """Подставная постановка «одной задачи за раз»: что поставили и под каким номером."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, tuple[Any, ...]]] = []
        self.options: list[dict[str, Any]] = []
        self.busy = False

    def __call__(self, _jobs: object, job_id: str, path: str, *args: Any, **options: Any) -> object:
        if self.busy:
            return None
        self.calls.append((job_id, path, args))
        self.options.append(options)
        return type("Job", (), {"id": job_id})()


@pytest.fixture
def enqueued(monkeypatch: pytest.MonkeyPatch) -> Enqueued:
    fake = Enqueued()
    monkeypatch.setattr("backend.api.contacts.deeper.enqueue_once", fake)
    monkeypatch.setattr("backend.api.contacts.deeper.contacts_queue", lambda: "contacts")
    monkeypatch.setattr("backend.api.contacts.form_letter.enqueue_once", fake)
    monkeypatch.setattr("backend.api.contacts.form_letter.quick_queue", lambda: "quick")
    monkeypatch.setattr("backend.api.contacts.form_letter.job_alive", lambda _id: False)
    return fake


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("оператор@site.example.test", role=UserRole.OPERATOR)
    return bearer(await sign_in("оператор@site.example.test"))


@pytest.fixture
async def viewer(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("зритель@site.example.test", role=UserRole.OPERATOR, permissions={"run": False})
    return bearer(await sign_in("зритель@site.example.test"))


async def _donor(
    session: AsyncSession,
    host: str,
    status: ContactStatus | None = ContactStatus.NOT_FOUND,
    *,
    form_url: str | None = None,
) -> DonorModel:
    domain = DomainModel(host=host, contact_form_url=form_url)
    session.add(domain)
    await session.flush()
    donor = DonorModel(
        domain_id=domain.id,
        status=DonorStatus.SUITABLE,
        dr=40,
        review="accepted",
        contact_status=status,
        contact_attempted_at=SEARCHED if status else None,
        contact_reason="сайт не пустил нас (403)" if status else None,
    )
    session.add(donor)
    await session.commit()
    return donor


async def _journal(session: AsyncSession) -> list[AuditLogModel]:
    found = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.RUN_STARTED)
        .order_by(AuditLogModel.id)
    )
    return list(found)


class TestSearchDeeper:
    async def test_queued_once_and_written_to_the_journal(
        self,
        client: AsyncClient,
        session: AsyncSession,
        operator: dict[str, str],
        enqueued: Enqueued,
    ) -> None:
        donor = await _donor(session, "deeper.example.test")

        answer = await client.post(f"/api/contacts/donors/{donor.id}/deeper", headers=operator)

        assert answer.status_code == 200
        assert answer.json() == {"job_id": f"contacts-deeper-{donor.id}", "pending": 1}
        assert enqueued.calls == [(f"contacts-deeper-{donor.id}", DEEPER_JOB, (donor.id,))]
        # Свой потолок задачи — с запасом над сроком лестницы и короче часа очереди:
        # процесс очереди адресов один (ревью 20.10.2026).
        timeout = enqueued.options[0]["job_timeout"]
        assert timeout == JOB_TIMEOUT_SEC
        assert contacts_cfg.DEEP_TIMEOUT_SEC < timeout < JOB_TIMEOUT
        [entry] = await _journal(session)
        assert entry.target == f"donor:{donor.id}"
        assert entry.details == {
            "действие": "поиск адреса глубже",
            "донор": "deeper.example.test",
            "платная ступень": "не зовётся",
        }

    async def test_second_press_while_running_is_refused_in_words(
        self,
        client: AsyncClient,
        session: AsyncSession,
        operator: dict[str, str],
        enqueued: Enqueued,
    ) -> None:
        donor = await _donor(session, "deeper.example.test")
        enqueued.busy = True

        answer = await client.post(f"/api/contacts/donors/{donor.id}/deeper", headers=operator)

        assert answer.status_code == 409
        assert "уже идёт" in answer.json()["detail"]
        assert await _journal(session) == []

    async def test_refusal_is_the_rule_in_words(
        self,
        client: AsyncClient,
        session: AsyncSession,
        operator: dict[str, str],
        enqueued: Enqueued,
    ) -> None:
        donor = await _donor(session, "form.example.test", ContactStatus.FORM_ONLY)

        answer = await client.post(f"/api/contacts/donors/{donor.id}/deeper", headers=operator)

        assert answer.status_code == 409
        assert answer.json()["detail"] == "На сайте нашлась форма — донор в очереди «Формы»."
        assert enqueued.calls == []

    async def test_needs_the_run_permission(
        self, client: AsyncClient, session: AsyncSession, viewer: dict[str, str], enqueued: Enqueued
    ) -> None:
        donor = await _donor(session, "deeper.example.test")

        answer = await client.post(f"/api/contacts/donors/{donor.id}/deeper", headers=viewer)

        assert answer.status_code == 403
        assert enqueued.calls == []

    async def test_unknown_donor_is_not_found(
        self, client: AsyncClient, operator: dict[str, str], enqueued: Enqueued
    ) -> None:
        answer = await client.post("/api/contacts/donors/99999999999/deeper", headers=operator)

        assert answer.status_code == 404


class TestDonorScreens:
    async def test_card_says_why_and_whether_deeper(
        self, client: AsyncClient, session: AsyncSession, operator: dict[str, str]
    ) -> None:
        donor = await _donor(session, "deeper.example.test", form_url=FORM_PAGE)

        card = (await client.get(f"/api/donors/{donor.id}", headers=operator)).json()

        assert card["contact_reason"] == "сайт не пустил нас (403)"
        assert card["form_url"] == FORM_PAGE
        assert card["deeper"] == {"refusal": None, "at": None, "note": None}

    async def test_card_after_deeper_names_the_outcome(
        self, client: AsyncClient, session: AsyncSession, operator: dict[str, str]
    ) -> None:
        donor = await _donor(session, "deeper.example.test")
        donor.contact_deeper_at = datetime.now(UTC)
        donor.contact_deeper_note = "адреса нет; прочитано страниц 12"
        await session.commit()

        card = (await client.get(f"/api/donors/{donor.id}", headers=operator)).json()

        assert card["deeper"]["note"] == "адреса нет; прочитано страниц 12"
        assert card["deeper"]["refusal"].startswith("Глубже уже искали")

    async def test_rows_carry_the_flag(
        self, client: AsyncClient, session: AsyncSession, operator: dict[str, str]
    ) -> None:
        ready = await _donor(session, "ready.example.test")
        await _donor(session, "form.example.test", ContactStatus.FORM_ONLY)

        rows = (await client.get("/api/donors", headers=operator)).json()["rows"]

        flags = {row["host"]: row["deeper"] for row in rows}
        assert flags == {"ready.example.test": True, "form.example.test": False}
        assert ready.id in {row["id"] for row in rows}


class TestFormLetterRoutes:
    async def test_form_queue_rows_carry_the_form_address(
        self, client: AsyncClient, session: AsyncSession, operator: dict[str, str]
    ) -> None:
        await _donor(session, "formsite.example.test", ContactStatus.FORM_ONLY, form_url=FORM_PAGE)

        rows = (await client.get("/api/contacts/forms", headers=operator)).json()["rows"]

        assert [row["form_url"] for row in rows] == [FORM_PAGE]

    async def test_viewer_sees_the_form_and_no_letter_yet(
        self,
        client: AsyncClient,
        session: AsyncSession,
        viewer: dict[str, str],
        enqueued: Enqueued,
    ) -> None:
        donor = await _donor(
            session, "formsite.example.test", ContactStatus.FORM_ONLY, form_url=FORM_PAGE
        )

        view = (await client.get(f"/api/contacts/forms/{donor.id}/letter", headers=viewer)).json()

        assert view == {
            "donor_id": donor.id,
            "host": "formsite.example.test",
            "form_url": FORM_PAGE,
            "letter": None,
            "job_id": None,
        }

    async def test_building_is_a_job_once_and_in_the_journal(
        self,
        client: AsyncClient,
        session: AsyncSession,
        operator: dict[str, str],
        enqueued: Enqueued,
    ) -> None:
        donor = await _donor(session, "formsite.example.test", ContactStatus.FORM_ONLY)

        view = (
            await client.post(f"/api/contacts/forms/{donor.id}/letter", json={}, headers=operator)
        ).json()

        assert view["job_id"] == f"form-letter-{donor.id}"
        assert enqueued.calls == [
            (f"form-letter-{donor.id}", FORM_LETTER_JOB, (donor.id, "оператор@site.example.test"))
        ]
        [entry] = await _journal(session)
        assert entry.details is not None
        assert entry.details["действие"] == "текст письма для формы"

    async def test_stored_letter_is_given_without_a_model_call(
        self,
        client: AsyncClient,
        session: AsyncSession,
        operator: dict[str, str],
        enqueued: Enqueued,
    ) -> None:
        donor = await _donor(session, "formsite.example.test", ContactStatus.FORM_ONLY)
        session.add(
            FormLetterModel(
                domain_id=donor.domain_id,
                stage=Stage.DONORS.value,
                subject="Guest article on formsite.example.test",
                body="Hi,\n\n«ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО»",
                tokens=900,
                based_on="текст первого письма по умолчанию",
            )
        )
        await session.commit()

        view = (
            await client.post(f"/api/contacts/forms/{donor.id}/letter", json={}, headers=operator)
        ).json()
        again = (
            await client.post(
                f"/api/contacts/forms/{donor.id}/letter", json={"again": True}, headers=operator
            )
        ).json()

        assert view["job_id"] is None
        assert view["letter"]["subject"] == "Guest article on formsite.example.test"
        assert view["letter"]["unset"] == ["ИМЯ ОТПРАВИТЕЛЯ НЕ ЗАДАНО"]
        assert again["job_id"] == f"form-letter-{donor.id}"
        assert len(enqueued.calls) == 1

    async def test_donor_outside_the_form_queue_is_refused(
        self,
        client: AsyncClient,
        session: AsyncSession,
        operator: dict[str, str],
        enqueued: Enqueued,
    ) -> None:
        donor = await _donor(session, "deeper.example.test")
        session.add(
            ContactModel(
                domain_id=donor.domain_id, email="a@deeper.example.test", source=ContactSource.PAGE
            )
        )
        await session.commit()

        answer = await client.post(
            f"/api/contacts/forms/{donor.id}/letter", json={}, headers=operator
        )

        assert answer.status_code == 409
        assert "нет в ручной очереди" in answer.json()["detail"]
        assert enqueued.calls == []

    async def test_building_needs_the_run_permission(
        self,
        client: AsyncClient,
        session: AsyncSession,
        viewer: dict[str, str],
        enqueued: Enqueued,
    ) -> None:
        donor = await _donor(session, "formsite.example.test", ContactStatus.FORM_ONLY)

        answer = await client.post(
            f"/api/contacts/forms/{donor.id}/letter", json={}, headers=viewer
        )

        assert answer.status_code == 403
