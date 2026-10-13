"""Отказ на письмо продаж хоронит адрес, который отказал, а не контакт письма (ревью стыков
12.10.2026).

Письмо продаж уходит адресу лида переписки, а контакт письма — тот, кто ответил: секретарь,
второй ящик компании. Адрес лида при этом — та же строка `contacts`, что у доноров (лид сбора
ссылается на лучший адрес домена), и отказ её не хоронил: доноры потом брали мёртвый адрес
первым. Теперь событие отказа хоронит строки с адресом события, приём ответов — адрес из
отчёта почты (`X-Failed-Recipients`) или ящик автоответа «больше не читается», если это лид;
адреса не узнать — не хоронится ничего. И «пишите на jane@» в таком автоответе в общие
контакты домена не ложится: лида заводят продажи. Модуль продаж здесь подставной.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.contacts.preference import DEAD
from backend.features.core import stages
from backend.features.core.domain import ContactSource, MessageStatus, ReplyKind
from backend.features.core.models.donor import ContactModel
from backend.features.letters import reply_to
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.replies import classify, redirect
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor
from tests.test_delivery_events_foreign import LeadMail
from tests.test_sales_stage_bridge import LEAD_EMAIL
from tests.test_sales_stage_mail import LEAD, World, sales_world

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
SECRET = "d" * 32
#: Ящик мира продаж (`sales_world`): с него ушло письмо, на него — метка ответа.
BOX = "sales@mail-sales.example.test"
AUTO = {"Auto-Submitted": "auto-replied"}


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> World:
    """Ушедшее письмо продаж лиду `LEAD_EMAIL`; контакт письма — `ceo@`, другой человек.
    Адрес лида — строка `contacts` и этого сайта, и другого (в своём регистре)."""
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    found = await sales_world(session, status=MessageStatus.SENT)
    other = await make_donor(session, "other-site.example.test")
    session.add_all(
        [
            ContactModel(
                domain_id=found.letter.domain_id, email=LEAD_EMAIL, source=ContactSource.PAGE
            ),
            ContactModel(
                domain_id=other.id, email="Jane@Lead.example.test", source=ContactSource.PAGE
            ),
        ]
    )
    await session.flush()
    monkeypatch.setattr(stages._SALES, "load", None)
    stages.register_sales(lambda: LeadMail(threads=(found.thread.id,)))
    return found


async def _marks(session: AsyncSession, email: str) -> list[tuple[str | None, int | None]]:
    """Отметки строк `contacts` с этим адресом — без учёта регистра, по порядку заведения."""
    rows = await session.execute(
        select(ContactModel.verification_status, ContactModel.verification_score)
        .where(func.lower(ContactModel.email) == email.lower())
        .order_by(ContactModel.id)
    )
    return [(status, score) for status, score in rows.tuples().all()]


async def _letter_contact(session: AsyncSession, world: World) -> ContactModel:
    contact = await session.get(ContactModel, world.letter.contact_id)
    assert contact is not None
    return contact


def _to(world: World) -> tuple[str, ...]:
    return (reply_to.address_for(world.letter.id, sender_email=BOX, secret=SECRET),)


class TestDeliveryEvent:
    async def test_a_hard_bounce_buries_the_address_of_the_event_everywhere(
        self, session: AsyncSession, world: World
    ) -> None:
        report = await apply_events(
            session,
            [DeliveryEvent("bounce", world.letter.id, LEAD_EMAIL.upper(), reason="550 no user")],
            now=NOW,
        )

        assert report.bounced == 1
        assert await _marks(session, LEAD_EMAIL) == [(DEAD, 0), (DEAD, 0)]
        assert (await _letter_contact(session, world)).verification_status != DEAD

    async def test_a_soft_bounce_buries_nothing(self, session: AsyncSession, world: World) -> None:
        await apply_events(
            session, [DeliveryEvent("bounce", world.letter.id, LEAD_EMAIL, soft=True)], now=NOW
        )

        assert all(status != DEAD for status, _ in await _marks(session, LEAD_EMAIL))
        assert (await _letter_contact(session, world)).verification_status != DEAD


class TestReply:
    async def test_a_delivery_report_buries_the_failed_address_not_the_letter_contact(
        self, session: AsyncSession, world: World
    ) -> None:
        got = await Inbox(session, now=NOW).accept(
            Incoming(
                message_id=f"<ndr-1@mx.{LEAD}>",
                to=_to(world),
                from_email=f"mailer-daemon@mx.{LEAD}",
                subject="Undelivered Mail Returned to Sender",
                text=f"550 5.1.1 <{LEAD_EMAIL}>: user unknown",
                headers={"X-Failed-Recipients": LEAD_EMAIL},
            )
        )
        await session.flush()

        assert (got.kind, got.bound) == (ReplyKind.BOUNCE, True)
        assert await _marks(session, LEAD_EMAIL) == [(DEAD, 0), (DEAD, 0)]
        assert (await _letter_contact(session, world)).verification_status != DEAD
        assert world.letter.status is MessageStatus.BOUNCED

    async def test_a_dead_mailbox_of_the_lead_buries_the_lead_address(
        self, session: AsyncSession, world: World
    ) -> None:
        got = await Inbox(session, now=NOW).accept(
            Incoming(
                message_id=f"<auto-1@{LEAD}>",
                to=_to(world),
                from_email=LEAD_EMAIL,
                subject="Automatic reply: A question about your team",
                text="This mailbox is no longer monitored.",
                headers=AUTO,
            )
        )
        await session.flush()

        assert got.rule == classify.DEAD_MAILBOX
        assert await _marks(session, LEAD_EMAIL) == [(DEAD, 0), (DEAD, 0)]
        assert (await _letter_contact(session, world)).verification_status != DEAD

    async def test_without_the_failed_address_nothing_is_buried(
        self, session: AsyncSession, world: World, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Отчёт почты без `X-Failed-Recipients`: адрес в его тексте каждая почта пишет
        по-своему — не хороним никого, ни лида, ни контакт письма, и говорим это журналу."""
        with caplog.at_level(logging.INFO, logger="backend.features.replies"):
            got = await Inbox(session, now=NOW).accept(
                Incoming(
                    message_id=f"<ndr-2@mx.{LEAD}>",
                    to=_to(world),
                    from_email=f"mailer-daemon@mx.{LEAD}",
                    subject="Delivery Status Notification (Failure)",
                    text="550 5.1.1 user unknown",
                    headers={"Content-Type": "multipart/report; report-type=delivery-status"},
                )
            )
        await session.flush()

        assert got.kind is ReplyKind.BOUNCE
        assert all(status != DEAD for status, _ in await _marks(session, LEAD_EMAIL))
        assert (await _letter_contact(session, world)).verification_status != DEAD
        assert "адреса, который отказал, не узнать" in caplog.text

    async def test_the_named_address_does_not_go_to_the_shared_contacts(
        self, session: AsyncSession, world: World
    ) -> None:
        """«Пишите на editor@» в автоответе на письмо продаж — новый лид, его заводят продажи:
        в общие контакты домена адрес не ложится (иначе стал бы первым у доноров)."""
        got = await Inbox(session, now=NOW).accept(
            Incoming(
                message_id=f"<auto-2@{LEAD}>",
                to=_to(world),
                from_email=LEAD_EMAIL,
                subject="Automatic reply: A question about your team",
                text=f"This mailbox is no longer monitored. Please write to editor@{LEAD}.",
                headers=AUTO,
            )
        )
        await session.flush()

        assert got.rule == classify.DEAD_MAILBOX
        assert got.forwarding == redirect.Redirect()
        known = await session.scalars(
            select(ContactModel.email).where(ContactModel.domain_id == world.letter.domain_id)
        )
        assert f"editor@{LEAD}" not in known.all()
