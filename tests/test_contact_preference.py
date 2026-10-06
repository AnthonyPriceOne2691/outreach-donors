"""Какой адрес донора первый — вписанный человеком, если он жив.

Боевой прогон 06.10.2026: человек вписал адрес в карточке донора, а порядок
решал ничью по оценке 100 старшей записью — то есть в пользу найденного
лестницей, и письмо ушло бы не туда, куда человек сказал.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.features.contacts.manual import MANUAL_SCORE
from backend.features.contacts.preference import DEAD, preferred_first
from backend.features.core.domain import ContactSource
from backend.features.core.models.donor import ContactModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

pytestmark = pytest.mark.asyncio


async def _order(session: AsyncSession, domain_id: int) -> list[str]:
    rows = await session.execute(
        select(ContactModel.email)
        .where(ContactModel.domain_id == domain_id)
        .order_by(*preferred_first())
    )
    return list(rows.scalars().all())


def _contact(domain_id: int, email: str, source: ContactSource, **fields: object) -> ContactModel:
    return ContactModel(domain_id=domain_id, email=email, source=source, **fields)  # type: ignore[arg-type]


async def test_typed_address_beats_a_found_one_with_the_same_score(session: AsyncSession) -> None:
    domain = await make_donor(session, "tie.example.test")
    session.add(
        _contact(domain.id, "info@tie.example.test", ContactSource.PAGE, verification_score=100)
    )
    await session.flush()
    session.add(
        _contact(
            domain.id,
            "editor@tie.example.test",
            ContactSource.MANUAL,
            verification_score=MANUAL_SCORE,
        )
    )
    await session.flush()

    assert (await _order(session, domain.id))[0] == "editor@tie.example.test"


async def test_typed_address_beats_one_that_replied(session: AsyncSession) -> None:
    """Решение человека сильнее эвристики «пишем тому, кто отвечал»."""
    domain = await make_donor(session, "replied.example.test")
    session.add(
        _contact(
            domain.id,
            "sales@replied.example.test",
            ContactSource.PROVIDER,
            verification_score=90,
            last_replied_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    )
    session.add(
        _contact(
            domain.id,
            "editor@replied.example.test",
            ContactSource.MANUAL,
            verification_score=MANUAL_SCORE,
        )
    )
    await session.flush()

    assert await _order(session, domain.id) == [
        "editor@replied.example.test",
        "sales@replied.example.test",
    ]


async def test_dead_typed_address_still_goes_last(session: AsyncSession) -> None:
    domain = await make_donor(session, "dead.example.test")
    session.add(
        _contact(
            domain.id,
            "editor@dead.example.test",
            ContactSource.MANUAL,
            verification_score=0,
            verification_status=DEAD,
        )
    )
    session.add(_contact(domain.id, "info@dead.example.test", ContactSource.PAGE))
    await session.flush()

    assert await _order(session, domain.id) == [
        "info@dead.example.test",
        "editor@dead.example.test",
    ]


async def test_address_learned_from_a_reply_is_not_a_typed_one(session: AsyncSession) -> None:
    """Адрес из ответа тоже заводится как `manual`, но без оценки — человек его
    не вписывал, и первым он встаёт по «отвечали», а не по решению человека."""
    domain = await make_donor(session, "learned.example.test")
    session.add(
        _contact(
            domain.id,
            "john@learned.example.test",
            ContactSource.MANUAL,
            last_replied_at=datetime(2026, 10, 1, tzinfo=UTC),
        )
    )
    session.add(
        _contact(
            domain.id,
            "editor@learned.example.test",
            ContactSource.MANUAL,
            verification_score=MANUAL_SCORE,
        )
    )
    await session.flush()

    assert (await _order(session, domain.id))[0] == "editor@learned.example.test"
