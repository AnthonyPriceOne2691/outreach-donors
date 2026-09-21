"""Рассылка и диалоги по HTTP: кто пускается и что видно.

Права здесь проверяются так же таблицей, как и в разделе учёток:
маршрут без строки в таблице — это маршрут, права которого никто
не проверял.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.features.core.domain import (
    ContactSource,
    MessageStatus,
    ReplyKind,
    SenderStatus,
    Stage,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime.now(UTC)

OUTREACH_ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/senders", None, "senders"),
    ("POST", "/api/senders/{sender}/enable", None, "senders"),
    ("POST", "/api/senders/{sender}/disable", {"reason": "проверка"}, "senders"),
    ("GET", "/api/threads", None, "view"),
    ("GET", "/api/threads/{thread}", None, "view"),
]


@pytest.fixture
async def sender(session: AsyncSession) -> SenderModel:
    model = SenderModel(
        domain="mail-one.example.test",
        email="outreach@mail-one.example.test",
        stage=Stage.DONORS,
        daily_cap=20,
        status=SenderStatus.FREE,
        enabled=True,
        warmup_started_at=NOW - timedelta(days=30),
    )
    session.add(model)
    await session.commit()
    return model


@pytest.fixture
async def thread(session: AsyncSession) -> ThreadModel:
    domain = DomainModel(host="donor.example.test")
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add_all([domain, campaign])
    await session.flush()

    contact = ContactModel(
        domain_id=domain.id, email="editor@donor.example.test", source=ContactSource.PAGE
    )
    session.add(contact)
    await session.flush()

    model = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add(model)
    await session.flush()

    session.add(
        MessageModel(
            campaign_id=campaign.id,
            thread_id=model.id,
            domain_id=domain.id,
            contact_id=contact.id,
            step=0,
            status=MessageStatus.DELIVERED,
            subject="Стоимость размещения",
            body="Здравствуйте!",
            sent_at=NOW - timedelta(days=3),
            idempotency_key="test:donor.example.test:0",
        )
    )
    session.add(
        ReplyModel(
            thread_id=model.id,
            kind=ReplyKind.HUMAN,
            raw_body="Размещение — 250 EUR.",
            price_white=250,
            currency="EUR",
            confidence=0.9,
        )
    )
    await session.commit()
    return model


async def _sent_letters(
    session: AsyncSession,
    sender: SenderModel,
    *,
    count: int,
    when: datetime | None = None,
) -> None:
    """Письма, отправленные с этого ящика. Дневной расход считается по ним."""
    moment = when or NOW
    campaign = CampaignModel(stage=Stage.DONORS, name="Расход", status="running")
    session.add(campaign)
    await session.flush()

    for number in range(count):
        host = f"spent-{number}.example.test"
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
        session.add(
            MessageModel(
                campaign_id=campaign.id,
                domain_id=domain.id,
                step=0,
                status=MessageStatus.SENT,
                sender_id=sender.id,
                sent_at=moment,
                idempotency_key=f"donors:{host}:0",
            )
        )
    await session.commit()


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


def _path(template: str, sender: SenderModel, thread: ThreadModel) -> str:
    return template.format(sender=sender.id, thread=thread.id)


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), OUTREACH_ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        sender: SenderModel,
        thread: ThreadModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        response = await client.request(method, _path(path, sender, thread), json=body)
        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "body", "permission"), OUTREACH_ROUTES)
    async def test_operator_sees_threads_but_not_senders(
        self,
        client: AsyncClient,
        operator_token: str,
        sender: SenderModel,
        thread: ThreadModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        """Диалоги оператору положены, домены рассылки — нет: включённый
        заново домен начинает разгон, и ошибка стоит репутации."""
        response = await client.request(
            method, _path(path, sender, thread), json=body, headers=bearer(operator_token)
        )

        if permission == "senders":
            assert response.status_code == 403
            # Сервер называет действие кодом — по нему разбирают журнал.
            # Словами его переводит интерфейс, у себя.
            assert "«senders»" in response.json()["detail"]
        else:
            assert response.status_code == 200, response.text

    @pytest.mark.parametrize(("method", "path", "body", "permission"), OUTREACH_ROUTES)
    async def test_admin_is_let_in(
        self,
        client: AsyncClient,
        admin_token: str,
        sender: SenderModel,
        thread: ThreadModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        response = await client.request(
            method, _path(path, sender, thread), json=body, headers=bearer(admin_token)
        )
        assert response.status_code not in (401, 403), response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith(("/api/senders", "/api/threads"))
            for method in methods
        }
        in_table = {
            (method, path.replace("{sender}", "{sender_id}").replace("{thread}", "{thread_id}"))
            for method, path, _, _ in OUTREACH_ROUTES
        }
        assert in_app == in_table


class TestSenders:
    async def test_warmup_is_shown_with_both_numbers(
        self,
        client: AsyncClient,
        admin_token: str,
        sender: SenderModel,
        session: AsyncSession,
    ) -> None:
        """«Отправлено 7 из 20» врёт на разгоне: потолок дня и дневной кап —
        разные числа, и отдаются оба.

        Дневной расход при этом считается по письмам, а не по полю: поле
        было, и обнулять его было некому — ящик упирался бы в кап навсегда.
        Поэтому семь писем здесь настоящие.
        """
        await _sent_letters(session, sender, count=7)

        response = await client.get("/api/senders", headers=bearer(admin_token))

        card = response.json()["senders"][0]
        assert card["daily_cap"] == 20
        assert card["warmup_allowance"] == 20
        assert card["sent_today"] == 7

    async def test_yesterdays_letters_do_not_count_today(
        self,
        client: AsyncClient,
        admin_token: str,
        sender: SenderModel,
        session: AsyncSession,
    ) -> None:
        """Вчерашняя отправка не съедает сегодняшний лимит."""
        await _sent_letters(session, sender, count=3, when=NOW - timedelta(days=1))

        response = await client.get("/api/senders", headers=bearer(admin_token))

        assert response.json()["senders"][0]["sent_today"] == 0

    async def test_enabling_restarts_the_warmup(
        self, client: AsyncClient, admin_token: str, sender: SenderModel
    ) -> None:
        await client.post(
            f"/api/senders/{sender.id}/disable",
            json={"reason": "проверка"},
            headers=bearer(admin_token),
        )

        response = await client.post(
            f"/api/senders/{sender.id}/enable", headers=bearer(admin_token)
        )

        card = response.json()
        assert card["enabled"] is True
        assert card["warmup_day"] == 1
        assert card["warmup_allowance"] < card["daily_cap"]

    async def test_disabling_keeps_the_reason(
        self, client: AsyncClient, admin_token: str, sender: SenderModel
    ) -> None:
        response = await client.post(
            f"/api/senders/{sender.id}/disable",
            json={"reason": "доля отказов 7%"},
            headers=bearer(admin_token),
        )

        assert response.json()["pause_reason"] == "доля отказов 7%"

    async def test_unknown_sender_is_not_found(self, client: AsyncClient, admin_token: str) -> None:
        response = await client.post("/api/senders/9999/enable", headers=bearer(admin_token))
        assert response.status_code == 404


class TestThreads:
    async def test_state_is_computed_not_stored(
        self, client: AsyncClient, operator_token: str, thread: ThreadModel
    ) -> None:
        """В базе у диалога `status = open`, а на экране «цена получена»:
        состояние считается по событиям."""
        assert thread.status.value == "open"

        response = await client.get("/api/threads", headers=bearer(operator_token))

        row = response.json()[0]
        assert row["state"] == "priced"
        assert row["price_white"] == "250.00"

    async def test_conversation_keeps_the_original_text(
        self, client: AsyncClient, operator_token: str, thread: ThreadModel
    ) -> None:
        """Распознанное отдаётся рядом с исходным текстом, а не вместо:
        иначе спорный разбор нечем проверить."""
        response = await client.get(f"/api/threads/{thread.id}", headers=bearer(operator_token))

        body = response.json()
        assert body["letters"][0]["subject"] == "Стоимость размещения"
        assert "250 EUR" in body["incoming"][0]["raw_body"]
        assert body["incoming"][0]["price_white"] == "250.00"

    async def test_unknown_thread_is_not_found(
        self, client: AsyncClient, operator_token: str
    ) -> None:
        response = await client.get("/api/threads/9999", headers=bearer(operator_token))
        assert response.status_code == 404
