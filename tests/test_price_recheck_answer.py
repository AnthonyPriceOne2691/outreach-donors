"""Ответ на перезапрос цены без числа — «цена та же» (проверка 12.10.2026).

Перезапрос просит назвать цену числом, но ответ бывает «цена та же». Такой ответ разбор
в карточку не кладёт — и переписка не должна писать «цена получена» старой ценой: она ждёт
человека, а у него в разборе — «Цена та же». Прежняя цена ложится в карточку временем
ответа, снова свежая, в журнале — кто и по какому ответу.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.crawl.targets import choose
from backend.features.letters import price_recheck
from backend.features.letters.sending import Sending
from backend.features.outreach.repository import OutreachRepository
from backend.features.outreach.threads import RECHECK_UNPRICED, ThreadState
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Parser
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.recheck_world import HOST, Recording, World, answer, stale_conversation

pytestmark = pytest.mark.usefixtures("filled_legal")

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


class Said:
    """Модель разбора: что сказал донор — без числа, разбор уверен."""

    def __init__(self, placement: str = "sells") -> None:
        self.found = Extracted(placement=placement, confidence=0.95)

    async def extract(self, incoming: Incoming) -> Extracted:
        return self.found


@pytest.fixture
def moment() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
async def world(session: AsyncSession, moment: datetime) -> World:
    return await stale_conversation(session, moment)


@pytest.fixture
async def admin(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user("admin@team.example.test", role=UserRole.ADMIN)
    return bearer(await sign_in("admin@team.example.test"))


async def _answered(
    session: AsyncSession, world: World, moment: datetime, *, placement: str = "sells"
) -> ReplyModel:
    """Перезапрос ушёл, донор ответил через два часа — без числа."""
    asked = await price_recheck.ask(
        session,
        Sending(session, Recording(), now=moment),
        world.donor.id,
        author_id=None,
        now=moment,
    )
    letter = await session.get(MessageModel, asked.message_id)
    assert letter is not None
    reply = await answer(session, letter, "Same as before, thanks.", at=moment + timedelta(hours=2))
    await Parser(session, Said(placement)).parse(reply.id)
    await session.commit()
    return reply


async def _state(session: AsyncSession, thread_id: int) -> ThreadState:
    session.expire_all()
    return (await OutreachRepository(session).thread(thread_id)).row.summary.state


class TestWaitsForPerson:
    async def test_an_answer_without_a_number_is_not_a_price(
        self,
        session: AsyncSession,
        world: World,
        moment: datetime,
        client: AsyncClient,
        admin: dict[str, str],
    ) -> None:
        thread_id = world.thread.id
        reply = await _answered(session, world, moment)

        assert await _state(session, thread_id) is ThreadState.NEEDS_REVIEW
        card = (await client.get(f"/api/threads/{thread_id}", headers=admin)).json()
        [shown] = [one for one in card["incoming"] if one["id"] == reply.id]
        assert (shown["needs_review"], shown["review_reason"]) == (True, RECHECK_UNPRICED)
        assert shown["same_price"] == {"price": "90.00", "currency": "USD"}
        assert card["card"]["state"] == "needs_review"

    async def test_an_empty_confirmation_leaves_no_price_on_the_thread(
        self,
        session: AsyncSession,
        world: World,
        moment: datetime,
        client: AsyncClient,
        admin: dict[str, str],
    ) -> None:
        """Человек подтвердил «цены в письме нет» — переписка «ответил человек», а не
        «цена получена» старой ценой."""
        thread_id = world.thread.id
        reply = await _answered(session, world, moment)

        confirmed = await client.patch(
            f"/api/replies/{reply.id}",
            json={"price_white": None, "price_grey": None, "currency": None},
            headers=admin,
        )

        assert confirmed.status_code == 200, confirmed.text
        assert await _state(session, thread_id) is ThreadState.REPLIED

    async def test_no_price_with_a_no_is_an_answer_not_a_question(
        self, session: AsyncSession, world: World, moment: datetime
    ) -> None:
        """«Больше не продаём» — ответ на вопрос: человека он не ждёт."""
        thread_id = world.thread.id
        await _answered(session, world, moment, placement="declines")

        assert await _state(session, thread_id) is ThreadState.DECLINED


class TestSamePrice:
    async def test_same_price_makes_the_old_price_fresh_by_the_answer(
        self,
        session: AsyncSession,
        world: World,
        moment: datetime,
        client: AsyncClient,
        admin: dict[str, str],
    ) -> None:
        thread_id, donor_id = world.thread.id, world.donor.id
        reply = await _answered(session, world, moment)
        answered_at = reply.created_at

        response = await client.post(
            f"/api/price-rechecks/replies/{reply.id}/same-price", headers=admin
        )

        assert response.status_code == 200, response.text
        assert response.json()["stored"] is True
        assert Decimal(response.json()["price"]) == Decimal("90")
        session.expire_all()
        donor = await session.get(type(world.donor), donor_id)
        assert donor is not None
        assert (donor.last_price, donor.last_price_at) == (Decimal("90"), answered_at)
        assert HOST in (await choose(session, now=moment + timedelta(hours=3))).hosts
        assert await _state(session, thread_id) is ThreadState.PRICED
        record = await session.scalar(
            select(AuditLogModel).where(
                AuditLogModel.action == AuditAction.PRICE_REVIEWED,
                AuditLogModel.target == f"reply:{reply.id}",
            )
        )
        assert record is not None
        assert record.details["действие"] == (
            "цена та же — прежняя цена подтверждена ответом на перезапрос"
        )

    async def test_twice_or_on_another_answer_it_is_refused_in_words(
        self,
        session: AsyncSession,
        world: World,
        moment: datetime,
        client: AsyncClient,
        admin: dict[str, str],
    ) -> None:
        old_reply = world.reply.id
        reply = await _answered(session, world, moment)
        first = await client.post(
            f"/api/price-rechecks/replies/{reply.id}/same-price", headers=admin
        )
        assert first.status_code == 200, first.text

        again = await client.post(
            f"/api/price-rechecks/replies/{reply.id}/same-price", headers=admin
        )
        before = await client.post(
            f"/api/price-rechecks/replies/{old_reply}/same-price", headers=admin
        )

        assert again.status_code == 409
        assert "уже разобран" in again.json()["detail"]
        assert before.status_code == 409
