"""Ревью стыков (A7): «отправлено сегодня» у ящика — тем же счётом, что дневной кап.

Кап ящика и разгон считают только первые письма (`outreach/repository.sent_today(first_only=True)`,
решение 21.09.2026: у добивок свой часовой потолок), и лимиты домена и направления на том же
экране — тоже. Карточка ящика считала все письма: ящик с выбранным капом и добивками
показывал «25 из 20», а ящик, которому ещё можно писать, — «20 из 20». Экран — общий код
(`api/senders/routes.py`): `xfail(strict=True)` с правкой для PR «общее».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import MessageStatus, Stage, UserRole
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_sender
from tests.test_api_outreach import NOW, _sent_letters


async def _followup_sent_today(session: AsyncSession, box: SenderModel) -> None:
    """Добивка, ушедшая с ящика сегодня: в кап не входит, у неё часовой потолок."""
    domain = DomainModel(host="followed.example.test")
    campaign = CampaignModel(stage=Stage.DONORS, name="Добивки", status="running")
    session.add_all([domain, campaign])
    await session.flush()
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            domain_id=domain.id,
            step=1,
            status=MessageStatus.SENT,
            sender_id=box.id,
            sent_at=NOW,
            idempotency_key="donors:followed.example.test:1",
        )
    )
    await session.commit()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "общий код api/senders/routes.py: карточка ящика (`SenderCard.sent_today`) считает все "
        "письма, а кап, разгон и лимиты домена — только первые; правка — PR «общее» (ревью "
        "стыков R1, A7)"
    ),
)
async def test_the_box_card_counts_what_its_cap_counts(
    client: AsyncClient, session: AsyncSession, admin_token: str
) -> None:
    box = await make_sender(session, "a@mail-seams.example.test")
    await _sent_letters(session, box, count=2)
    await _followup_sent_today(session, box)

    response = await client.get("/api/senders", headers=bearer(admin_token))

    [card] = response.json()["senders"]
    assert (card["sent_today"], card["warmup_allowance"]) == (2, 20)


@pytest.fixture
async def admin_token(
    make_user: Callable[..., Awaitable[Any]], sign_in: Callable[..., Awaitable[str]]
) -> str:
    await make_user("admin@senders-seams.example.test", role=UserRole.ADMIN)
    return await sign_in("admin@senders-seams.example.test")
