"""Отметка проверки, с которой платный поиск отдаёт адрес (`provider.find_emails` →
`repository.save_addresses`): по ней адрес перед первым письмом повторно не проверяется.

Hunter отдаёт рядом с адресом поиска свою проверку — слово и день. Верим «существует» и
«принимает всё»; прочее — без отметки, адрес спросят перед письмом. Отметка ложится только
адресу без отметки: мёртвый она не воскрешает.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.config import contacts as contacts_cfg
from backend.features.contacts import address_check
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.preference import DEAD
from backend.features.contacts.provider import HunterProvider
from backend.features.contacts.quality import Candidate
from backend.features.contacts.repository import ContactRepository
from backend.features.core.domain import ContactSource, ContactStatus
from backend.features.core.models.donor import ContactModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

SITE = "site.example.test"
TODAY = datetime.now(UTC).date()
#: Ключ теста — выдуманный: сеть подменена, настоящего ключа тест не видит.
KEY = "k-test"


def _row(email: str, verification: Any) -> dict[str, Any]:
    return {"value": email, "confidence": 94, "verification": verification}


async def _found(rows: list[dict[str, Any]]) -> list[Candidate]:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"emails": rows}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        return await HunterProvider(http, api_key=KEY).find_emails(SITE)


@pytest.mark.parametrize(
    ("verification", "verified"),
    [
        ({"date": "2026-10-30", "status": "valid"}, ("valid", date(2026, 10, 30))),
        (
            {"date": "2026-10-30T08:15:00Z", "status": "Accept_All"},
            ("accept_all", date(2026, 10, 30)),
        ),
        ({"date": None, "status": None}, None),
        ({"date": "вчера", "status": "valid"}, None),
        ({"status": "valid"}, None),
        (None, None),
        ("valid", None),
    ],
)
async def test_the_search_brings_the_check_hunter_already_made(
    verification: Any, verified: tuple[str, date] | None
) -> None:
    [found] = await _found([_row(f"editor@{SITE}", verification)])

    assert found.verified == verified
    assert (found.email, found.confidence) == (f"editor@{SITE}", 94)


@pytest.mark.parametrize(
    ("word", "kept"),
    [("valid", "valid"), ("accept_all", "accept_all"), ("unknown", None), ("invalid", None)],
)
def test_only_two_words_of_the_search_become_a_mark(word: str, kept: str | None) -> None:
    found = Candidate(f"a@{SITE}", ContactSource.PROVIDER, confidence=90, verified=(word, TODAY))

    mark = address_check.found_mark(found)

    assert mark["verification_status"] == kept
    assert (mark["verified_at"] is None) == (kept is None)


async def _save(session: AsyncSession, found: Candidate) -> ContactModel:
    result = LadderResult(host=SITE, status=ContactStatus.FOUND, contact=found)
    await ContactRepository(session).save([result])
    contact = await session.scalar(select(ContactModel).where(ContactModel.email == found.email))
    assert contact is not None
    await session.refresh(contact)
    return contact


class TestSavedMark:
    async def test_a_checked_address_of_the_search_needs_no_call_before_the_letter(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(contacts_cfg, "VERIFY_TTL_DAYS", 90)
        await make_donor(session, SITE)
        checked_on = TODAY - timedelta(days=5)
        found = Candidate(
            f"editor@{SITE}", ContactSource.PROVIDER, confidence=94, verified=("valid", checked_on)
        )

        contact = await _save(session, found)

        assert contact.verification_status == "valid"
        assert contact.verified_at is not None
        assert contact.verified_at.date() == checked_on
        assert contact.verification_score is None, "порядок адресов домена находка не меняет"
        assert address_check.kind_of(contact, datetime.now(UTC)) is address_check.Kind.TRUSTED

    async def test_an_old_check_of_the_search_is_asked_again(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(contacts_cfg, "VERIFY_TTL_DAYS", 90)
        await make_donor(session, SITE)
        long_ago = TODAY - timedelta(days=200)
        found = Candidate(f"editor@{SITE}", ContactSource.PROVIDER, verified=("valid", long_ago))

        contact = await _save(session, found)

        assert address_check.kind_of(contact, datetime.now(UTC)) is None

    async def test_an_address_found_on_a_page_has_no_mark(self, session: AsyncSession) -> None:
        await make_donor(session, SITE)

        contact = await _save(session, Candidate(f"info@{SITE}", ContactSource.PAGE))

        assert (contact.verification_status, contact.verified_at) == (None, None)

    async def test_the_mark_of_the_search_does_not_raise_a_dead_address(
        self, session: AsyncSession
    ) -> None:
        await make_donor(session, SITE, email=f"editor@{SITE}")
        dead = await session.scalar(
            select(ContactModel).where(ContactModel.email == f"editor@{SITE}")
        )
        assert dead is not None
        dead.verification_status, dead.verification_score = DEAD, 0
        await session.flush()
        found = Candidate(f"editor@{SITE}", ContactSource.PROVIDER, verified=("valid", TODAY))

        contact = await _save(session, found)

        assert (contact.verification_status, contact.verification_score) == (DEAD, 0)
        assert contact.verified_at is None
        assert contact.source is ContactSource.PROVIDER, "источник обновляется, как и раньше"
