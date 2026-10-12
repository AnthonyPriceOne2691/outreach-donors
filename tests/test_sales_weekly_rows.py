"""Строки базы для тестов недельного отчёта продаж (срез 6.3): лиды с моментом заведения,
письма цепочки и ответы с моментом по Москве, передачи, ящики продаж и строки их здоровья —
прямо в базу, без загрузки, сборки и отправки.

Неделя — выдуманная: с понедельника 1 марта по воскресенье 7 марта 2027 года; Москва —
UTC+3. Тексты и адреса выдуманы (`*.example.test`).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from backend.features.core.domain import MessageStatus, ReplyKind, SenderStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    MessageModel,
    ReplyModel,
    SenderHealthModel,
    SenderModel,
    SendingDomainModel,
)
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHandoffModel,
    SalesLeadModel,
)
from backend.features.sales.weekly import Week
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_funnel_rows import Lead, Rows

#: Москва — своим поясом, а не поясом кода: мутант «неделя по UTC» сдвинул бы и данные теста.
MSK = ZoneInfo("Europe/Moscow")
WEEK = Week(date(2027, 3, 1))
#: Отчёт собирается в понедельник следующей недели — в 09:13 по Москве.
BUILT = datetime(2027, 3, 8, 9, 13, tzinfo=MSK)
_GONE = (MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED)

#: Снимок разбора ответа продаж: вид назван, человека ответ не ждёт.
SETTLED = {"stage": "sales", "kind": "question", "waits": False}


def msk(day: int, hour: int = 12, minute: int = 0, *, month: int = 3) -> datetime:
    """Момент по Москве в выдуманном 2027 году."""
    return datetime(2027, month, day, hour, minute, tzinfo=MSK)


class WeekRows:
    """Заводит строки недели в базе теста; гипотезы, диалоги и рассылки — как у воронки."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._rows = Rows(session)
        self._keys = 0

    async def hypothesis(self, name: str) -> int:
        return await self._rows.hypothesis(name)

    async def lead(
        self,
        hypothesis_id: int,
        email: str,
        *,
        at: datetime,
        status: LeadStatus = LeadStatus.READY,
        company: str | None = None,
        name: str | None = None,
    ) -> Lead:
        """Лид с диалогом продаж, заведённый в момент `at`."""
        lead = await self._rows.lead(hypothesis_id, email)
        row = await self.session.get(SalesLeadModel, lead.lead_id)
        assert row is not None
        row.created_at, row.status, row.company, row.name = at, status, company, name
        await self.session.flush()
        return lead

    async def loaded(
        self, hypothesis_id: int, email: str, *, at: datetime, status: LeadStatus
    ) -> int:
        """Лид, только загруженный: без диалога и писем; домен адреса — свой у каждого."""
        domain = DomainModel(host=email.split("@", 1)[1])
        self.session.add(domain)
        await self.session.flush()
        lead = SalesLeadModel(
            hypothesis_id=hypothesis_id,
            domain_id=domain.id,
            email=email,
            language="en",
            source=LeadSource.IMPORT,
            status=status,
            created_at=at,
        )
        self.session.add(lead)
        await self.session.flush()
        return lead.id

    async def letter(
        self,
        lead: Lead,
        *,
        step: int,
        status: MessageStatus,
        at: datetime,
        answers: ReplyModel | None = None,
    ) -> MessageModel:
        """Письмо диалога; ушедшее — с временем ухода `at`, в очереди — записанное в `at`."""
        self._keys += 1
        gone = status in _GONE
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
            created_at=at,
            sent_at=at if gone else None,
            idempotency_key=f"weekly-test:{lead.email}:{step}:{self._keys}",
        )
        self.session.add(message)
        await self.session.flush()
        return message

    async def reply(
        self,
        lead: Lead,
        kind: ReplyKind,
        *,
        at: datetime,
        snapshot: dict[str, Any] | None = None,
    ) -> ReplyModel:
        reply = ReplyModel(
            thread_id=lead.thread_id,
            kind=kind,
            raw_body="A made-up reply.",
            from_email=lead.email,
            subject="Re: A made-up question",
            model_parse=snapshot,
            created_at=at,
        )
        self.session.add(reply)
        await self.session.flush()
        return reply

    async def hand_off(
        self, lead: Lead, *, at: datetime, link: str | None = None
    ) -> SalesHandoffModel:
        """Передача заведена в момент `at`; `link` — что получил телемаркетолог."""
        row = SalesHandoffModel(
            thread_id=lead.thread_id, lead_id=lead.lead_id, notified_link=link, created_at=at
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def box(
        self,
        email: str,
        *,
        enabled: bool = True,
        cap: int = 20,
        started: datetime | None = None,
        stage: Stage = Stage.SALES,
    ) -> SenderModel:
        """Ящик направления; `started` — начало разгона (пусто — разгон кончен)."""
        box = SenderModel(
            domain=email.split("@", 1)[1],
            email=email,
            stage=stage,
            daily_cap=cap,
            status=SenderStatus.FREE if enabled else SenderStatus.PAUSED,
            enabled=enabled,
            warmup_started_at=started,
        )
        self.session.add(box)
        await self.session.flush()
        return box

    async def domain(
        self, name: str, *, limit: int, young_until: datetime | None = None
    ) -> SendingDomainModel:
        row = SendingDomainModel(
            domain=name, stage=Stage.SALES, daily_limit=limit, young_until=young_until
        )
        self.session.add(row)
        await self.session.flush()
        return row

    async def health(
        self, box: SenderModel, kind: str, *, at: datetime, detail: str | None = None
    ) -> None:
        self.session.add(SenderHealthModel(sender_id=box.id, kind=kind, at=at, detail=detail))
        await self.session.flush()
