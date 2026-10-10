"""События доставки о чужом письме с нашим номером (аудит 10.10.2026, №8).

Номер письма уезжает в `custom_args` и возвращается в событии, но уникален он только
в нашей базе. Стенд с ключом боевой учётки платформы шлёт свои письма №17, база,
поднятая из копии, раздаёт номера заново — и отказ или жалоба по чужому письму №17
хоронили адрес нашего донора, ставили его в стоп-лист и роняли долю отказов ящика.
Теперь адрес события сверяется с получателем письма: не он — событие чужое, у него нет
последствий, а вебхук отвечает, как на событие без письма.

Получатель — тот же, кого назвала отправка: у доноров и рекламодателей — контакт письма,
у продаж — лид переписки (его знает модуль продаж, здесь он подставной). Подпись
платформы — своей парой ключей, сети нет.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, SenderStatus, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from backend.features.letters import events
from backend.features.letters.events import DeliveryEvent, apply_events
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_delivery_events import _keypair, _sign
from tests.test_sales_stage_bridge import LEAD_EMAIL, FakeSalesMail
from tests.test_sales_stage_mail import LEAD, sales_world
from tests.thread_letters import stuck_first

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
HOST = "foreign.example.test"
#: Кому ушло наше письмо.
TO = f"editor@{HOST}"
#: Кому ушло чужое письмо с тем же номером — со стенда или из базы до восстановления.
STRANGER = "someone@elsewhere.example.test"


@pytest.fixture
async def sent(session: AsyncSession) -> MessageModel:
    """Наше отправленное письмо донору: с ящиком, контактом и сроком добивки."""
    domain = await make_donor(session, HOST, email=TO)
    campaign = CampaignModel(stage=Stage.DONORS, name="Чужие события", status="running")
    box = SenderModel(
        domain="mail-f.example.test",
        email="outreach@mail-f.example.test",
        stage=Stage.DONORS,
        daily_cap=20,
        status=SenderStatus.FREE,
        enabled=True,
    )
    session.add_all([campaign, box])
    await session.flush()
    contact_id = await session.scalar(
        select(ContactModel.id).where(ContactModel.domain_id == domain.id)
    )
    letter = MessageModel(
        campaign_id=campaign.id,
        domain_id=domain.id,
        contact_id=contact_id,
        sender_id=box.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Hi",
        body="Hi",
        sent_at=NOW,
        next_action_at=NOW + timedelta(days=7),
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(letter)
    await session.flush()
    return letter


async def _contact(session: AsyncSession, letter: MessageModel) -> ContactModel:
    contact = await session.get(ContactModel, letter.contact_id)
    assert contact is not None
    return contact


async def _stopped(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(SuppressionModel)) or 0)


class TestDonors:
    @pytest.mark.parametrize("email", [TO, TO.upper(), f"  {TO} "])
    async def test_event_to_our_recipient_is_applied(
        self, session: AsyncSession, sent: MessageModel, email: str
    ) -> None:
        """Наш получатель — в любом регистре и с краевыми пробелами: событие — как раньше."""
        report = await apply_events(session, [DeliveryEvent("bounce", sent.id, email)], now=NOW)

        assert (report.bounced, report.unknown) == (1, 0)
        assert sent.status is MessageStatus.BOUNCED
        assert (await _contact(session, sent)).verification_status == "bounced"

    async def test_foreign_bounce_changes_nothing(
        self, session: AsyncSession, sent: MessageModel, caplog: pytest.LogCaptureFixture
    ) -> None:
        caplog.set_level(logging.WARNING, logger=events.__name__)
        before = (await _contact(session, sent)).verification_status
        bounce = DeliveryEvent("bounce", sent.id, STRANGER, reason="550 no user", event_id="sg-f1")

        report = await apply_events(session, [bounce], now=NOW)

        assert (report.bounced, report.unknown) == (0, 1)
        assert sent.status is MessageStatus.SENT
        assert (sent.failure_reason, sent.next_action_at) == (None, NOW + timedelta(days=7))
        assert (await _contact(session, sent)).verification_status == before
        # Пропуск виден в журнале и ищется по полям: письмо, событие, чей адрес.
        [said] = [r for r in caplog.records if "о чужом письме" in r.getMessage()]
        fields = {key: vars(said)[key] for key in ("letter", "event", "event_email", "event_id")}
        assert fields == {
            "letter": sent.id,
            "event": "bounce",
            "event_email": STRANGER,
            "event_id": "sg-f1",
        }

    async def test_foreign_complaint_does_not_stop_the_donor(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        report = await apply_events(
            session, [DeliveryEvent("spamreport", sent.id, STRANGER)], now=NOW
        )

        assert (report.complained, report.unknown) == (0, 1)
        assert await _stopped(session) == 0
        assert sent.next_action_at == NOW + timedelta(days=7)

    async def test_foreign_bounce_does_not_park_the_box(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Ящик на грани парковки (10 отказов из 100): наш отказ паркует его, чужой — нет."""
        for number in range(99):
            session.add(
                MessageModel(
                    campaign_id=sent.campaign_id,
                    domain_id=sent.domain_id,
                    sender_id=sent.sender_id,
                    step=0,
                    status=MessageStatus.BOUNCED if number < 10 else MessageStatus.DELIVERED,
                    sent_at=NOW,
                    idempotency_key=f"donors:foreign-fill-{number}:0",
                )
            )
        await session.flush()

        report = await apply_events(session, [DeliveryEvent("bounce", sent.id, STRANGER)], now=NOW)

        box = await session.get(SenderModel, sent.sender_id)
        assert box is not None
        assert (report.paused_domains, box.enabled, box.pause_reason) == ([], True, None)

    async def test_letter_without_a_contact_is_trusted_by_its_number(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Контакт письма удалён — получателя не узнать: событие идёт, как шло до сверки,
        иначе отказы наших же писем терялись бы."""
        sent.contact_id = None
        await session.flush()

        report = await apply_events(session, [DeliveryEvent("bounce", sent.id, STRANGER)], now=NOW)

        assert (report.bounced, report.unknown) == (1, 0)
        assert sent.status is MessageStatus.BOUNCED


async def test_foreign_event_does_not_settle_a_stuck_letter(
    session: AsyncSession, filled_legal: None
) -> None:
    """Письмо в «отправляется»: событие доказывает, что платформа его приняла, — но только
    наше. Чужое «processed» с тем же номером исход не решает."""
    letter = await stuck_first(session, since=NOW - timedelta(minutes=12))

    report = await apply_events(session, [DeliveryEvent("processed", letter.id, STRANGER)], now=NOW)

    assert (report.resolved, report.unknown) == (0, 1)
    assert letter.status is MessageStatus.SENDING


async def test_route_answers_a_foreign_event_like_one_without_a_letter(
    client: AsyncClient,
    session: AsyncSession,
    sent: MessageModel,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Вебхук: 200 и «принято», чужое событие — в «без письма»; наше в той же пачке идёт."""
    private, public = _keypair()
    monkeypatch.setattr("backend.config.outreach.EVENTS_PUBLIC_KEY", public)
    batch = [
        {"event": "bounce", "message_id": str(sent.id), "email": STRANGER},
        {"event": "delivered", "message_id": str(sent.id), "email": TO},
    ]
    payload = json.dumps(batch).encode()
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
    said = response.json()
    assert (said["accepted"], said["unknown"], said["bounced"], said["delivered"]) == (
        True,
        1,
        0,
        1,
    )
    await session.refresh(sent)
    assert sent.status is MessageStatus.DELIVERED


@dataclass
class LeadMail(FakeSalesMail):
    """Подставной модуль продаж, который знает адрес лида: переписки адресата — только его."""

    async def threads_to(self, session: AsyncSession, email: str) -> tuple[int, ...]:
        self.asked.append(f"threads_to {email}")
        return self.threads if email.strip().lower() == LEAD_EMAIL else ()


@pytest.mark.parametrize(
    ("email", "ours"),
    [(LEAD_EMAIL.upper(), True), (f"ceo@{LEAD}", False), (STRANGER, False)],
    ids=["lead", "letter-contact", "stranger"],
)
async def test_sales_letter_is_matched_by_its_lead(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, email: str, ours: bool
) -> None:
    """Письмо продаж уходит лиду переписки, а не контакту письма: свой ли адрес события,
    отвечает модуль продаж мостом почты. Контакт письма (`ceo@`) — не получатель."""
    world = await sales_world(session, status=MessageStatus.SENT)
    found = LeadMail(threads=(world.thread.id,))
    monkeypatch.setattr(stages._SALES, "load", None)
    stages.register_sales(lambda: found)

    report = await apply_events(session, [DeliveryEvent("bounce", world.letter.id, email)], now=NOW)

    assert (report.bounced, report.unknown) == ((1, 0) if ours else (0, 1))
    assert world.letter.status is (MessageStatus.BOUNCED if ours else MessageStatus.SENT)
    assert found.asked == [f"threads_to {email}"]
