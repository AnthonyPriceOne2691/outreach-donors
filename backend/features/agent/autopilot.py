"""Автопилот агента переписки: ответ в границах уходит сам, остальное — человеку.

Решение владельца 04.10.2026: два режима, по умолчанию черновики; автопилот —
переключатель с границами. **Три ключа, и все по умолчанию выключены:**

- флаг этапа в коде (`AgentStage.autopilot`) — письмо без человека на этапе
  включает владелец, а не экран; сейчас он не стоит ни у одного этапа;
- выключатель сервера (`OUTREACH_AGENT_AUTOPILOT`);
- режим «автопилот» в версии настроек, по которой написан черновик.

Своего текста автопилот не пишет: он отправляет готовый черновик
(`drafted` — агент не сомневается, судья этапа пропустил), если тот проходит
все границы, а если нет — отдаёт его человеку с причиной (`escalated`).

**Границы механические, модели на слово не верят:** разбор цены этого
ответа не ждёт человека; суммы в черновике — в пределе цены по стороне этапа
(`PriceSide`: покупаем — не дороже, продаём — не дешевле; без предела сумм
нет вовсе); переписку не ведёт человек; ответов автопилота меньше предела.

**Отправка — путь решения человека** (`drafts.send_draft` «как есть», решил
`autopilot`): стоп-лист, решение по донору, метрики Ahrefs, лимиты ящика.
Их отказ — тоже причина отдать черновик человеку, а не сбой задачи.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.features.agent import drafts, stages
from backend.features.agent.drafts import AUTOPILOT as BY_AUTOPILOT
from backend.features.agent.drafts import Decider, DraftDecisionError
from backend.features.agent.settings import AUTOPILOT
from backend.features.agent.stages import PriceSide
from backend.features.core.domain import DraftStatus, Stage
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters.guards import ForbiddenContentError
from backend.features.letters.sending import SendError, Sending
from backend.features.letters.transport import MaybeSentError, TransportError
from backend.features.outreach.threads import review_of
from backend.features.replies.money import amounts_in

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AutopilotOutcome:
    """Чем кончилось: письмо ушло, черновик отдан человеку — или автопилот
    здесь ни при чём (не разрешён, режим черновиков, черновик не готов)."""

    sent_message_id: int | None = None
    held: str | None = None


def refusal(stage: Stage) -> str | None:
    """Почему автопилот этапу не разрешён. `None` — разрешён кодом и сервером."""
    parts = stages.AGENT_STAGES.get(stage)
    if parts is None or not parts.autopilot:
        return (
            f"Этапу «{stage.value}» автопилот не разрешён — ответ без человека на "
            "этапе включает владелец в коде этапа; агент пишет только черновики"
        )
    if not outreach_cfg.AGENT_AUTOPILOT:
        return (
            "Автопилот на этом сервере выключен (OUTREACH_AGENT_AUTOPILOT) — "
            "агент пишет только черновики"
        )
    return None


def money_beyond(side: PriceSide, limit: Decimal | None, text: str) -> str | None:
    """Сумма в тексте за пределом цены — словами. `None` — сумм нет или все в пределе."""
    amounts = sorted(amounts_in(text))
    if not amounts:
        return None
    if limit is None:
        return f"в черновике сумма {amounts[0]}, а предела цены нет — сумму называет человек"
    if side is PriceSide.BUY:
        over = [amount for amount in amounts if amount > limit]
        return f"в черновике {over[-1]} — дороже предела {limit}" if over else None
    under = [amount for amount in amounts if amount < limit]
    return f"в черновике {under[0]} — дешевле предела {limit}" if under else None


def turns_left(answered: Sequence[int], by_autopilot: Sequence[int], max_turns: int) -> str | None:
    """Чья переписка и хватает ли предела ответов. `None` — автопилот может ответить."""
    if any(message_id not in by_autopilot for message_id in answered):
        return "переписку ведёт человек — автопилот в неё не вмешивается"
    if len(by_autopilot) >= max_turns:
        return f"автопилот уже ответил здесь {len(by_autopilot)} раз — дальше человек"
    return None


async def run(session: AsyncSession, sending: Sending, draft_id: int) -> AutopilotOutcome:
    """Отправить черновик сам — или отдать его человеку с причиной."""
    draft = await session.get(AgentDraftModel, draft_id)
    settings = None if draft is None else await session.get(AgentSettingsModel, draft.settings_id)
    if draft is None or settings is None or not _applies(draft, settings):
        return AutopilotOutcome()
    reply = await session.get(ReplyModel, draft.reply_id)
    thread = None if reply is None else await session.get(ThreadModel, reply.thread_id)
    if reply is None or thread is None:
        return AutopilotOutcome()
    stage = await session.scalar(
        select(CampaignModel.stage).where(CampaignModel.id == thread.campaign_id)
    )
    if stage is None or refusal(stage) is not None:
        return AutopilotOutcome()
    why = await _beyond_bounds(session, draft, settings, reply, thread, stage)
    if why is None:
        try:
            sent = await drafts.send_draft(
                session, sending, draft_id, body=None, by=Decider(name=BY_AUTOPILOT)
            )
        except MaybeSentError as exc:
            # Письмо, возможно, ушло: второго не будет (ответ «отправляется», черновик
            # устарел), исход решает человек по блоку «Исход неизвестен».
            logger.warning(
                "автопилот: исход письма неизвестен", extra={"draft": draft_id, "why": str(exc)}
            )
            why = f"исход отправки неизвестен — письмо, возможно, ушло: {exc}"
        except (SendError, ForbiddenContentError, TransportError, DraftDecisionError) as exc:
            # Отказ пути отправки — не сбой задачи: черновик уходит человеку.
            logger.warning("автопилот: черновик №%s не отправлен — %s", draft_id, exc)
            why = f"отправка отказала: {exc}"
        else:
            await session.commit()
            return AutopilotOutcome(sent_message_id=sent.message_id)
    return await _hold(session, draft_id, why)


def _applies(draft: AgentDraftModel, settings: AgentSettingsModel) -> bool:
    """Автопилоту есть что решать: режим включён и черновик готов к отправке."""
    return (
        settings.mode == AUTOPILOT
        and draft.status is DraftStatus.DRAFTED
        and bool(draft.body.strip())
    )


async def _beyond_bounds(
    session: AsyncSession,
    draft: AgentDraftModel,
    settings: AgentSettingsModel,
    reply: ReplyModel,
    thread: ThreadModel,
    stage: Stage,
) -> str | None:
    if review_of(reply, stage).waiting:
        return "разбор цены этого ответа ждёт человека"
    side = stages.AGENT_STAGES[stage].price
    beyond = money_beyond(side, settings.price_limit_usd, draft.body)
    if beyond is not None:
        return beyond
    answered = (
        await session.scalars(
            select(MessageModel.id).where(
                MessageModel.thread_id == thread.id, MessageModel.answers_reply_id.is_not(None)
            )
        )
    ).all()
    by_autopilot = (
        await session.scalars(
            select(AgentDraftModel.sent_message_id)
            .join(ReplyModel, ReplyModel.id == AgentDraftModel.reply_id)
            .where(
                ReplyModel.thread_id == thread.id,
                AgentDraftModel.decided_by == BY_AUTOPILOT,
                AgentDraftModel.sent_message_id.is_not(None),
            )
        )
    ).all()
    return turns_left(answered, [m for m in by_autopilot if m is not None], settings.max_turns)


async def _hold(session: AsyncSession, draft_id: int, why: str) -> AutopilotOutcome:
    """Черновик — человеку, с причиной рядом с причиной агента.

    Черновик берётся заново: отказ отправки мог прийти после записей пути
    ответа, и прежний объект — уже не то, что в базе.
    """
    draft = await session.get(AgentDraftModel, draft_id, populate_existing=True)
    if draft is not None:
        note = f"автопилот не отправил: {why}"
        draft.status = DraftStatus.ESCALATED
        draft.reason = note if not draft.reason else f"{draft.reason}; {note}"
        await session.commit()
    return AutopilotOutcome(held=why)
