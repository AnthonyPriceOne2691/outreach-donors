"""События доставки: подпись, монотонность, последствия, парковка.

Это ручка, чужой запрос к которой останавливает нашу рассылку:
событие о недоставке двигает статус письма, а серия таких снимает
домен с отправки. Поэтому проверяется и то, что она делает, и то,
чего она не делает по чужой просьбе.
"""

from __future__ import annotations

import base64
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core.domain import (
    MessageStatus,
    SenderStatus,
    Stage,
    SuppressionReason,
)
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderModel,
)
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.shared.webhook_signature import SignatureError, verify
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
HOST = "donor.example.test"


# --- подпись ---


def _keypair() -> tuple[ec.EllipticCurvePrivateKey, str]:
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, base64.b64encode(public).decode()


def _sign(private: ec.EllipticCurvePrivateKey, payload: bytes, timestamp: str) -> str:
    raw = private.sign(timestamp.encode() + payload, ec.ECDSA(hashes.SHA256()))
    return base64.b64encode(raw).decode()


class TestTheSignature:
    def test_real_signature_passes(self) -> None:
        private, public = _keypair()
        payload, stamp = b'[{"event":"delivered"}]', str(int(time.time()))

        verify(
            payload=payload,
            timestamp=stamp,
            signature=_sign(private, payload, stamp),
            public_key=public,
        )

    def test_changed_body_fails(self) -> None:
        """Подпись считается по телу: подменённое событие не пройдёт."""
        private, public = _keypair()
        stamp = str(int(time.time()))
        signature = _sign(private, b'[{"event":"delivered"}]', stamp)

        with pytest.raises(SignatureError, match="не сошлась"):
            verify(
                payload=b'[{"event":"bounce"}]',
                timestamp=stamp,
                signature=signature,
                public_key=public,
            )

    def test_someone_elses_key_fails(self) -> None:
        private, _ = _keypair()
        _, stranger = _keypair()
        payload, stamp = b"[]", str(int(time.time()))

        with pytest.raises(SignatureError):
            verify(
                payload=payload,
                timestamp=stamp,
                signature=_sign(private, payload, stamp),
                public_key=stranger,
            )

    def test_yesterdays_event_is_refused(self) -> None:
        """Подпись настоящая, но повтор такого возраста прислали не сейчас."""
        private, public = _keypair()
        stamp = str(int(time.time()) - 60 * 60 * 30)
        payload = b"[]"

        with pytest.raises(SignatureError, match="старше"):
            verify(
                payload=payload,
                timestamp=stamp,
                signature=_sign(private, payload, stamp),
                public_key=public,
            )

    def test_without_a_key_nothing_passes(self) -> None:
        """Ручка паркует домены и пишет в стоп-лист: без ключа она
        открыта любому, и это отказ, а не умолчание."""
        with pytest.raises(SignatureError, match="OUTREACH_EVENTS_PUBLIC_KEY"):
            verify(payload=b"[]", timestamp="1", signature="x", public_key="")


# --- последствия ---


@pytest.fixture
async def sent(session: AsyncSession) -> MessageModel:
    """Отправленное письмо с ящиком и контактом."""
    domain = await make_donor(session, HOST, email=f"editor@{HOST}")
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    sender = SenderModel(
        domain="mail-a.example",
        email="outreach@mail-a.example",
        stage=Stage.DONORS,
        daily_cap=20,
        status=SenderStatus.FREE,
        enabled=True,
    )
    session.add_all([campaign, sender])
    await session.flush()

    contact = (
        (await session.execute(select(ContactModel).where(ContactModel.domain_id == domain.id)))
        .scalars()
        .one()
    )
    message = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain.id,
        contact_id=contact.id,
        sender_id=sender.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Hi",
        body="Hi",
        sent_at=NOW,
        next_action_at=NOW + timedelta(days=7),
        provider_message_id="sg-1",
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(message)
    await session.flush()
    return message


class TestWhatEventsDo:
    async def test_delivered_marks_the_time(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        report = await apply_events(
            session, [DeliveryEvent("delivered", sent.id, f"editor@{HOST}")], now=NOW
        )

        assert report.delivered == 1
        assert sent.status is MessageStatus.DELIVERED
        assert sent.delivered_at == NOW

    async def test_late_delivered_does_not_undo_a_bounce(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """События приходят не по порядку. Запоздавший «доставлено»
        поверх недоставки означал бы письмо, которое одновременно
        дошло и не дошло."""
        await apply_events(session, [DeliveryEvent("bounce", sent.id, "x@y.z")], now=NOW)

        await apply_events(session, [DeliveryEvent("delivered", sent.id, "x@y.z")], now=NOW)

        assert sent.status is MessageStatus.BOUNCED

    async def test_repeat_does_not_double_the_count(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Платформа доставляет события «хотя бы один раз»."""
        one = DeliveryEvent("delivered", sent.id, "x@y.z")

        first = await apply_events(session, [one], now=NOW)
        second = await apply_events(session, [one], now=NOW)

        assert (first.delivered, second.delivered) == (1, 0)

    async def test_hard_bounce_buries_the_address(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Адреса нет — открывается следующий адрес донора."""
        await apply_events(
            session,
            [DeliveryEvent("bounce", sent.id, "x@y.z", reason="550 no such user")],
            now=NOW,
        )

        contact = await session.get(ContactModel, sent.contact_id)
        assert contact is not None
        assert contact.verification_status == "bounced"
        assert sent.failure_reason == "550 no such user"
        assert sent.next_action_at is None

    async def test_soft_bounce_leaves_the_address_alive(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Ящик переполнен — завтра письмо уйдёт. Хоронить адрес нельзя."""
        await apply_events(
            session,
            [DeliveryEvent("bounce", sent.id, "x@y.z", reason="mailbox full", soft=True)],
            now=NOW,
        )

        contact = await session.get(ContactModel, sent.contact_id)
        assert contact is not None
        assert contact.verification_status != "bounced"

    async def test_spam_report_closes_the_donor(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Второе письмо пожаловавшемуся — вторая жалоба и выгоревший домен."""
        report = await apply_events(
            session, [DeliveryEvent("spamreport", sent.id, f"editor@{HOST}")], now=NOW
        )

        assert report.complained == 1
        rows = (await session.execute(select(SuppressionModel))).scalars().all()
        assert [(row.domain_id, row.reason) for row in rows] == [
            (sent.domain_id, SuppressionReason.COMPLAINED)
        ]

    async def test_event_without_our_number_is_counted_not_lost(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Платформа шлёт события и по письмам, которых у нас нет."""
        report = await apply_events(session, [DeliveryEvent("delivered", None, "x@y.z")], now=NOW)

        assert report.unknown == 1


class TestParking:
    """Домен с высокой долей отказов снимается с отправки. Доля, а не
    число: один отказ из двух писем — это 50% и ничего не значит."""

    async def _fill(
        self, session: AsyncSession, sent: MessageModel, *, total: int, bounced: int
    ) -> None:
        for number in range(total):
            session.add(
                MessageModel(
                    campaign_id=sent.campaign_id,
                    domain_id=sent.domain_id,
                    sender_id=sent.sender_id,
                    step=0,
                    status=(MessageStatus.BOUNCED if number < bounced else MessageStatus.DELIVERED),
                    subject="Hi",
                    body="Hi",
                    sent_at=NOW,
                    idempotency_key=f"donors:fill-{number}:0",
                )
            )
        await session.flush()

    async def test_few_letters_do_not_park_anyone(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        await self._fill(session, sent, total=5, bounced=5)

        report = await apply_events(session, [DeliveryEvent("bounce", sent.id, "x@y.z")], now=NOW)

        assert report.paused_domains == []
        sender = await session.get(SenderModel, sent.sender_id)
        assert sender is not None
        assert sender.enabled

    async def test_high_share_parks_the_box(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        await self._fill(session, sent, total=99, bounced=10)

        report = await apply_events(session, [DeliveryEvent("bounce", sent.id, "x@y.z")], now=NOW)

        assert report.paused_domains == ["outreach@mail-a.example"]
        sender = await session.get(SenderModel, sent.sender_id)
        assert sender is not None
        assert not sender.enabled
        assert sender.pause_reason is not None
        assert "отказов" in sender.pause_reason


# --- ручка ---


class TestTheRoute:
    async def test_signed_events_are_applied(
        self,
        client: AsyncClient,
        session: AsyncSession,
        sent: MessageModel,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        private, public = _keypair()
        monkeypatch.setattr("backend.config.outreach.EVENTS_PUBLIC_KEY", public)
        payload = json.dumps([{"event": "delivered", "message_id": str(sent.id)}]).encode()
        stamp = str(int(time.time()))

        response = await client.post(
            "/api/events/delivery",
            content=payload,
            headers={
                "X-Twilio-Email-Event-Webhook-Signature": _sign(private, payload, stamp),
                "X-Twilio-Email-Event-Webhook-Timestamp": stamp,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200, response.text
        assert response.json()["delivered"] == 1

    async def test_unsigned_is_refused(
        self, client: AsyncClient, sent: MessageModel, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _, public = _keypair()
        monkeypatch.setattr("backend.config.outreach.EVENTS_PUBLIC_KEY", public)

        response = await client.post(
            "/api/events/delivery",
            content=b'[{"event":"bounce","message_id":"1"}]',
            headers={"Content-Type": "application/json"},
        )

        assert response.status_code == 403
        assert sent.status is MessageStatus.SENT
