"""Проба продаж на свой ящик — что убирает `prune --test-traces`.

**Проба — лид с адресом своего ящика.** Свои ящики названы предохранителем отправки
(`outreach/own_inboxes.py`), как у следов проверки доноров. Сборка продаж пишет диалог
и письма без адреса `contacts` — адрес живёт у лида, — и чистка следов доноров их не
видит. Здесь проба находится от лида: его диалоги (связь сборки `sales_threads` и диалоги
его передач), письма и ответы в них, передача.

**Что уходит.** Лид, его диалоги с письмами и ответами — каскадом с ними связь диалога,
передача и черновики ответов — и рассылки, в которых ничего не останется. Домен остаётся:
он общий, а липовый (`.invalid`) уберёт `prune --probes`, когда лида на нём не станет.

**Сделка в Kommo остаётся.** Чистка в Kommo не ходит: удалить сделку может только
человек — по номеру из плана. Номера — в плане и в журнале действий.

Условия повторены в запросах удаления, как у остальной чистки: уходит только то, что
относится к лиду со своим ящиком, что бы ни лежало в плане. Лид, у которого после показа
появился новый диалог или передача, остаётся: его держит то, чего в плане нет.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, Row, Select, delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.donors.probe import emptied_campaign
from backend.features.outreach.own_inboxes import OwnInboxes
from backend.features.sales.models import SalesHandoffModel, SalesLeadModel, SalesThreadModel


@dataclass(slots=True)
class TrialLead:
    """Лид пробы — строка показа."""

    lead_id: int
    email: str
    host: str
    threads: int = 0
    letters: int = 0
    replies: int = 0
    #: Номера сделок Kommo у передач лида: в Kommo они остаются.
    deals: list[int] = field(default_factory=list)


@dataclass(slots=True)
class TrialTrace:
    """Что уходит с пробой продаж при `prune --test-traces`. Сам план ничего не меняет."""

    inboxes: OwnInboxes
    leads: list[TrialLead] = field(default_factory=list)
    threads: list[int] = field(default_factory=list)
    letters: list[int] = field(default_factory=list)
    replies: list[int] = field(default_factory=list)
    handoffs: list[int] = field(default_factory=list)
    #: Номера сделок Kommo всех передач пробы: в Kommo они остаются.
    deals: list[int] = field(default_factory=list)
    #: Рассылки, в которых не останется ни писем, ни переписки.
    campaigns: list[int] = field(default_factory=list)

    def as_details(self) -> dict[str, Any]:
        """Запись в журнал: какие лиды, что ушло и какие сделки остались в Kommo."""
        return {
            "проба продаж": {
                "лиды": [lead.lead_id for lead in self.leads],
                "адреса": [lead.email for lead in self.leads],
                "диалогов": len(self.threads),
                "писем": len(self.letters),
                "ответов": len(self.replies),
                "передачи": self.handoffs,
                "сделки Kommo — остаются в Kommo": self.deals,
                "рассылки": self.campaigns,
            }
        }


def _own(inboxes: OwnInboxes) -> ColumnElement[bool]:
    """Лид со своим ящиком: адрес из предохранителя."""
    return func.lower(func.trim(SalesLeadModel.email)).in_(inboxes.addresses)


def _dialog_of(
    column: InstrumentedAttribute[int] | InstrumentedAttribute[int | None],
    leads: Select[tuple[int]],
) -> ColumnElement[bool]:
    """`column` — диалог одного из лидов: по связи сборки продаж или по его передаче."""
    return or_(
        column.in_(select(SalesThreadModel.thread_id).where(SalesThreadModel.lead_id.in_(leads))),
        column.in_(select(SalesHandoffModel.thread_id).where(SalesHandoffModel.lead_id.in_(leads))),
    )


async def trial_trace(session: AsyncSession, inboxes: OwnInboxes) -> TrialTrace:
    """Проба продаж и всё, что за ней тянется. Ничего не меняет."""
    trace = TrialTrace(inboxes=inboxes)
    found = (
        await session.execute(
            select(SalesLeadModel.id, SalesLeadModel.email, DomainModel.host)
            .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
            .where(_own(inboxes))
            .order_by(SalesLeadModel.id)
        )
    ).all()
    if not found:
        return trace
    leads = {row.id: TrialLead(row.id, row.email, row.host) for row in found}
    trace.leads = list(leads.values())
    owner = await _dialogs(session, list(leads))
    trace.threads = sorted(owner)
    for lead_id in owner.values():
        leads[lead_id].threads += 1
    letters = (
        await session.execute(
            select(MessageModel.id, MessageModel.thread_id, MessageModel.campaign_id)
            .where(MessageModel.thread_id.in_(trace.threads))
            .order_by(MessageModel.id)
        )
    ).all()
    trace.letters = [row.id for row in letters]
    for row in letters:
        leads[owner[row.thread_id]].letters += 1
    await _replies(session, trace, leads, owner, {row.id: row.thread_id for row in letters})
    await _handoffs(session, trace, leads)
    campaigns = await session.scalars(
        select(ThreadModel.campaign_id).where(ThreadModel.id.in_(trace.threads))
    )
    touched = {*campaigns.all(), *(row.campaign_id for row in letters)}
    trace.campaigns = await _emptied(session, trace, touched)
    return trace


async def _dialogs(session: AsyncSession, lead_ids: Sequence[int]) -> dict[int, int]:
    """Диалог → лид: по связи сборки продаж, а без неё — по передаче."""
    linked = await session.execute(
        select(SalesThreadModel.thread_id, SalesThreadModel.lead_id).where(
            SalesThreadModel.lead_id.in_(lead_ids)
        )
    )
    handed = await session.execute(
        select(SalesHandoffModel.thread_id, SalesHandoffModel.lead_id).where(
            SalesHandoffModel.lead_id.in_(lead_ids)
        )
    )
    owner = dict(handed.tuples().all())
    owner.update(linked.tuples().all())
    return owner


async def _replies(
    session: AsyncSession,
    trace: TrialTrace,
    leads: dict[int, TrialLead],
    owner: dict[int, int],
    letter_thread: dict[int, int | None],
) -> None:
    """Ответы в диалогах пробы и на её письма — по лидам."""
    rows: Sequence[Row[Any]] = (
        await session.execute(
            select(ReplyModel.id, ReplyModel.thread_id, ReplyModel.message_id)
            .where(
                or_(
                    ReplyModel.thread_id.in_(trace.threads),
                    ReplyModel.message_id.in_(trace.letters),
                )
            )
            .order_by(ReplyModel.id)
        )
    ).all()
    trace.replies = [row.id for row in rows]
    for row in rows:
        thread = row.thread_id if row.thread_id in owner else letter_thread.get(row.message_id)
        if thread in owner:
            leads[owner[thread]].replies += 1


async def _handoffs(session: AsyncSession, trace: TrialTrace, leads: dict[int, TrialLead]) -> None:
    """Передачи пробы и номера их сделок в Kommo: сделки остаются, номера — в плане."""
    rows = (
        await session.execute(
            select(SalesHandoffModel.id, SalesHandoffModel.lead_id, SalesHandoffModel.kommo_lead_id)
            .where(
                or_(
                    SalesHandoffModel.lead_id.in_(list(leads)),
                    SalesHandoffModel.thread_id.in_(trace.threads),
                )
            )
            .order_by(SalesHandoffModel.id)
        )
    ).all()
    trace.handoffs = [row.id for row in rows]
    for row in rows:
        if row.kommo_lead_id is None:
            continue
        trace.deals.append(row.kommo_lead_id)
        if row.lead_id in leads:
            leads[row.lead_id].deals.append(row.kommo_lead_id)


async def _emptied(session: AsyncSession, trace: TrialTrace, touched: Collection[int]) -> list[int]:
    """Рассылки, где кроме пробы нет ни писем, ни переписки."""
    elsewhere = or_(
        exists().where(
            MessageModel.campaign_id == CampaignModel.id, MessageModel.id.not_in(trace.letters)
        ),
        exists().where(
            ThreadModel.campaign_id == CampaignModel.id, ThreadModel.id.not_in(trace.threads)
        ),
    )
    rows = await session.scalars(
        select(CampaignModel.id)
        .where(CampaignModel.id.in_(sorted(touched)), ~elsewhere)
        .order_by(CampaignModel.id)
    )
    return list(rows.all())


async def remove_trials(session: AsyncSession, trace: TrialTrace) -> None:
    """Убрать пробу продаж по плану. Без коммита: решает вызывающий.

    Порядок — по ссылкам. Ответы — до писем: удаление письма обнулило бы у ответа ссылку,
    и ответ остался бы висеть. Письма — до диалогов, диалоги — до лида: лида держат связь
    диалога и передача (`RESTRICT`), они уходят с диалогом каскадом. Рассылки — после, по
    тому, что осталось.
    """
    if not trace.leads:
        return
    mine = (SalesLeadModel.id.in_([lead.lead_id for lead in trace.leads]), _own(trace.inboxes))
    own = select(SalesLeadModel.id).where(*mine)
    letters = select(MessageModel.id).where(_dialog_of(MessageModel.thread_id, own))
    await session.execute(
        delete(ReplyModel).where(
            ReplyModel.id.in_(trace.replies),
            or_(_dialog_of(ReplyModel.thread_id, own), ReplyModel.message_id.in_(letters)),
        )
    )
    await session.execute(
        delete(MessageModel).where(
            MessageModel.id.in_(trace.letters), _dialog_of(MessageModel.thread_id, own)
        )
    )
    await session.execute(
        delete(ThreadModel).where(
            ThreadModel.id.in_(trace.threads), _dialog_of(ThreadModel.id, own)
        )
    )
    await session.execute(
        delete(SalesLeadModel).where(
            *mine,
            ~exists().where(SalesThreadModel.lead_id == SalesLeadModel.id),
            ~exists().where(SalesHandoffModel.lead_id == SalesLeadModel.id),
        )
    )
    if trace.campaigns:
        await session.execute(
            delete(CampaignModel).where(CampaignModel.id.in_(trace.campaigns), emptied_campaign())
        )
