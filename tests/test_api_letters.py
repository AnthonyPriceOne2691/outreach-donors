"""Очередь писем по HTTP: кто пускается и что видно.

Права здесь проверяются таблицей, как и в остальных разделах: маршрут
без строки в таблице — это маршрут, права которого никто не проверял.

Разделение простое и не про иерархию: смотреть очередь может каждый,
у кого есть доступ к базе, а менять что-либо — только с правом
на отправку. Скомпрометированная учётка обычного сотрудника не должна
превращаться в рассылку с наших доменов.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    SenderStatus,
    Stage,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from backend.features.letters import compose, template
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)

#: Маршрут, права на нём и тело запроса. Смотреть — `view`, менять — `send`.
LETTER_ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/letters", None, "view"),
    ("POST", "/api/letters/build", {"campaign": "Проверка"}, "send"),
    ("PATCH", "/api/letters/{letter}", {"subject": "S", "body": "B"}, "send"),
    ("POST", "/api/letters/{letter}/skip", None, "send"),
    ("POST", "/api/letters/{letter}/send", None, "send"),
]


@pytest.fixture
async def letter(session: AsyncSession, filled_legal: None) -> MessageModel:
    """Письмо в очереди, собранное тем же кодом, что и в бою."""
    domain = DomainModel(host="donor.example.test")
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="draft")
    session.add_all([domain, campaign])
    await session.flush()

    session.add(
        DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=40, review="accepted")
    )
    contact = ContactModel(
        domain_id=domain.id, email="editor@donor.example.test", source=ContactSource.PAGE
    )
    session.add(contact)
    session.add(
        SenderModel(
            domain="mail.example.test",
            email="outreach1@mail.example.test",
            stage=Stage.DONORS,
            daily_cap=20,
            status=SenderStatus.FREE,
            enabled=True,
        )
    )
    await session.flush()

    rendered = compose.render(
        template.default(), compose.values_for(host=domain.host, domain_id=domain.id)
    )
    body = compose.assemble(rendered, {"greeting": "Good afternoon to you,"})
    model = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.QUEUED,
        subject=body.subject,
        body=body.body,
        uniqueness_pct=0.18,
        idempotency_key="donors:donor.example.test:0",
    )
    session.add(model)
    await session.commit()
    return model


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


def _path(path: str, letter: MessageModel) -> str:
    return path.replace("{letter}", str(letter.id))


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), LETTER_ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        letter: MessageModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        response = await client.request(method, _path(path, letter), json=body)

        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "body", "permission"), LETTER_ROUTES)
    async def test_operator_sees_but_does_not_send(
        self,
        client: AsyncClient,
        operator_token: str,
        letter: MessageModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        """Отправка не входит в роль оператора: её выдают поимённо."""
        response = await client.request(
            method, _path(path, letter), json=body, headers=bearer(operator_token)
        )

        if permission == "send":
            assert response.status_code == 403
            assert "«send»" in response.json()["detail"]
        else:
            assert response.status_code == 200, response.text

    async def test_pointed_permission_opens_sending(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        letter: MessageModel,
    ) -> None:
        """Точечное право поверх роли — то, ради чего оно и заведено."""
        await make_user("правка@site.com", role=UserRole.OPERATOR, permissions={"send": True})
        token = await sign_in("правка@site.com")

        response = await client.post(f"/api/letters/{letter.id}/skip", headers=bearer(token))

        assert response.status_code == 200, response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/letters")
            for method in methods
        }
        in_table = {
            (method, path.replace("{letter}", "{letter_id}"))
            for method, path, _, _ in LETTER_ROUTES
        }
        assert in_app == in_table


class TestQueue:
    async def test_queue_shows_letter_with_its_text(
        self, client: AsyncClient, admin_token: str, letter: MessageModel
    ) -> None:
        """Текст приходит вместе со списком: экран для того и сделан,
        чтобы читать письма подряд."""
        response = await client.get("/api/letters", headers=bearer(admin_token))

        card = response.json()["letters"][0]
        assert card["host"] == "donor.example.test"
        assert card["email"] == "editor@donor.example.test"
        assert "Good afternoon to you," in card["body"]
        assert card["verdict"] is None  # 18% — в коридоре

    async def test_queue_says_what_blocks_sending(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        """Имя отправителя не задано — отправить нельзя ни одно письмо,
        и узнать об этом надо до нажатия, а не после.

        Настройки здесь не подменяются нарочно: это состояние сервиса
        на сегодня, и экран обязан его показывать. Адрес и отписка
        отправку больше не держат — юридический блок снят 23.09.2026.
        """
        response = await client.get("/api/letters", headers=bearer(admin_token))

        blocked = response.json()["blocked_by"]
        assert blocked == ["OUTREACH_SENDER_NAME"]

    async def test_filled_settings_block_nothing(
        self, client: AsyncClient, admin_token: str, letter: MessageModel
    ) -> None:
        response = await client.get("/api/letters", headers=bearer(admin_token))

        assert response.json()["blocked_by"] == []

    async def test_queue_says_transport_is_not_real(
        self, client: AsyncClient, admin_token: str, letter: MessageModel
    ) -> None:
        """Ложь здесь дороже всего: письмо, помеченное отправленным
        и никуда не ушедшее, выглядит как работа."""
        response = await client.get("/api/letters", headers=bearer(admin_token))

        assert response.json()["transport"]["real"] is False

    async def test_corridor_comes_from_the_server(
        self, client: AsyncClient, admin_token: str, letter: MessageModel
    ) -> None:
        """Второй экземпляр границ на фронте разъехался бы с настройкой
        при первой её правке."""
        response = await client.get("/api/letters", headers=bearer(admin_token))

        corridor = response.json()["corridor"]
        assert corridor["min"] == outreach_cfg.UNIQUENESS_TARGET_MIN
        assert corridor["max"] == outreach_cfg.UNIQUENESS_TARGET_MAX

    async def test_funnel_says_where_donors_ran_out(
        self, client: AsyncClient, admin_token: str, letter: MessageModel
    ) -> None:
        response = await client.get("/api/letters", headers=bearer(admin_token))

        assert response.json()["funnel"]["подходящих"] == 1


class TestEditing:
    async def test_edit_recomputes_the_difference(
        self,
        client: AsyncClient,
        admin_token: str,
        letter: MessageModel,
        session: AsyncSession,
        filled_legal: None,
    ) -> None:
        """Число обязано поехать вместе с текстом: процент, посчитанный
        по прежнему письму, — обещание, которого нет."""
        body = (letter.body or "").replace("Good afternoon to you,", "Hi,")

        response = await client.patch(
            f"/api/letters/{letter.id}",
            json={"subject": "Rates", "body": body},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200, response.text
        assert response.json()["uniqueness"] != 0.18

    async def test_edit_refuses_metrics(
        self, client: AsyncClient, admin_token: str, letter: MessageModel
    ) -> None:
        """Запрет на метрики Ahrefs нарушается не злым умыслом, а желанием
        объяснить донору, чем он приглянулся. Платит за это ключ."""
        response = await client.patch(
            f"/api/letters/{letter.id}",
            json={"subject": "Rates", "body": "Your DR 46 site looks great"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 400
        assert "Ahrefs" in response.json()["detail"]

    async def test_empty_letter_is_refused(
        self, client: AsyncClient, admin_token: str, letter: MessageModel
    ) -> None:
        response = await client.patch(
            f"/api/letters/{letter.id}",
            json={"subject": "Rates", "body": "   "},
            headers=bearer(admin_token),
        )

        assert response.status_code == 409


class TestSkipping:
    async def test_skipped_letter_leaves_the_queue(
        self, client: AsyncClient, admin_token: str, letter: MessageModel
    ) -> None:
        await client.post(f"/api/letters/{letter.id}/skip", headers=bearer(admin_token))

        response = await client.get("/api/letters", headers=bearer(admin_token))
        assert response.json()["letters"] == []

    async def test_skipped_donor_does_not_come_back(
        self, client: AsyncClient, admin_token: str, letter: MessageModel
    ) -> None:
        """Иначе от донора пришлось бы отказываться каждую неделю заново."""
        await client.post(f"/api/letters/{letter.id}/skip", headers=bearer(admin_token))

        response = await client.get("/api/letters", headers=bearer(admin_token))
        assert response.json()["funnel"]["ещё не писали"] == 0

    async def test_skipping_twice_is_refused(
        self, client: AsyncClient, admin_token: str, letter: MessageModel
    ) -> None:
        await client.post(f"/api/letters/{letter.id}/skip", headers=bearer(admin_token))

        again = await client.post(f"/api/letters/{letter.id}/skip", headers=bearer(admin_token))

        assert again.status_code == 409


class TestSending:
    async def test_sends_through_the_null_transport(
        self,
        client: AsyncClient,
        admin_token: str,
        letter: MessageModel,
        session: AsyncSession,
        filled_legal: None,
    ) -> None:
        response = await client.post(f"/api/letters/{letter.id}/send", headers=bearer(admin_token))

        assert response.status_code == 200, response.text
        assert response.json()["real"] is False
        sent = (
            (await session.execute(select(MessageModel).where(MessageModel.id == letter.id)))
            .scalars()
            .one()
        )
        assert sent.status is MessageStatus.SENT

    async def test_unfilled_sender_name_refuses(
        self,
        client: AsyncClient,
        admin_token: str,
        letter: MessageModel,
        session: AsyncSession,
    ) -> None:
        """Проверяется текст письма, а не настройки: отправляем мы текст.

        Настройку могли заполнить после того, как письмо собрали, —
        и в письме всё равно стоит метка вместо имени отправителя.
        """
        letter.body = compose.assemble(
            compose.render(
                template.default(),
                {
                    "host": "donor.example.test",
                    "sender_name": "",
                    "postal_address": "",
                    "unsubscribe_url": "",
                },
            ),
            {},
        ).body
        await session.commit()

        response = await client.post(f"/api/letters/{letter.id}/send", headers=bearer(admin_token))

        assert response.status_code == 409
        detail = response.json()["detail"]
        assert "не задано имя отправителя" in detail
        assert "OUTREACH_" not in detail
