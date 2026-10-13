"""Карточка донора: письмо сайту в другой роли — словами перезапроса (ревью стыков 12.10.2026).

Одному адресату не пишут и как донору, и как рекламодателю, и как лиду продаж. Сайту писали
продажи — сборка донору письма не соберёт, а карточка объясняла это словами доноров: «донору уже
писали» о письме, которое ему не уходило, и «донор ответил» о разговоре лида, которого в его
диалогах нет. Теперь — те же слова и та же видимость, что у перезапроса цены (`other_role`):
письмо продаж номером и ролью — только с правом «Продажи».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.core.models.access import UserModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ReplyModel
from backend.features.letters import attempts
from backend.features.letters.recipients import Recipients
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_letter_address import _donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

HOST = "other-role.example.test"
HIDDEN = "уже писали в другой роли: одному адресату письмо в другой роли не уходит."


async def _letter(session: AsyncSession, donor: DonorModel, stage: Stage, *, answered: bool) -> int:
    """Письмо сайту донора в рассылке этапа — ушло и живо; `answered` — на него ответили."""
    campaign = CampaignModel(stage=stage, name=f"Другая роль {stage.value}", status="running")
    session.add(campaign)
    await session.flush()
    letter = MessageModel(
        campaign_id=campaign.id,
        domain_id=donor.domain_id,
        step=0,
        status=MessageStatus.DELIVERED,
        sent_at=datetime.now(UTC) - timedelta(days=2),
        subject="A question",
        body="Good afternoon!",
        idempotency_key=f"other-role:{stage.value}:{donor.domain_id}:0",
    )
    session.add(letter)
    await session.flush()
    if answered:
        session.add(
            ReplyModel(
                message_id=letter.id,
                kind=ReplyKind.HUMAN,
                raw_body="Tell me more",
                from_email=f"ceo@{HOST}",
            )
        )
    await session.commit()
    return letter.id


async def _blocked(session: AsyncSession, donor: DonorModel, *, sales_visible: bool) -> str | None:
    found = await Recipients(session).letter_address(donor.domain_id, sales_visible=sales_visible)
    return found.blocked


@pytest.mark.parametrize("answered", [False, True], ids=["писали", "лид-ответил"])
async def test_a_sales_letter_is_named_as_the_recheck_names_it(
    session: AsyncSession, answered: bool
) -> None:
    donor = await _donor(session, HOST, [{"email": f"info@{HOST}"}])
    letter = await _letter(session, donor, Stage.SALES, answered=answered)

    hidden = await _blocked(session, donor, sales_visible=False)
    shown = await _blocked(session, donor, sales_visible=True)

    assert hidden == HIDDEN
    assert shown == (
        f"уже писали как лиду продаж (письмо №{letter}): одному адресату письмо в другой "
        "роли не уходит."
    )


async def test_an_offer_to_the_site_is_named_to_everyone(session: AsyncSession) -> None:
    """Письмо рекламодателю — не продажи: номер и роль видны всем, как у перезапроса."""
    donor = await _donor(session, HOST, [{"email": f"info@{HOST}"}])
    letter = await _letter(session, donor, Stage.ADVERTISERS, answered=False)

    blocked = await _blocked(session, donor, sales_visible=False)

    assert blocked is not None
    assert blocked.startswith(f"уже писали как рекламодателю (письмо №{letter})")


async def test_the_donors_own_letter_keeps_the_donor_words(session: AsyncSession) -> None:
    donor = await _donor(session, HOST, [{"email": f"info@{HOST}"}])
    await _letter(session, donor, Stage.DONORS, answered=True)

    assert await _blocked(session, donor, sales_visible=False) == attempts.ANSWERED


async def test_the_card_names_it_by_the_right_of_who_looks(
    client: AsyncClient, session: AsyncSession, make_user: MakeUser, sign_in: SignIn
) -> None:
    donor = await _donor(session, HOST, [{"email": f"info@{HOST}"}])
    letter = await _letter(session, donor, Stage.SALES, answered=True)
    await make_user("outsider@team.example.test", permissions={"sales": False})
    await make_user("seller@team.example.test")
    outsider = bearer(await sign_in("outsider@team.example.test"))
    seller = bearer(await sign_in("seller@team.example.test"))

    hidden = (await client.get(f"/api/donors/{donor.id}", headers=outsider)).json()
    shown = (await client.get(f"/api/donors/{donor.id}", headers=seller)).json()

    assert (hidden["letter_contact_id"], hidden["letter_blocked"]) == (None, HIDDEN)
    assert f"как лиду продаж (письмо №{letter})" in shown["letter_blocked"]
