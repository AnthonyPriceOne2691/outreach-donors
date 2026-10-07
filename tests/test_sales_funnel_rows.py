"""Строки базы для тестов воронки продаж (срез 5.4): гипотеза, лид, его диалог продаж, письма
цепочки, ответы, передача — прямо в базу, без сборки и отправки.

Время — от некруглого выдуманного «сейчас» (`NOW`), не дата-обязательство. Тексты и адреса
выдуманы (`*.example.test`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHandoffModel,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 10, 14, 9, 37, tzinfo=UTC)
#: Письмо встаёт в очередь раньше, чем уходит: время записи и время ухода различаются.
QUEUED_BEFORE = timedelta(hours=3, minutes=11)
_GONE = (MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED)


@dataclass(frozen=True, slots=True)
class Lead:
    """Лид и его диалог продаж — то, к чему тест цепляет письма и ответы."""

    lead_id: int
    thread_id: int
    domain_id: int
    campaign_id: int
    email: str


class Rows:
    """Заводит строки пути лида в базе теста."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._campaigns: dict[int, int] = {}
        self._keys = 0

    async def hypothesis(self, name: str) -> int:
        hypothesis = SalesHypothesisModel(name=name)
        self.session.add(hypothesis)
        await self.session.flush()
        return hypothesis.id

    async def _domain(self, host: str) -> int:
        found = await self.session.scalar(select(DomainModel.id).where(DomainModel.host == host))
        if found is not None:
            return found
        domain = DomainModel(host=host)
        self.session.add(domain)
        await self.session.flush()
        return domain.id

    async def _campaign(self, hypothesis_id: int) -> int:
        """Рассылка гипотезы — одна на гипотезу, как у сборки очереди."""
        if hypothesis_id not in self._campaigns:
            campaign = CampaignModel(stage=Stage.SALES, name=f"Продажи: №{hypothesis_id}")
            self.session.add(campaign)
            await self.session.flush()
            self._campaigns[hypothesis_id] = campaign.id
        return self._campaigns[hypothesis_id]

    async def lead(self, hypothesis_id: int, email: str) -> Lead:
        """Лид `ready` и его диалог продаж — явной связью, как у сборки очереди."""
        domain_id = await self._domain(email.split("@", 1)[1])
        campaign_id = await self._campaign(hypothesis_id)
        lead = SalesLeadModel(
            hypothesis_id=hypothesis_id,
            domain_id=domain_id,
            email=email,
            language="en",
            source=LeadSource.IMPORT,
            status=LeadStatus.READY,
        )
        thread = ThreadModel(domain_id=domain_id, campaign_id=campaign_id, contact_id=None)
        self.session.add_all([lead, thread])
        await self.session.flush()
        self.session.add(
            SalesThreadModel(
                thread_id=thread.id,
                lead_id=lead.id,
                chain_hypothesis_id=None,
                language="en",
                chain_version="chain-madeup0001",
            )
        )
        await self.session.flush()
        return Lead(lead.id, thread.id, domain_id, campaign_id, email)

    async def letter(
        self,
        lead: Lead,
        *,
        step: int,
        status: MessageStatus,
        days_ago: int,
        sent: bool = True,
        answers: ReplyModel | None = None,
    ) -> MessageModel:
        """Письмо диалога. Ушедшее — с временем ухода (`sent=False` — без него);
        `answers` — наш ответ в переписке, а не письмо цепочки."""
        moment = NOW - timedelta(days=days_ago, minutes=7 * step)
        gone = status in _GONE
        self._keys += 1
        message = MessageModel(
            campaign_id=lead.campaign_id,
            thread_id=lead.thread_id,
            domain_id=lead.domain_id,
            contact_id=None,
            answers_reply_id=None if answers is None else answers.id,
            step=step,
            status=status,
            subject="A made-up question",
            body="A made-up letter.",
            created_at=moment - QUEUED_BEFORE if gone else moment,
            sent_at=moment if gone and sent else None,
            idempotency_key=f"funnel-test:{lead.email}:{step}:{self._keys}",
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def reply(self, lead: Lead, kind: ReplyKind, *, days_ago: int) -> ReplyModel:
        reply = ReplyModel(
            thread_id=lead.thread_id,
            kind=kind,
            raw_body="A made-up reply.",
            from_email=lead.email,
            subject="Re: A made-up question",
            created_at=NOW - timedelta(days=days_ago),
        )
        self.session.add(reply)
        await self.session.flush()
        return reply

    async def hand_off(self, lead: Lead) -> None:
        """Передача заведена — дальше её путь (Kommo, Telegram) воронке не важен."""
        self.session.add(SalesHandoffModel(thread_id=lead.thread_id, lead_id=lead.lead_id))
        await self.session.flush()

    async def donor_letter(self, email: str) -> None:
        """Письмо доноров: диалог без связи с лидом продаж."""
        domain_id = await self._domain(email.split("@", 1)[1])
        campaign = CampaignModel(stage=Stage.DONORS, name="доноры")
        self.session.add(campaign)
        await self.session.flush()
        thread = ThreadModel(domain_id=domain_id, campaign_id=campaign.id, contact_id=None)
        self.session.add(thread)
        await self.session.flush()
        self.session.add(
            MessageModel(
                campaign_id=campaign.id,
                thread_id=thread.id,
                domain_id=domain_id,
                step=0,
                status=MessageStatus.DELIVERED,
                sent_at=NOW - timedelta(days=2),
                idempotency_key=f"funnel-test:donor:{email}",
            )
        )
        await self.session.flush()
