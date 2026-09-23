"""Вебхук приёма: кто пускается и что он отвечает.

Это единственная ручка наружу без пропуска, и проверяется она как таковая:
секрет заголовком, постоянное по времени сравнение, потолок частоты
и — отдельно — то, какие коды ответа она отдаёт. Платформа повторяет
доставку на любой не-2xx, и отказ на письме, которое мы всё равно
не разберём, превращается в бесконечный поток повторов.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from backend.api.inbound import routes as inbound_routes
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    Stage,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters import reply_to
from backend.shared.sliding_window import SlidingWindow
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
SECRET = "s" * 32
HOST = "donor.example.test"

HEADERS_BLOB = (
    "Message-ID: <in-1@site.test>\n"
    "In-Reply-To: <ours-1@mail.test>\n"
    "Return-Path: <editor@donor.example.test>"
)


class NoQueue:
    """Очередь, которая ничего не ставит: тест проверяет приём, а не rq."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, job: str, *args: Any, **_: Any) -> object:
        self.jobs.append((job, args))
        return type("Job", (), {"id": "job-1"})()


@pytest.fixture(autouse=True)
def inbound_setup(monkeypatch: pytest.MonkeyPatch) -> NoQueue:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    monkeypatch.setattr(outreach_cfg, "REPLY_DOMAIN", "replies.ours.test")
    queue = NoQueue()
    monkeypatch.setattr(inbound_routes, "runs_queue", lambda: queue)
    # Счётчик частоты живёт в памяти процесса и общий на все тесты:
    # без свежего окна десятый тест ловил бы отказ от девятого. Чистить
    # по ключу нельзя: адрес клиента зависит от транспорта тестов
    # (`testclient` у одного, `127.0.0.1` у другого) — очистка по чужому
    # ключу молча ничего не делала.
    monkeypatch.setattr(inbound_routes, "_throttle", SlidingWindow())
    return queue


@pytest.fixture
async def sent(session: AsyncSession) -> MessageModel:
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
        provider_message_id="<ours-1@mail.test>",
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(message)
    await session.commit()
    return message


def form_for(message: MessageModel, text: str, **extra: str) -> dict[str, str]:
    label = reply_to.address_for(
        message.id,
        sender_email="anna@mail.test",
        reply_domain="replies.ours.test",
        secret=SECRET,
    )
    payload = {
        "from": f"Elena <editor@{HOST}>",
        "to": label,
        "subject": "Re: Advertising rates",
        "text": text,
        "headers": HEADERS_BLOB,
    }
    payload.update(extra)
    return payload


def secret_header(value: str = SECRET) -> dict[str, str]:
    return {"X-Inbound-Secret": value}


class TestWhoIsLetIn:
    async def test_without_the_secret_nobody(self, client: AsyncClient, sent: MessageModel) -> None:
        response = await client.post("/api/inbound/replies", data=form_for(sent, "250 EUR"))

        assert response.status_code == 403

    async def test_wrong_secret_is_refused(self, client: AsyncClient, sent: MessageModel) -> None:
        response = await client.post(
            "/api/inbound/replies",
            data=form_for(sent, "250 EUR"),
            headers=secret_header("wrong-secret"),
        )

        assert response.status_code == 403

    async def test_secret_as_basic_auth_password_is_let_in(
        self, client: AsyncClient, sent: MessageModel
    ) -> None:
        """Платформа без своих заголовков: секрет паролем в адресе вебхука."""
        response = await client.post(
            "/api/inbound/replies", data=form_for(sent, "250 EUR"), auth=("inbound", SECRET)
        )

        assert response.status_code == 200

    async def test_wrong_basic_auth_password_is_refused(
        self, client: AsyncClient, sent: MessageModel
    ) -> None:
        for auth in (("inbound", "wrong-secret"), (SECRET, "")):
            response = await client.post(
                "/api/inbound/replies", data=form_for(sent, "250 EUR"), auth=auth
            )
            assert response.status_code == 403, auth

    async def test_missing_secret_in_settings_refuses_everyone(
        self, client: AsyncClient, sent: MessageModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Без секрета ручку наружу может дёрнуть кто угодно — значит
        не принимаем вовсе, а не принимаем всех."""
        monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", "")

        response = await client.post(
            "/api/inbound/replies", data=form_for(sent, "250 EUR"), headers=secret_header()
        )

        assert response.status_code == 403
        assert "OUTREACH_INBOUND_SECRET" in response.json()["reason"]

    async def test_too_many_letters_are_throttled(
        self, client: AsyncClient, sent: MessageModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(inbound_routes, "RATE_PER_MINUTE", 2)

        codes = []
        for number in range(4):
            response = await client.post(
                "/api/inbound/replies",
                data=form_for(sent, "250 EUR", headers=f"Message-ID: <in-{number}@site.test>"),
                headers=secret_header(),
            )
            codes.append(response.status_code)

        assert codes[:2] == [200, 200]
        assert codes[2] == 403

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        """Раздел без пропуска — это раздел, у которого защита своя,
        и список его маршрутов должен быть виден целиком."""
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/inbound")
            for method in methods
        }

        assert in_app == {("POST", "/api/inbound/replies")}


class TestTaking:
    async def test_reply_is_taken_and_bound(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        response = await client.post(
            "/api/inbound/replies",
            data=form_for(sent, "Placement is 250 EUR."),
            headers=secret_header(),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["accepted"]
        assert body["bound"]
        assert body["kind"] == "human"
        reply = (await session.execute(select(ReplyModel))).scalars().one()
        assert reply.thread_id == sent.thread_id

    async def test_parsing_goes_to_the_queue(
        self, client: AsyncClient, sent: MessageModel, inbound_setup: NoQueue
    ) -> None:
        """Вызов модели идёт секундами, а платформа повторяет вебхук
        по таймауту: платный разбор внутри запроса означал бы повторные
        списания."""
        await client.post(
            "/api/inbound/replies",
            data=form_for(sent, "Placement is 250 EUR."),
            headers=secret_header(),
        )

        assert [job for job, _ in inbound_setup.jobs] == ["backend.workers.jobs.parse_reply"]

    async def test_repeat_answers_200_and_does_not_double(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        payload = form_for(sent, "Placement is 250 EUR.")
        await client.post("/api/inbound/replies", data=payload, headers=secret_header())

        again = await client.post("/api/inbound/replies", data=payload, headers=secret_header())

        assert again.status_code == 200
        assert again.json()["duplicate"]
        rows = await session.execute(select(ReplyModel))
        assert len(rows.scalars().all()) == 1

    async def test_unbound_letter_still_gets_200(
        self, client: AsyncClient, sent: MessageModel, session: AsyncSession
    ) -> None:
        """Отказ на письме, которое мы всё равно не разберём, превращается
        в бесконечный поток повторов от платформы."""
        payload = form_for(sent, "Hello?", to="info@ours.test", headers="Message-ID: <x@y.test>")

        response = await client.post("/api/inbound/replies", data=payload, headers=secret_header())

        assert response.status_code == 200
        assert not response.json()["bound"]
        assert response.json()["needs_review"]

    async def test_letter_without_a_sender_is_not_retried(
        self, client: AsyncClient, sent: MessageModel
    ) -> None:
        payload = form_for(sent, "Hello?")
        payload["from"] = ""

        response = await client.post("/api/inbound/replies", data=payload, headers=secret_header())

        assert response.status_code == 200
        assert not response.json()["accepted"]
        assert "отправител" in response.json()["reason"]

    async def test_auto_reply_is_taken_but_not_parsed(
        self, client: AsyncClient, sent: MessageModel, inbound_setup: NoQueue
    ) -> None:
        response = await client.post(
            "/api/inbound/replies",
            data=form_for(sent, "I am out of the office until Monday."),
            headers=secret_header(),
        )

        assert response.json()["kind"] == "auto_reply"
        assert inbound_setup.jobs == []
