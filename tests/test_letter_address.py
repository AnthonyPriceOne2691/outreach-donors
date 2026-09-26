"""На какой адрес уйдёт письмо: карточка донора и сборка писем выбирают одно.

Порядок адресов живёт одним местом (`contacts/preference.py`): ответивший,
потом оценка проверки, потом старшая запись. Сборка берёт по нему один
адрес на донора среди тех, что вне стоп-листа, и перепроверяет его перед
письмом. Карточка отмечает адрес тем же запросом (`Recipients.letter_address`).

Проверяется настоящей сборкой очереди на настоящей базе: адрес письма,
легшего в очередь, — тот же, что отметила карточка. И наоборот: если сборка
письма не собирает, карточка не отмечает ничего и говорит почему.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.features.contacts import manual
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    Stage,
    SuppressionReason,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.rewrite import RewriteResult
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime.now(UTC)


class _Rewriter:
    """Модель, которая ничего не переписывает: адрес письма от неё не зависит."""

    async def rewrite(self, rendered: object, about: object) -> RewriteResult:
        return RewriteResult(zones={}, tokens_spent=0)


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


async def _donor(
    session: AsyncSession,
    host: str,
    addresses: list[dict[str, Any]],
    *,
    review: str | None = "accepted",
    status: DonorStatus = DonorStatus.SUITABLE,
) -> DonorModel:
    """Донор и его адреса в том порядке, в каком их записали (номера растут)."""
    domain = DomainModel(host=host)
    session.add(domain)
    await session.flush()
    donor = DonorModel(domain_id=domain.id, status=status, dr=40, review=review)
    session.add(donor)
    for fields in addresses:
        session.add(ContactModel(domain_id=domain.id, **{"source": ContactSource.PAGE, **fields}))
        await session.flush()
    await session.commit()
    return donor


async def _card(client: AsyncClient, token: str, donor: DonorModel) -> dict[str, Any]:
    response = await client.get(f"/api/donors/{donor.id}", headers=bearer(token))
    assert response.status_code == 200, response.text
    card: dict[str, Any] = response.json()
    return card


async def _built(session: AsyncSession, donor: DonorModel) -> int | None:
    """Адрес письма, которое настоящая сборка положила этому донору; `None` — не положила."""
    await QueueBuilder(session, _Rewriter()).build(  # type: ignore[arg-type]
        BuildRequest(campaign_name="Проверка адреса письма", limit=50)
    )
    return await session.scalar(
        select(MessageModel.contact_id).where(MessageModel.domain_id == donor.domain_id)
    )


def _email_of(card: dict[str, Any], contact_id: int | None) -> str | None:
    return next((c["email"] for c in card["contacts"] if c["id"] == contact_id), None)


#: Адреса донора → какой из них берут письмо и карточка.
CASES: dict[str, tuple[list[dict[str, Any]], str]] = {
    "вписанный руками сильнее найденного": (
        [
            {"email": "page@one.example.test"},
            {
                "email": "editor@one.example.test",
                "source": ContactSource.MANUAL,
                "verification_score": manual.MANUAL_SCORE,
            },
        ],
        "editor@one.example.test",
    ),
    "ответивший сильнее вписанного": (
        [
            {
                "email": "editor@one.example.test",
                "source": ContactSource.MANUAL,
                "verification_score": manual.MANUAL_SCORE,
            },
            {"email": "sales@one.example.test", "last_replied_at": NOW - timedelta(days=1)},
        ],
        "sales@one.example.test",
    ),
    "без оценок — старшая запись": (
        [{"email": "first@one.example.test"}, {"email": "second@one.example.test"}],
        "first@one.example.test",
    ),
    "один адрес": ([{"email": "only@one.example.test"}], "only@one.example.test"),
}


class TestSameAddress:
    @pytest.mark.parametrize("case", list(CASES))
    async def test_card_marks_the_address_the_letter_goes_to(
        self, client: AsyncClient, token: str, session: AsyncSession, case: str
    ) -> None:
        addresses, expected = CASES[case]
        donor = await _donor(session, "one.example.test", addresses)

        card = await _card(client, token, donor)
        built = await _built(session, donor)

        assert _email_of(card, card["letter_contact_id"]) == expected
        assert card["letter_contact_id"] == built
        assert card["letter_blocked"] is None
        # Карточка показывает адреса в том же порядке — отмеченный первым.
        assert card["contacts"][0]["email"] == expected

    async def test_suppressed_best_address_gives_way_to_the_next(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Лучший адрес в стоп-листе — письмо уйдёт на следующий, и карточка
        отмечает следующий, хотя первым в списке стоит лучший."""
        donor = await _donor(
            session,
            "one.example.test",
            [
                {"email": "page@one.example.test"},
                {
                    "email": "editor@one.example.test",
                    "source": ContactSource.MANUAL,
                    "verification_score": manual.MANUAL_SCORE,
                },
            ],
        )
        session.add(
            SuppressionModel(email="editor@one.example.test", reason=SuppressionReason.COMPLAINED)
        )
        await session.commit()

        card = await _card(client, token, donor)
        built = await _built(session, donor)

        assert _email_of(card, card["letter_contact_id"]) == "page@one.example.test"
        assert card["letter_contact_id"] == built


class TestNoLetter:
    async def test_address_failing_the_check_stops_the_letter(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Сохранённый давно адрес не проходит нынешнюю проверку: сборка
        пропускает донора, а не берёт следующий адрес, — и карточка не обещает
        письма ни на один."""
        donor = await _donor(
            session,
            "one.example.test",
            [
                {"email": "you@yourbusiness.com", "verification_score": 90},
                {"email": "a@one.example.test"},
            ],
        )

        card = await _card(client, token, donor)
        built = await _built(session, donor)

        assert built is None
        assert card["letter_contact_id"] is None
        assert "не проходит проверку" in card["letter_blocked"]

    @pytest.mark.parametrize(
        ("fields", "said"),
        [
            ({"review": None}, "письма уходят только донорам, принятым человеком"),
            ({"review": "rejected"}, "письма уходят только донорам, принятым человеком"),
            ({"status": DonorStatus.UNSUITABLE}, "письма не собираются: донор не прошёл пороги"),
        ],
    )
    async def test_donor_the_letter_does_not_go_to(
        self,
        client: AsyncClient,
        token: str,
        session: AsyncSession,
        fields: dict[str, Any],
        said: str,
    ) -> None:
        donor = await _donor(
            session, "one.example.test", [{"email": "a@one.example.test"}], **fields
        )

        card = await _card(client, token, donor)
        built = await _built(session, donor)

        assert built is None
        assert (card["letter_contact_id"], card["letter_blocked"]) == (None, said)

    async def test_written_donor_gets_no_second_first_letter(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "one.example.test", [{"email": "a@one.example.test"}])
        campaign = CampaignModel(stage=Stage.DONORS, name="Прошлая", status="running")
        session.add(campaign)
        await session.flush()
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                domain_id=donor.domain_id,
                status=MessageStatus.SENT,
                idempotency_key="test:one:earlier",
            )
        )
        await session.commit()

        card = await _card(client, token, donor)

        assert card["letter_contact_id"] is None
        assert card["letter_blocked"] == "донору уже писали — следующие письма идут в тот же диалог"

    async def test_suppressed_donor(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "one.example.test", [{"email": "a@one.example.test"}])
        session.add(
            SuppressionModel(domain_id=donor.domain_id, reason=SuppressionReason.UNSUBSCRIBED)
        )
        await session.commit()

        card = await _card(client, token, donor)
        built = await _built(session, donor)

        assert built is None
        assert card["letter_blocked"] == "все адреса донора в стоп-листе"

    async def test_no_addresses_no_words(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        donor = await _donor(session, "one.example.test", [])

        card = await _card(client, token, donor)

        assert (card["letter_contact_id"], card["letter_blocked"]) == (None, None)
