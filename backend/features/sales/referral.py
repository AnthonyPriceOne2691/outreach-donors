"""«Это не ко мне, пишите …»: новый лид той же компании из ответа продаж.

Названный адрес становится лидом той же гипотезы и того же домена компании,
что лид исходного диалога; источник — `referral`, ссылка — на диалог, где его
назвали. Дальше — как у любого лида: очистка (`cleaning.clean_leads`: дубль,
стоп-листы, другое направление, годность, почта домена, проверяльщик). Письмо
ему — очередь писем продаж: здесь только лид.

**Исходный диалог закрывается**: собеседник сказал, что это не к нему. Помехой новому
лиду он не стал бы и открытым: «другое направление» очистки — только доноры и рекламодатели.

**Лид исходного диалога — по явной связи** (`sales_threads`, 4.6b): диалог, начатый
сборкой очереди продаж, знает своего лида, а строки `contacts` у него нет. Диалог без
связи — по домену и адресу контакта, которому писали. Не нашёлся — лида не выдумываем:
ответ ждёт человека с причиной.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import assert_never

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import ThreadStatus
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import ThreadModel
from backend.features.sales.cleaning import clean_leads
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.verifier import EmailVerifier


@dataclass(frozen=True, slots=True)
class Referred:
    """Что вышло из «пишите другому»: лид или почему его нет — словами."""

    lead_id: int | None
    words: str

    @property
    def waits(self) -> bool:
        """Лида нет — ответ ждёт человека."""
        return self.lead_id is None


async def origin_lead(session: AsyncSession, thread: ThreadModel) -> SalesLeadModel | None:
    """Лид исходного диалога: по явной связи, без неё — адрес контакта диалога на домене
    диалога, самый ранний."""
    linked: SalesLeadModel | None = await session.scalar(
        select(SalesLeadModel)
        .join(SalesThreadModel, SalesThreadModel.lead_id == SalesLeadModel.id)
        .where(SalesThreadModel.thread_id == thread.id)
    )
    if linked is not None:
        return linked
    if thread.contact_id is None:
        return None
    email = await session.scalar(
        select(ContactModel.email).where(ContactModel.id == thread.contact_id)
    )
    if not email:
        return None
    found: SalesLeadModel | None = await session.scalar(
        select(SalesLeadModel)
        .where(SalesLeadModel.domain_id == thread.domain_id)
        .where(func.lower(SalesLeadModel.email) == email.lower())
        .order_by(SalesLeadModel.id)
        .limit(1)
    )
    return found


async def refer(
    session: AsyncSession,
    thread_id: int | None,
    contact: str | None,
    verifier: EmailVerifier,
    *,
    now: datetime,
) -> Referred:
    """Завести лида на названный адрес и очистить его; исходный диалог — закрыть."""
    thread = await session.get(ThreadModel, thread_id) if thread_id else None
    if thread is None:
        return Referred(None, "диалога нет — завести лида руками")
    if not contact:
        return Referred(None, "адреса в ответе нет — найти его и завести лида руками")
    origin = await origin_lead(session, thread)
    if origin is None:
        return Referred(None, f"лид исходного диалога не найден — завести лида {contact} руками")
    lead = SalesLeadModel(
        hypothesis_id=origin.hypothesis_id,
        domain_id=thread.domain_id,
        email=contact,
        company=origin.company,
        country=origin.country,
        timezone=origin.timezone,
        language=origin.language,
        source=LeadSource.REFERRAL,
        status=LeadStatus.NEW,
        referred_from_thread_id=thread.id,
    )
    session.add(lead)
    if thread.status is not ThreadStatus.UNSUBSCRIBED:
        thread.status = ThreadStatus.CLOSED
    await session.flush()
    await clean_leads(session, [lead], verifier, now=now)
    await session.refresh(lead)
    return Referred(lead.id, _words(lead))


def _words(lead: SalesLeadModel) -> str:
    who = f"новый лид №{lead.id} ({lead.email})"
    match lead.status:
        case LeadStatus.READY:
            return f"{who} прошёл очистку и ждёт письма; диалог закрыт"
        case LeadStatus.REJECTED:
            return f"{who} отсеян очисткой — {lead.cleaning_note}; диалог закрыт"
        case LeadStatus.NEW:
            return f"{who} ждёт очистки — {lead.cleaning_note}; диалог закрыт"
        case _:
            assert_never(lead.status)
