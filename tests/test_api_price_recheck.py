"""Маршруты перезапроса цены: список протухших, один донор, отправка по одному и пачкой.

Проверяется то, что видит экран: слова сервера до нажатия и после, право на письмо
наружу (`send`), потолок пачки словами, итог по каждому донору — и что второй перезапрос
того же донора раньше срока отказывает, а не уходит.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import MessageStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.chain import RECHECK_STEP
from backend.features.letters.price_recheck import BATCH_MAX
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.recheck_world import ANSWERED_FROM, BOX, HOST, World, manual_stale, stale_conversation

pytestmark = pytest.mark.usefixtures("filled_legal")

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture(autouse=True)
def _null_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Письмо из теста не уходит наружу, даже если в `.env` разработчика настоящая почта."""
    monkeypatch.setattr(outreach_cfg, "TRANSPORT", "null")


@pytest.fixture
async def world(session: AsyncSession) -> World:
    return await stale_conversation(session, datetime.now(UTC).replace(microsecond=0))


@pytest.fixture
async def admin(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("admin@team.example.test", role=UserRole.ADMIN)
    return bearer(await sign_in("admin@team.example.test"))


@pytest.fixture
async def operator(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    """Оператор без права отправки — смотреть может, писать наружу нет."""
    await make_user("operator@team.example.test")
    return bearer(await sign_in("operator@team.example.test"))


class TestLooking:
    async def test_the_list_names_where_each_letter_goes(
        self, client: AsyncClient, session: AsyncSession, world: World, operator: dict[str, str]
    ) -> None:
        manual = await manual_stale(
            session, datetime.now(UTC), host="manual.example.test", email="desk@manual.example.test"
        )

        response = await client.get("/api/price-rechecks", headers=operator)

        assert response.status_code == 200, response.text
        body = response.json()
        assert (body["total"], body["ttl_days"], body["every_days"]) == (2, 150, 14)
        assert body["batch_max"] == BATCH_MAX
        rows = {row["host"]: row for row in body["rows"]}
        thread = rows[HOST]
        assert (thread["way"], thread["thread_id"], thread["refusal"]) == (
            "thread",
            world.thread.id,
            None,
        )
        assert (thread["email"], thread["mailbox"], thread["price"]) == (ANSWERED_FROM, BOX, 90.0)
        first = rows["manual.example.test"]
        assert (first["donor_id"], first["way"], first["mailbox"]) == (manual.id, "first", None)
        assert first["email"] == "desk@manual.example.test"
        assert first["source"] == "manual"

    async def test_one_donor_shows_the_letter_in_full(
        self, client: AsyncClient, world: World, operator: dict[str, str]
    ) -> None:
        response = await client.get(f"/api/price-rechecks/{world.donor.id}", headers=operator)

        assert response.status_code == 200, response.text
        card = response.json()
        assert card["subject"] == f"Re: Guest article on {HOST}"
        assert card["body"].startswith("Hi,\n\nWe're planning a new round of sponsored articles")
        assert card["body"].endswith("Best regards,\nAnna Ro\n1 Main Street, Dublin")

    async def test_unknown_donor_is_404_in_words(
        self, client: AsyncClient, operator: dict[str, str]
    ) -> None:
        response = await client.get("/api/price-rechecks/987654", headers=operator)

        assert response.status_code == 404
        assert response.json()["detail"] == "Донора №987654 нет"


class TestAsking:
    async def test_sends_and_the_second_click_is_refused_in_words(
        self, client: AsyncClient, session: AsyncSession, world: World, admin: dict[str, str]
    ) -> None:
        donor_id = world.donor.id

        response = await client.post(
            "/api/price-rechecks", json={"donor_ids": [donor_id]}, headers=admin
        )

        assert response.status_code == 200, response.text
        [sent] = response.json()["sent"]
        assert (sent["donor_id"], sent["way"], sent["sender_email"]) == (donor_id, "thread", BOX)
        assert sent["real"] is False  # нулевая почта
        letter = await session.scalar(
            select(MessageModel).where(MessageModel.id == sent["message_id"])
        )
        assert letter is not None
        assert (letter.step, letter.status) == (RECHECK_STEP, MessageStatus.SENT)

        again = await client.post(
            "/api/price-rechecks", json={"donor_ids": [donor_id]}, headers=admin
        )

        assert again.status_code == 200
        assert again.json()["sent"] == []
        [refused] = again.json()["refused"]
        assert refused["host"] == HOST
        assert refused["reason"].startswith(f"Цену {HOST} уже перезапросили")

    async def test_without_the_right_to_send_it_is_403(
        self, client: AsyncClient, world: World, operator: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/price-rechecks", json={"donor_ids": [world.donor.id]}, headers=operator
        )

        assert response.status_code == 403

    async def test_too_many_at_once_is_refused_in_words(
        self, client: AsyncClient, admin: dict[str, str]
    ) -> None:
        response = await client.post(
            "/api/price-rechecks",
            json={"donor_ids": list(range(1, BATCH_MAX + 2))},
            headers=admin,
        )

        assert response.status_code == 422
        [error] = response.json()["detail"]
        assert error["msg"].startswith(f"За раз перезапрашиваем не больше {BATCH_MAX} доноров")
