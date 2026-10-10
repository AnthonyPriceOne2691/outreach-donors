"""Черновики агента: что видит экран и решения по ним — отправить или отклонить.

Решения общие для всех этапов и проверяются сервером, а не только экраном:

- **отклонить — только с причиной**: причина — датасет докрутки агента, и
  «отклонено» без неё ничего не объясняет. Вид причины — пункт списка этапа
  (`AgentStage.reject_reasons`) или «другое» — ложится рядом (`reject_kind`):
  калибровка считает отклонения по виду, не читая слов. У этапа со строгим
  списком (продажи) причина своими словами без «другое: …» не принимается;
- **«как есть» у `escalated` закрыто**: агент, бриф или судья отдали ответ
  человеку, и их текст без правки наружу не уходит;
- **отправка — путём ответа человека** (`letters/answers.answer_reply`):
  стоп-лист, решение по донору, метрики Ahrefs, лимиты ящика — те же;
- **устаревший черновик не уходит** (`stale`): он написан на одно письмо
  собеседника, и если после него в переписке уже есть наше письмо или новое
  письмо собеседника, отправка — второй ответ или ответ мимо нового письма;
- ответ, отправленный из переписки мимо кнопки черновика, тоже закрывает
  черновик (`settle_sent`): с правкой или без — видно по тексту.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.agent.drafting import DECIDED
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.agent.stages import AGENT_STAGES, OTHER_REASON
from backend.features.core.domain import AuditAction, DraftStatus, MessageStatus, Stage
from backend.features.core.models.access import UserModel
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.letters.answers import answer_reply
from backend.features.letters.attempts import ANSWERS
from backend.features.letters.sending import Sending, SendOutcome

#: Кто решил, когда решил не человек.
AUTOPILOT = "autopilot"

#: Наше письмо, которое собеседник получил или вот-вот получит. В очереди и
#: остановленное — не в счёт: неудачная попытка ответа не делает черновик
#: устаревшим, и повтор по нему остаётся возможен.
_OUT = (MessageStatus.SENDING, MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED)


class UnknownDraftError(LookupError):
    """Черновика с таким номером нет."""


class DraftDecisionError(RuntimeError):
    """Решение по черновику невозможно: уже решён, «как есть» закрыто, текста нет."""


class DraftReasonError(ValueError):
    """Причина отклонения не годится: её нет, или у этапа со строгим списком её нет
    в списке, или «другое» без слов. Это запрос, а не состояние черновика (422)."""


class StaleDraftError(DraftDecisionError):
    """Черновик устарел: после письма, на которое он написан, переписка ушла дальше."""


@dataclass(frozen=True, slots=True)
class ShownDraft:
    """Черновик, номер версии настроек, по которой он написан, и переписка."""

    draft: AgentDraftModel
    settings_version: int
    #: У ответа, ни к чему не привязанного, переписки нет — и черновика тоже
    #: (`drafting._target`); тип колонки ответа это не знает.
    thread_id: int | None


@dataclass(frozen=True, slots=True)
class Decider:
    """Кто решает: человек (номер и адрес) — или автопилот без номера."""

    name: str
    user_id: int | None = None

    @classmethod
    def of(cls, user: UserModel) -> Decider:
        """Человек, нажавший кнопку: адрес — в черновик, номер — в журнал."""
        return cls(name=user.email, user_id=user.id)


def _shown() -> Select[tuple[AgentDraftModel, int, int | None]]:
    return (
        select(AgentDraftModel, AgentSettingsModel.version, ReplyModel.thread_id)
        .join(AgentSettingsModel, AgentSettingsModel.id == AgentDraftModel.settings_id)
        .join(ReplyModel, ReplyModel.id == AgentDraftModel.reply_id)
    )


async def drafts_of(session: AsyncSession, reply_ids: Iterable[int]) -> Sequence[ShownDraft]:
    """Черновики к ответам переписки — одним запросом на всю переписку."""
    ids = list(reply_ids)
    if not ids:
        return []
    rows = await session.execute(_shown().where(AgentDraftModel.reply_id.in_(ids)))
    return [ShownDraft(draft, version, thread) for draft, version, thread in rows.all()]


async def waiting(
    session: AsyncSession,
    status: DraftStatus,
    *,
    limit: int,
    stages: Collection[Stage],
) -> Sequence[ShownDraft]:
    """Черновики в одном статусе, новые первыми — например, ждущие человека.

    `stages` — этапы, которые видит спрашивающий (`access.permissions.visible_stages`):
    черновик продаж без права «Продажи» в список не попадает (решение Anthony 10.10.2026,
    П2). Этап — по версии настроек черновика: её берут по этапу переписки. Умолчания нет
    (ревью продаж к #304): забытый вызов ловит mypy, а не показывает продажи всем.
    """
    rows = await session.execute(
        _shown()
        .where(AgentDraftModel.status == status)
        .where(AgentSettingsModel.stage.in_(stages))
        .order_by(AgentDraftModel.updated_at.desc(), AgentDraftModel.id.desc())
        .limit(limit)
    )
    return [ShownDraft(draft, version, thread) for draft, version, thread in rows.all()]


async def one(session: AsyncSession, draft_id: int) -> ShownDraft:
    row = (await session.execute(_shown().where(AgentDraftModel.id == draft_id))).first()
    if row is None:
        raise UnknownDraftError(f"Черновика №{draft_id} нет")
    draft, version, thread = row
    return ShownDraft(draft, version, thread)


async def agent_writes(session: AsyncSession, stage: Stage) -> bool:
    """Пишет ли агент на этапе: этап в реестре, настроен и включён."""
    if stage not in AGENT_STAGES:
        return False
    current = await AgentSettingsRepository(session).current(stage)
    return current is not None and current.enabled


def reject_reasons(stage: Stage) -> tuple[str, ...]:
    """За что отклоняют черновик на этапе; этапа без агента — не за что."""
    parts = AGENT_STAGES.get(stage)
    return () if parts is None else parts.reject_reasons


async def send_draft(
    session: AsyncSession,
    sending: Sending,
    draft_id: int,
    *,
    body: str | None,
    by: Decider,
) -> SendOutcome:
    """Отправить ответ по черновику: `body=None` — как есть, иначе — с правкой."""
    shown = await one(session, draft_id)
    draft = _undecided(shown.draft)
    _refuse_as_is(draft, body)
    if shown.thread_id is None:
        raise DraftDecisionError(f"Ответ черновика №{draft_id} ни к чему не привязан")
    why = await stale(session, draft, shown.thread_id)
    if why is not None:
        raise StaleDraftError(
            f"Черновик №{draft_id} устарел: {why}. Прочтите переписку и ответьте по ней — "
            "сами или новым черновиком"
        )
    text = draft.body if body is None else body
    sent = await answer_reply(
        session,
        sending,
        thread_id=shown.thread_id,
        reply_id=draft.reply_id,
        body=text,
        author_id=by.user_id,
    )
    await settle_sent(session, draft.reply_id, message_id=sent.message_id, text=text, by=by)
    return sent


async def stale(session: AsyncSession, draft: AgentDraftModel, thread_id: int) -> str | None:
    """Почему черновик устарел — или `None`, если переписка с него не сдвинулась.

    Черновик написан на ответ `draft.reply_id` (`based_on`). Устарел он, если
    после этого ответа в переписке есть новое письмо собеседника — ответ или
    отписка, как у цепочки (`attempts.ANSWERS`; автоответчик и отказ доставки
    не в счёт) — или наше письмо: на сам ответ или позже, или ушедшее позже
    него. «После ответа» у писем собеседника — по номеру, как переписку видит
    писатель (`drafting._turns`), у наших — по ответу, на который письмо, и по
    времени отправки.
    """
    newer = await session.scalar(
        select(ReplyModel.id)
        .where(
            ReplyModel.thread_id == thread_id,
            ReplyModel.id > draft.reply_id,
            ReplyModel.kind.in_(ANSWERS),
        )
        .limit(1)
    )
    if newer is not None:
        return f"после письма, на которое он написан, собеседник написал ещё (ответ №{newer})"
    received = await session.scalar(
        select(ReplyModel.created_at).where(ReplyModel.id == draft.reply_id)
    )
    ours = await session.scalar(
        select(MessageModel.id)
        .where(
            MessageModel.thread_id == thread_id,
            MessageModel.status.in_(_OUT),
            or_(
                MessageModel.answers_reply_id >= draft.reply_id,
                MessageModel.sent_at > received,
            ),
        )
        .limit(1)
    )
    if ours is not None:
        return f"после письма, на которое он написан, в переписке уже есть наше письмо №{ours}"
    return None


async def settle_sent(
    session: AsyncSession, reply_id: int, *, message_id: int, text: str, by: Decider
) -> AgentDraftModel | None:
    """Ответ ушёл — черновик к этому ответу закрыт как отправленный.

    Черновика нет или по нему уже решили — ничего не меняется: ответ,
    написанный поверх отклонённого черновика, остаётся отклонением.
    """
    draft = await session.scalar(
        select(AgentDraftModel).where(AgentDraftModel.reply_id == reply_id)
    )
    if draft is None or draft.status in DECIDED:
        return None
    draft.status = DraftStatus.SENT
    draft.final_body = text.strip()
    draft.edited = draft.final_body != draft.body.strip()
    draft.sent_message_id = message_id
    await _decided(
        session, draft, by, "отправлен с правкой" if draft.edited else "отправлен как есть"
    )
    return draft


async def reject_draft(
    session: AsyncSession, draft_id: int, *, reason: str, by: Decider
) -> AgentDraftModel:
    """Отклонить черновик — с причиной словами; вид причины — пункт списка этапа.

    У этапа со строгим списком (`AgentStage.strict_reasons`) причина — только пункт
    списка или «другое: …» словами: иначе `DraftReasonError` (422), черновик цел.
    """
    why = reason.strip()
    if not why:
        raise DraftReasonError("Отклонить черновик можно только с причиной")
    draft = _undecided((await one(session, draft_id)).draft)
    stage = await session.scalar(
        select(AgentSettingsModel.stage).where(AgentSettingsModel.id == draft.settings_id)
    )
    parts = None if stage is None else AGENT_STAGES.get(stage)
    kind = _kind(why, () if parts is None else parts.reject_reasons)
    if kind is None and parts is not None and parts.strict_reasons:
        raise DraftReasonError(_unlisted(why, parts.reject_reasons))
    draft.status = DraftStatus.REJECTED
    draft.reject_reason = why
    draft.reject_kind = kind
    await _decided(session, draft, by, "отклонён")
    return draft


def _kind(reason: str, listed: Sequence[str]) -> str | None:
    """Вид причины: пункт списка этапа — он сам, «другое: …» со словами — «другое»;
    иначе вида нет (причина своими словами у этапа без строгого списка)."""
    if reason in listed:
        return reason
    head, colon, said = reason.partition(":")
    return OTHER_REASON if colon and head.strip() == OTHER_REASON and said.strip() else None


def _unlisted(reason: str, listed: Sequence[str]) -> str:
    """Почему причина не принята — словами, со списком этапа."""
    if reason.partition(":")[0].strip() == OTHER_REASON:
        return f"Причина «{OTHER_REASON}» — только со словами: «{OTHER_REASON}: что не так»"
    return (
        "Отклонить черновик этого этапа можно только с причиной из списка: "
        f"{'; '.join(listed)} — или «{OTHER_REASON}: …» своими словами"
    )


def _refuse_as_is(draft: AgentDraftModel, body: str | None) -> None:
    """«Как есть» уходит только готовый черновик с текстом.

    У отданного человеку «как есть» закрыто и правкой тем же текстом (после
    обрезки пробелов): иначе запрет обходится одним запросом с прежним текстом.
    """
    unchanged = body is None or body.strip() == draft.body.strip()
    if draft.status is DraftStatus.ESCALATED and unchanged:
        raise DraftDecisionError(
            f"Черновик №{draft.id} отдан человеку ({draft.reason or 'без причины'}) — "
            "как есть он не уходит, и прежним текстом через правку тоже: поправьте текст "
            "или ответьте сами"
        )
    if body is None and not draft.body.strip():
        raise DraftDecisionError(f"В черновике №{draft.id} нет текста — отправлять нечего")


def _undecided(draft: AgentDraftModel) -> AgentDraftModel:
    if draft.status in DECIDED:
        raise DraftDecisionError(
            f"По черновику №{draft.id} уже решили ({draft.status.value}) — второе решение не ляжет"
        )
    if draft.status is DraftStatus.SKIPPED:
        raise DraftDecisionError(
            f"Черновик №{draft.id} пропущен ({draft.reason}) — решать по нему нечего"
        )
    return draft


async def _decided(session: AsyncSession, draft: AgentDraftModel, by: Decider, what: str) -> None:
    draft.decided_by = by.name
    draft.decided_at = datetime.now(UTC)
    await session.flush()
    await AccessRepository(session).record(
        AuditAction.AGENT_DRAFT_DECIDED,
        author_id=by.user_id,
        target=f"agent_draft:{draft.id}",
        details={
            "решение": what,
            "ответ": draft.reply_id,
            "письмо": draft.sent_message_id,
            "причина": draft.reject_reason,
            "вид причины": draft.reject_kind,
            "кто": by.name,
        },
    )
