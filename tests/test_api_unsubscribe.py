"""Страница отписки: что она показывает и когда пишет.

Вторая и последняя ручка наружу без пропуска, и проверяется она как
таковая: переход ничего не меняет, пишет только нажатие, подобранная
метка не отписывает никого, а частота ограничена.
"""

from __future__ import annotations

import pytest
from backend.api.unsubscribe import routes as unsubscribe_routes
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel
from backend.features.letters import unsubscribe
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

SECRET = "u" * 32
HOST = "donor.example.test"
CLIENT = "127.0.0.1"


@pytest.fixture(autouse=True)
def page_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    monkeypatch.setattr(outreach_cfg, "SENDER_NAME", "Anna Ro")
    monkeypatch.setattr(outreach_cfg, "POSTAL_ADDRESS", "1 Main Street, Dublin")
    # Счётчик частоты живёт в памяти процесса и общий на все тесты:
    # без очистки десятый тест ловил бы отказ от девятого. Адрес тот,
    # каким видит клиента сервер, — не имя транспорта.
    unsubscribe_routes._throttle.clear(CLIENT)


@pytest.fixture
async def queued(session: AsyncSession) -> MessageModel:
    """Донор, которому письмо уже стоит в очереди."""
    domain = await make_donor(session, HOST, email=f"editor@{HOST}")
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add(campaign)
    await session.flush()

    message = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain.id,
        step=0,
        status=MessageStatus.QUEUED,
        subject="Hi",
        body="Hi",
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(message)
    await session.commit()
    return message


def link(domain_id: int) -> str:
    return "/api/unsubscribe/" + unsubscribe.label_for(domain_id, secret=SECRET)


class TestOpening:
    async def test_page_names_the_site(self, client: AsyncClient, queued: MessageModel) -> None:
        response = await client.get(link(queued.domain_id))

        assert response.status_code == 200
        assert HOST in response.text
        assert response.headers["content-type"].startswith("text/html")

    async def test_opening_changes_nothing(
        self, client: AsyncClient, session: AsyncSession, queued: MessageModel
    ) -> None:
        """По ссылкам в письмах ходят сканеры почты и предпросмотры:
        отписка на переходе сработала бы за донора, который её не нажимал."""
        await client.get(link(queued.domain_id))

        rows = (await session.execute(select(SuppressionModel))).scalars().all()
        assert rows == []

    async def test_signature_of_the_sender_is_shown(
        self, client: AsyncClient, queued: MessageModel
    ) -> None:
        """Страница без имени отправителя выглядит чужой."""
        response = await client.get(link(queued.domain_id))

        assert "Anna Ro" in response.text
        assert "1 Main Street, Dublin" in response.text

    async def test_forged_label_says_nothing(
        self, client: AsyncClient, queued: MessageModel
    ) -> None:
        forged = f"/api/unsubscribe/u{queued.domain_id}." + "0" * 10

        response = await client.get(forged)

        assert response.status_code == 404
        assert HOST not in response.text


class TestPressing:
    async def test_button_writes_the_stop_list(
        self, client: AsyncClient, session: AsyncSession, queued: MessageModel
    ) -> None:
        response = await client.post(link(queued.domain_id))

        assert response.status_code == 200
        assert "unsubscribed" in response.text.lower()
        rows = (await session.execute(select(SuppressionModel))).scalars().all()
        assert [(row.domain_id, row.created_by) for row in rows] == [
            (queued.domain_id, unsubscribe_routes.SOURCE)
        ]

    async def test_queued_letter_is_taken_off(
        self, client: AsyncClient, session: AsyncSession, queued: MessageModel
    ) -> None:
        await client.post(link(queued.domain_id))

        message = await session.get(MessageModel, queued.id)
        assert message is not None
        await session.refresh(message)
        assert message.status is MessageStatus.STOPPED

    async def test_second_press_is_the_same_page(
        self, client: AsyncClient, session: AsyncSession, queued: MessageModel
    ) -> None:
        """Один клик из почтового клиента и нажатие на странице приходят
        сюда же: донору разница не видна, в базе второй строки нет."""
        first = await client.post(link(queued.domain_id))
        second = await client.post(link(queued.domain_id))

        assert first.status_code == second.status_code == 200
        assert first.text == second.text
        rows = (await session.execute(select(SuppressionModel))).scalars().all()
        assert len(rows) == 1

    async def test_forged_label_writes_nothing(
        self, client: AsyncClient, session: AsyncSession, queued: MessageModel
    ) -> None:
        response = await client.post(f"/api/unsubscribe/u{queued.domain_id}." + "f" * 10)

        assert response.status_code == 404
        rows = (await session.execute(select(SuppressionModel))).scalars().all()
        assert rows == []

    async def test_deleted_donor_does_not_break_the_page(
        self, client: AsyncClient, queued: MessageModel
    ) -> None:
        response = await client.post(link(queued.domain_id + 1000))

        assert response.status_code == 404


class TestDefence:
    async def test_too_many_tries_are_refused(
        self, client: AsyncClient, queued: MessageModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Метку подбирают перебором, и перебор должен упираться."""
        monkeypatch.setattr(unsubscribe_routes, "RATE_PER_MINUTE", 2)

        codes = [(await client.get(link(queued.domain_id))).status_code for _ in range(3)]

        assert codes == [200, 200, 429]

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        """Раздел без пропуска — это раздел, у которого защита своя,
        и список его маршрутов должен быть виден целиком."""
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/unsubscribe")
            for method in methods
        }

        assert in_app == {
            ("GET", "/api/unsubscribe/{label}"),
            ("POST", "/api/unsubscribe/{label}"),
        }
