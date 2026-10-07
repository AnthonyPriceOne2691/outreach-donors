"""Живые продажи — случаи прогона агента продаж из черновиков с решением человека.

Черновик этапа продаж (`agent_drafts`) и что с ним сделал человек: отправил как есть или с
правкой, отклонил с причиной, ответил сам (черновика с текстом не было); пропуск без ответа
старше срока — молчание человека. Нерешённые черновики и свежие пропуски в прогон не идут:
исхода у них ещё нет, их только считают.

**Переписка — какой была к приходу письма**: наши письма, ушедшие до него, и письма
собеседника по него. Наш ответ на него в неё не попадает — иначе версия «увидела» бы
решение человека.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.agent.writer import Turn
from backend.features.core.domain import DraftStatus, MessageStatus, ReplyKind, Stage
from backend.features.core.models.agent import AgentDraftModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.sales.agent.replay import LABELS, Case, Decision, Human

#: Пропуск без ответа моложе этого — молчание человека ещё не исход: в CRM на «ответ не
#: нужен» отвечали в течение трёх дней.
SETTLE = timedelta(days=3)

_EPOCH = datetime.min.replace(tzinfo=UTC)
#: Письма, которые собеседник получил: их текст и есть наша сторона разговора.
_DELIVERED = (MessageStatus.SENT, MessageStatus.DELIVERED)


@dataclass(frozen=True, slots=True)
class Drafts:
    """Живые продажи: случаи с исходом — и сколько черновиков исхода ещё не имеют."""

    cases: tuple[Case, ...]
    #: Черновик ждёт решения человека.
    pending: int
    #: Пропуск моложе срока: молчание человека — ещё не исход.
    fresh: int


async def from_drafts(
    session: AsyncSession, *, now: datetime, settle: timedelta = SETTLE
) -> Drafts:
    """Черновики этапа продаж с решением человека — случаями прогона."""
    rows = (
        await session.execute(
            select(AgentDraftModel, ReplyModel)
            .join(ReplyModel, ReplyModel.id == AgentDraftModel.reply_id)
            .join(ThreadModel, ThreadModel.id == ReplyModel.thread_id)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .where(CampaignModel.stage == Stage.SALES)
            .order_by(AgentDraftModel.id)
        )
    ).all()
    answered = await _answered(session, [reply.id for _, reply in rows])
    cases: list[Case] = []
    pending = fresh = 0
    for draft, reply in rows:
        human = decided(draft, answered=reply.id in answered)
        if human is None:
            pending += 1
        elif human.decision is Decision.SILENT and reply.created_at > now - settle:
            fresh += 1
        else:
            cases.append(Case(f"draft-{draft.id}", await _before(session, reply), human))
    return Drafts(tuple(cases), pending, fresh)


def decided(draft: AgentDraftModel, *, answered: bool) -> Human | None:
    """Решение человека по черновику. `None` — решения ещё нет.

    Отправленный пустой черновик — ответ человека поверх пропуска или «отдан без текста»
    (`drafts.settle_sent`): человек ответил сам. Метка — ситуация версии, писавшей черновик.
    """
    label = (draft.meta or {}).get("situation")
    known = label if isinstance(label, str) and label in LABELS else None

    def made(decision: Decision, replied: bool, reason: str | None = None) -> Human:
        return Human(decision, replied, known, "model", reason)

    if draft.status is DraftStatus.SENT:
        if not draft.body.strip():
            return made(Decision.OWN, True)
        return made(Decision.SENT_AS_IS if draft.edited is False else Decision.SENT_EDITED, True)
    if draft.status is DraftStatus.REJECTED:
        return made(Decision.REJECTED, answered, draft.reject_reason)
    if draft.status is DraftStatus.SKIPPED:
        return made(Decision.OWN, True) if answered else made(Decision.SILENT, False)
    return None


async def _answered(session: AsyncSession, reply_ids: Sequence[int]) -> set[int]:
    """Входящие, на которые человек ответил письмом — в любом его состоянии: решение
    ответить принято, а дошло ли письмо — забота почты."""
    if not reply_ids:
        return set()
    found = await session.scalars(
        select(MessageModel.answers_reply_id).where(MessageModel.answers_reply_id.in_(reply_ids))
    )
    return {number for number in found if number is not None}


async def _before(session: AsyncSession, reply: ReplyModel) -> tuple[Turn, ...]:
    """Переписка к приходу письма: наши письма, ушедшие до него, и письма собеседника по
    него включительно — тексты как есть, очистка — при прогоне."""
    letters = await session.execute(
        select(MessageModel.sent_at, MessageModel.body).where(
            MessageModel.thread_id == reply.thread_id,
            MessageModel.status.in_(_DELIVERED),
            MessageModel.body.is_not(None),
            or_(MessageModel.sent_at.is_(None), MessageModel.sent_at <= reply.created_at),
        )
    )
    replies = await session.execute(
        select(ReplyModel.created_at, ReplyModel.raw_body).where(
            ReplyModel.thread_id == reply.thread_id,
            ReplyModel.kind == ReplyKind.HUMAN,
            ReplyModel.id <= reply.id,
        )
    )
    timed = [(at or _EPOCH, Turn(ours=True, text=body or "")) for at, body in letters.all()]
    timed += [(at, Turn(ours=False, text=raw)) for at, raw in replies.all()]
    timed.sort(key=lambda pair: pair[0])
    return tuple(turn for _, turn in timed)
