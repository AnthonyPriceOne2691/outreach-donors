"""Карточка донора ведёт в его переписку: «Открыть диалог →» (аудит экранов 09.10.2026).

Диалог — на адрес, а не на домен, поэтому у донора их бывает несколько: карточка
получает все переписки Этапа 1, новую первой, и ведёт в неё. Переписка того же домена
на другом этапе — не разговор о цене размещения, её в карточке нет.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.features.core.domain import Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.outreach import CampaignModel, ThreadModel
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


async def _thread(session: AsyncSession, domain_id: int, stage: Stage) -> int:
    campaign = CampaignModel(stage=stage, name=f"Проверка {stage.value}", status="running")
    session.add(campaign)
    await session.flush()
    thread = ThreadModel(domain_id=domain_id, campaign_id=campaign.id)
    session.add(thread)
    await session.flush()
    return thread.id


async def _card(client: AsyncClient, token: str, session: AsyncSession, domain_id: int) -> dict:
    donor_id = await session.scalar(select(DonorModel.id).where(DonorModel.domain_id == domain_id))
    response = await client.get(f"/api/donors/{donor_id}", headers=bearer(token))
    assert response.status_code == 200, response.text
    return response.json()


async def test_card_leads_to_the_newest_donor_thread(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    domain = await make_donor(session, "donor.example.test")
    older = await _thread(session, domain.id, Stage.DONORS)
    elsewhere = await _thread(session, domain.id, Stage.ADVERTISERS)
    newer = await _thread(session, domain.id, Stage.DONORS)
    await session.commit()

    card = await _card(client, token, session, domain.id)

    assert card["threads"] == [newer, older]
    assert elsewhere not in card["threads"]


async def test_card_without_letters_has_no_threads(
    client: AsyncClient, token: str, session: AsyncSession
) -> None:
    domain = await make_donor(session, "quiet.example.test")
    await session.commit()

    card = await _card(client, token, session, domain.id)

    assert card["threads"] == []
