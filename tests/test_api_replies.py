"""Подтверждение разбора цены по HTTP.

Ветка, без которой не держится приёмка. Проверяется главное:
решение человека сильнее уверенности модели и доходит до карточки
донора, а след того, что сказала модель, не стирается — иначе потом
не узнать, часто ли она ошибается.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    ReplyKind,
    Stage,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
HOST = "donor.example.test"

REVIEW_ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("PATCH", "/api/replies/{reply}", {"price_white": "250", "currency": "EUR"}, "prices"),
    ("GET", "/api/replies/calibration", None, "view"),
    # Ответы, ждущие человека, разбирает один человек, какого бы этапа они ни были.
    ("POST", "/api/replies/{reply}/lead", None, "prices"),
]


@pytest.fixture
async def unsure(session: AsyncSession) -> ReplyModel:
    """Ответ с ценой, в которой модель не уверена."""
    domain = DomainModel(host=HOST)
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add_all([domain, campaign])
    await session.flush()

    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=40))
    contact = ContactModel(domain_id=domain.id, email=f"editor@{HOST}", source=ContactSource.PAGE)
    session.add(contact)
    await session.flush()

    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add(thread)
    await session.flush()

    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Advertising rates",
        body="Good afternoon,",
        sent_at=NOW,
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(message)
    await session.flush()

    reply = ReplyModel(
        thread_id=thread.id,
        message_id=message.id,
        kind=ReplyKind.HUMAN,
        raw_body="A post is around 250 EUR, I think.",
        from_email=f"elena@{HOST}",
        subject="Re: Advertising rates",
        inbound_message_id="<in-1@site.test>",
        price_white=Decimal("250"),
        currency="EUR",
        confidence=0.4,
        placement="sells",
        model_parse={
            "price_white": "250",
            "price_grey": None,
            "currency": "EUR",
            "placement": "sells",
            "confidence": 0.4,
            "prompt_version": "v-test",
        },
    )
    session.add(reply)
    await session.commit()
    return reply


@pytest.fixture
async def reviewer_token(make_user: Any, sign_in: Any) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def watcher_token(make_user: Any, sign_in: Any) -> str:
    """Учётка, у которой право подтверждать отобрано точечно."""
    await make_user("зритель@site.com", role=UserRole.OPERATOR, permissions={"prices": False})
    return await sign_in("зритель@site.com")


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), REVIEW_ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        unsure: ReplyModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        response = await client.request(method, path.replace("{reply}", str(unsure.id)), json=body)

        assert response.status_code == 401

    async def test_pointed_refusal_closes_the_action(
        self, client: AsyncClient, watcher_token: str, unsure: ReplyModel
    ) -> None:
        """Точечное «нет» поверх роли: смотреть цены можно всем,
        подтверждать — названное действие."""
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "EUR"},
            headers=bearer(watcher_token),
        )

        assert response.status_code == 403
        assert "«prices»" in response.json()["detail"]

    async def test_operator_may_review(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        assert response.status_code == 200, response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/replies")
            for method in methods
        }
        in_table = {
            (method, path.replace("{reply}", "{reply_id}")) for method, path, _, _ in REVIEW_ROUTES
        }
        assert in_app == in_table

    async def test_unknown_reply_is_not_found(
        self, client: AsyncClient, reviewer_token: str
    ) -> None:
        response = await client.patch(
            "/api/replies/999999", json={"price_white": "1"}, headers=bearer(reviewer_token)
        )

        assert response.status_code == 404


class TestReviewing:
    async def test_confirmed_price_reaches_the_donor(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """Подтверждение человека сильнее уверенности модели."""
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        assert response.json()["stored_price"] is True
        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert donor.last_price == Decimal("300.00")
        assert donor.last_price_currency == "EUR"

    async def test_model_confidence_is_not_erased(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """След того, что сказала модель, — единственное, по чему потом
        видно, часто ли она ошибается."""
        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        assert reply.confidence == 0.4
        assert reply.reviewed_by == "оператор@site.com"
        assert reply.reviewed_at is not None

    async def test_confirming_no_price_stores_nothing(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """«Цены в письме нет» — законное решение, и оно не должно класть
        в карточку донора прошлую догадку модели."""
        response = await client.patch(
            f"/api/replies/{unsure.id}", json={}, headers=bearer(reviewer_token)
        )

        assert response.json()["stored_price"] is False
        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert donor.last_price is None
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        assert reply.price_white is None

    async def test_decline_lands_on_the_domain(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """«Не продаёт размещения» — одним нажатием: для гест-постинга это
        ответ на главный вопрос письма, и он уходит в отбор."""
        response = await client.patch(
            f"/api/replies/{unsure.id}", json={"declines": True}, headers=bearer(reviewer_token)
        )

        body = response.json()
        assert body["seller_answer"] == "declines"
        assert body["stored_price"] is False
        domain = (await session.execute(select(DomainModel))).scalars().one()
        assert domain.seller_answer == "declines"
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        assert reply.placement == "declines"

    async def test_confirmed_price_means_the_site_sells(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )
        domain = (await session.execute(select(DomainModel))).scalars().one()
        assert domain.seller_answer == "sells"

    async def test_decline_with_a_price_is_refused(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"declines": True, "price_white": "100", "currency": "USD"},
            headers=bearer(reviewer_token),
        )
        assert response.status_code == 422
        assert "не бывают" in response.text

    async def test_review_is_written_to_the_journal(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """Цена в базе — решение с последствиями, и у него должен быть автор."""
        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        rows = await session.execute(
            select(AuditLogModel).where(AuditLogModel.target == f"reply:{unsure.id}")
        )
        entry = rows.scalars().one()
        assert entry.details is not None
        assert entry.details["уверенность модели"] == 0.4

    async def test_thread_stops_waiting_after_review(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        """Список диалогов должен перестать звать на разбор: иначе очередь
        не кончается и по ней перестают ходить."""
        before = await client.get("/api/threads", headers=bearer(reviewer_token))
        assert before.json()[0]["state"] == "needs_review"

        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        after = await client.get("/api/threads", headers=bearer(reviewer_token))
        assert after.json()[0]["state"] == "priced"


class TestWhatTheCardShows:
    async def test_card_shows_who_answered_and_whether_it_waits(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        response = await client.get(
            f"/api/threads/{unsure.thread_id}", headers=bearer(reviewer_token)
        )

        incoming = response.json()["incoming"][0]
        assert incoming["from_email"] == f"elena@{HOST}"
        assert incoming["needs_review"] is True
        assert incoming["confidence"] == 0.4

    async def test_user_model_is_untouched(self, session: AsyncSession) -> None:
        """Сторож: разбор цен не должен был тронуть учётки."""
        rows = await session.execute(select(UserModel))
        assert rows.scalars().all() is not None


class TestDonorIsFoundEvenWithoutTheLetter:
    async def test_price_reaches_the_donor_through_the_thread(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        """Связь с письмом стирается при его удалении, а диалог остаётся —
        и ответ, потерявший письмо, всё равно принадлежит своему донору.

        Найдено живым прогоном: подтверждение срабатывало, а цена
        в карточку не попадала.
        """
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        reply.message_id = None
        await session.commit()

        response = await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "275", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        assert response.json()["stored_price"] is True
        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert donor.last_price == Decimal("275.00")


class TestCalibration:
    """Предложение модели против решения человека — приём, который в соседней
    системе работал в бою для черновиков ответов. Здесь — для полей разбора."""

    async def _calibration(self, client: AsyncClient, token: str) -> dict[str, Any]:
        response = await client.get("/api/replies/calibration", headers=bearer(token))
        assert response.status_code == 200, response.text
        versions = response.json()["versions"]
        assert len(versions) == 1
        return versions[0]

    async def test_confirmed_as_is(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "250.00", "currency": "eur"},
            headers=bearer(reviewer_token),
        )

        score = await self._calibration(client, reviewer_token)
        assert score["version"] == "v-test"
        assert (score["reviewed"], score["as_is"], score["edited"]) == (1, 1, 0)

    async def test_corrected_field_is_named(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel, session: AsyncSession
    ) -> None:
        await client.patch(
            f"/api/replies/{unsure.id}",
            json={"price_white": "300", "currency": "EUR"},
            headers=bearer(reviewer_token),
        )

        score = await self._calibration(client, reviewer_token)
        assert score["edited"] == 1
        assert score["wrong"]["price_white"] == 1
        assert score["wrong"]["currency"] == 0
        # ⚠ Снимок модели правкой человека не переписан — иначе считать не с чем.
        reply = await session.get(ReplyModel, unsure.id)
        assert reply is not None
        await session.refresh(reply)
        assert reply.model_parse is not None
        assert reply.model_parse["price_white"] == "250"

    async def test_decline_over_a_price_is_a_placement_miss(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        await client.patch(
            f"/api/replies/{unsure.id}", json={"declines": True}, headers=bearer(reviewer_token)
        )

        score = await self._calibration(client, reviewer_token)
        assert score["wrong"]["placement"] == 1
        assert score["wrong"]["price_white"] == 1

    async def test_unreviewed_is_not_scored(
        self, client: AsyncClient, reviewer_token: str, unsure: ReplyModel
    ) -> None:
        """Без человека сверять не с чем — это не «точность», а пустое место."""
        score = await self._calibration(client, reviewer_token)
        assert score["reviewed"] == 0
        assert score["waiting"] == 1, "уверенность 0,4 — ждёт человека, а не положена сама"
        assert score["auto_stored"] == 0
