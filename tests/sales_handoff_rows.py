"""Строки базы для тестов передачи лида продаж: диалог с лидом, письмом и ответом.

Этапа продаж у рассылки ещё нет (срез 1.1b), поэтому рассылка диалога — Этапа 2:
передаче этап не важен, ей нужен лид, найденный по домену и адресу диалога.
Тексты и адреса выдуманные (`*.example.test`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.features.core.domain import ContactSource, MessageStatus, ReplyKind, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
)
from sqlalchemy.ext.asyncio import AsyncSession

#: Когда ушло первое письмо — некруглое, заведомо выдуманное время.
SENT_AT = datetime(2026, 10, 13, 9, 47, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Dialog:
    thread: ThreadModel
    lead: SalesLeadModel
    contact: ContactModel
    message: MessageModel
    reply: ReplyModel


async def sales_dialog(
    session: AsyncSession,
    *,
    host: str = "acme.example.test",
    email: str = "ivan@acme.example.test",
    text: str = "Давайте созвонимся во вторник после обеда.",
) -> Dialog:
    """Лид продаж, первое письмо ему и ответ «давайте созвонимся»."""
    domain = DomainModel(host=host)
    hypothesis = SalesHypothesisModel(name=f"гипотеза {host}")
    campaign = CampaignModel(stage=Stage.ADVERTISERS, name=f"продажи {host}")
    session.add_all([domain, hypothesis, campaign])
    await session.flush()
    contact = ContactModel(domain_id=domain.id, email=email, source=ContactSource.MANUAL)
    session.add(contact)
    await session.flush()
    lead = SalesLeadModel(
        hypothesis_id=hypothesis.id,
        domain_id=domain.id,
        contact_id=contact.id,
        email=email,
        name="Иван Примеров",
        company="Акме Тест",
        country="de",
        language="ru",
        source=LeadSource.IMPORT,
        status=LeadStatus.READY,
    )
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add_all([lead, thread])
    await session.flush()
    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Кто у вас отвечает за SEO?",
        sent_at=SENT_AT,
        idempotency_key=f"test:{host}:{email}:0",
    )
    session.add(message)
    await session.flush()
    reply = await answer(
        session, thread, message, text, at=SENT_AT + timedelta(hours=26), sender=email
    )
    return Dialog(thread, lead, contact, message, reply)


async def answer(
    session: AsyncSession,
    thread: ThreadModel,
    message: MessageModel,
    text: str,
    *,
    at: datetime,
    sender: str = "ivan@acme.example.test",
) -> ReplyModel:
    """Ещё один ответ человека в тот же диалог."""
    reply = ReplyModel(
        thread_id=thread.id,
        message_id=message.id,
        kind=ReplyKind.HUMAN,
        raw_body=text,
        from_email=sender,
        subject="Re: Кто у вас отвечает за SEO?",
        created_at=at,
    )
    session.add(reply)
    await session.flush()
    return reply
