"""Что известно о лиде и диалоге в момент передачи — карточка для примечания и сообщения.

Карточку собирает задача передачи (`handoff.process`) из базы: лид, гипотеза, сайт, последнее
письмо человека, сводка переписки числами и то, что о лиде сказал разбор ответов (v2): о чём его
проект и какие услуги ему интересны (`reply_kind.told`). Проект и услуги нужны телемаркетологу для
MQL (Ф5: в сводке — проект, услуги и ГЕО); их берут из снимков всех ответов диалога, а не
только последнего: «да, давайте созвонимся» об услугах не говорит, а прошлое письмо говорило.
Проект — последний названный; услуги — все названные, в порядке, как их назвали.

**Последнее письмо — ответ человека или его отписка словами** (`FORWARDED`): автоответ
телемаркетологу не нужен, а «больше не пишите» от лида, которому уже звонят, — нужно.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.replies.quoting import written_by_hand
from backend.features.sales.handoff_text import Card
from backend.features.sales.models import (
    SalesHandoffModel,
    SalesHypothesisModel,
    SalesLeadModel,
)

#: Какие письма человека уходят телемаркетологу: ответ и отписка словами — не автоответ.
FORWARDED = (ReplyKind.HUMAN, ReplyKind.UNSUBSCRIBE)


async def latest_reply(session: AsyncSession, thread_id: int) -> ReplyModel | None:
    """Последнее письмо человека в диалоге (`FORWARDED`): по нему задача решает, есть ли что
    записать в Kommo и о чём сказать телемаркетологу."""
    found: ReplyModel | None = await session.scalar(
        select(ReplyModel)
        .where(ReplyModel.thread_id == thread_id, ReplyModel.kind.in_(FORWARDED))
        .order_by(ReplyModel.created_at.desc(), ReplyModel.id.desc())
        .limit(1)
    )
    return found


async def card_of(session: AsyncSession, row: SalesHandoffModel) -> Card:
    """Что известно о лиде и диалоге: лид, гипотеза, сайт, последний ответ, сводка, проект
    и услуги."""
    found = await session.execute(
        select(SalesLeadModel, SalesHypothesisModel.name, DomainModel.host)
        .join(SalesHypothesisModel, SalesHypothesisModel.id == SalesLeadModel.hypothesis_id)
        .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
        .where(SalesLeadModel.id == row.lead_id)
        .execution_options(populate_existing=True)
    )
    lead, hypothesis, host = found.one()._tuple()
    reply = await latest_reply(session, row.thread_id)
    sent, first = (
        await session.execute(
            select(func.count(), func.min(MessageModel.sent_at)).where(
                MessageModel.thread_id == row.thread_id, MessageModel.sent_at.is_not(None)
            )
        )
    ).one()
    replies = await session.scalar(
        select(func.count())
        .select_from(ReplyModel)
        .where(ReplyModel.thread_id == row.thread_id, ReplyModel.kind == ReplyKind.HUMAN)
    )
    snapshots = await session.scalars(
        select(ReplyModel.model_parse)
        .where(ReplyModel.thread_id == row.thread_id, ReplyModel.model_parse.is_not(None))
        .order_by(ReplyModel.id)
    )
    project, services = told(snapshots)
    return Card(
        thread_id=row.thread_id,
        lead_id=lead.id,
        email=lead.email,
        name=lead.name or "",
        company=lead.company or "",
        site=host,
        hypothesis=hypothesis,
        country=lead.country or "",
        language=lead.language or "",
        reply_id=reply.id if reply else None,
        reply_at=reply.created_at if reply else None,
        reply_subject=(reply.subject or "") if reply else "",
        reply_text=written_by_hand(reply.raw_body) if reply else "",
        sent=int(sent),
        replies=int(replies or 0),
        first_sent_at=first,
        project=project,
        services=services,
    )


def told(snapshots: Iterable[Mapping[str, Any] | None]) -> tuple[str, tuple[str, ...]]:
    """Проект и услуги из снимков разбора ответов продаж по порядку: проект — последний
    названный, услуги — все, без повторов. Снимки других этапов и записки без вида не в счёт."""
    project = ""
    services: dict[str, None] = {}
    for snap in snapshots:
        if not snap or snap.get("stage") != Stage.SALES.value:
            continue
        named = snap.get("project")
        if isinstance(named, str) and named.strip():
            project = named.strip()
        listed = snap.get("services")
        for title in listed if isinstance(listed, list) else []:
            if isinstance(title, str) and title.strip():
                services.setdefault(title.strip(), None)
    return project, tuple(services)
