"""Черновик ответа собеседнику: кому он положен, из чего пишется, где лежит.

Решение владельца 04.10.2026: агент готовит черновик, человек отправляет его
как есть, с правкой или отклоняет. Здесь только черновик: письма агент не
отправляет и в очередь не ставит.

**Кому черновик положен.** Ответу человека в привязанной переписке, на
который ещё не ответили, на этапе из реестра (`agent/stages.AGENT_STAGES`),
где агент настроен и включён. Отписка, закрытая переписка, автоответчик —
без черновика: отвечать им не будут, и платить модели не за что. Причина
пропуска называется словами — вызвавший кладёт её в итог или в отказ.

**Бриф этапа — до модели.** Этап может сказать «ответ не нужен» (черновик
`skipped`) или «не берусь» (`escalated` без текста) — модель тогда не
зовётся; его факты уходят писателю, его `meta` ложится в черновик.

**Из чего пишется.** Переписка целиком, в порядке времени: наши ушедшие
письма и ответы человека после общей очистки (`agent/cleaning.py`: невидимые
знаки, разметка ролей модели, цитата и подпись); разобранная цена последнего
ответа, если она есть; настройки этапа. Судья этапа и петля правки —
`agent/guarding.py`.

**Потолок расхода на модель** проверяется до вызова, как у разбора ответа,
и расход пишется в журнал операцией этапа.

**Модель черновик не написала** — человеку черновик без текста с причиной (`unwritten`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.features.agent import guarding
from backend.features.agent.cleaning import clean
from backend.features.agent.settings import AgentSettingsRepository, settings_of
from backend.features.agent.stages import (
    AGENT_STAGES,
    AgentStage,
    Brief,
    Conversation,
    DraftNotice,
    SkipKind,
)
from backend.features.agent.writer import Request, Turn, Written
from backend.features.core.domain import (
    DraftStatus,
    MessageStatus,
    ReplyKind,
    Stage,
    ThreadStatus,
)
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)

logger = logging.getLogger(__name__)

#: Писем переписки в запросе — последние. Дальше в прошлое — повторы
#: того же торга, а платится за каждый токен.
MAX_TURNS = 8
#: Знаков одного письма в запросе. Длиннее — почти всегда вставленный
#: документ или прайс, и агенту хватает его начала.
MAX_TURN_CHARS = 3000

#: Письма, которые собеседник получил: их текст и есть наша сторона разговора.
_DELIVERED = (MessageStatus.SENT, MessageStatus.DELIVERED)
_CLOSED = (ThreadStatus.UNSUBSCRIBED, ThreadStatus.CLOSED)
#: По этим черновикам решение принято: «написать заново» их не трогает.
DECIDED = (DraftStatus.SENT, DraftStatus.REJECTED)
#: О каких черновиках говорит крючок уведомления: по ним ждут человека.
_ANNOUNCED = (DraftStatus.DRAFTED, DraftStatus.ESCALATED)
_EPOCH = datetime.min.replace(tzinfo=UTC)
#: Начало причины черновика, который модель не написала (`unwritten`).
UNWRITTEN = "черновик не написан"
#: Ответ, которому черновик положен: его переписка, части этапа и настройки этапа.
type _Ready = tuple[_Target, AgentStage, AgentSettingsModel]


class UnknownDraftReplyError(LookupError):
    """Ответа с таким номером нет — писать черновик не к чему."""


class DraftRefusedError(RuntimeError):
    """Черновик этому ответу не положен; сообщение говорит почему."""


class Writer(Protocol):
    """Тот, кто пишет черновик: модель или её замена в тестах. Модель и
    промпт этапа приходят в запросе (`Request.model`, `Request.prompt`)."""

    async def write(self, request: Request) -> Written: ...


@dataclass(frozen=True, slots=True)
class DraftOutcome:
    """Чем кончилось: черновик записан — или почему его нет (`skipped`)."""

    reply_id: int
    notice: DraftNotice | None = None
    skipped: str | None = None
    tokens: int = 0

    @property
    def draft_id(self) -> int | None:
        return None if self.notice is None else self.notice.draft_id

    @property
    def status(self) -> DraftStatus | None:
        return None if self.notice is None else self.notice.status


@dataclass(frozen=True, slots=True)
class _Target:
    reply: ReplyModel
    thread: ThreadModel
    stage: Stage


@dataclass(frozen=True, slots=True)
class _Draft:
    """Что ложится в строку черновика."""

    status: DraftStatus
    body: str
    reason: str | None
    meta: Mapping[str, Any]
    tokens: int


async def draft_answer(
    session: AsyncSession,
    writer: Writer,
    reply_id: int,
    *,
    again: bool = False,
    force: bool = False,
) -> DraftOutcome:
    """Написать черновик ответа на ответ `reply_id` и записать его.

    `again` — человек попросил написать заново: черновик переписывается, пока
    по нему не решили. Без него уже написанный не трогается — повтор задачи
    очереди не платит модели второй раз. `force` — «всё же написать»: пропуск
    брифа не останавливает модель, а ложится в `meta`.
    """
    ready = await _ready(session, reply_id, again=again)
    if isinstance(ready, str):
        return DraftOutcome(reply_id=reply_id, skipped=ready)
    target, stage, settings = ready

    turns, cleaned = await _turns(session, target)
    conversation = Conversation(
        stage=target.stage,
        thread_id=target.thread.id,
        reply_id=reply_id,
        turns=turns,
        settings=settings_of(settings),
        cleaned=cleaned,
    )
    brief = await stage.brief(session, conversation)
    if brief.skip is not None and not force:
        draft = _skipped(brief)
    else:
        draft = await _written(session, stage, writer, conversation, target.reply, brief)
    return await _recorded(session, ready, draft)


async def unwritten(session: AsyncSession, reply_id: int, why: str) -> DraftOutcome:
    """Модель не написала черновик, и повтора не будет: черновик `escalated` без текста
    с причиной «черновик не написан: …»; фиксация и крючок этапа — у вызывающего. Ответ,
    которому черновик уже не положен (ответили, решили, написан кнопкой), не трогается."""
    ready = await _ready(session, reply_id, again=False)
    if isinstance(ready, str):
        return DraftOutcome(reply_id=reply_id, skipped=ready)
    reason = f"{UNWRITTEN}: {why}"
    draft = _Draft(status=DraftStatus.ESCALATED, body="", reason=reason, meta={}, tokens=0)
    return await _recorded(session, ready, draft)


async def _ready(session: AsyncSession, reply_id: int, *, again: bool) -> _Ready | str:
    """Положен ли ответу черновик: его переписка, этап и настройки — или почему нет, словами."""
    target = await _target(session, reply_id)
    if target is None:
        return "ответ не привязан к переписке"
    stage = AGENT_STAGES.get(target.stage)
    settings = await AgentSettingsRepository(session).current(target.stage)
    why = _refusal(target, stage, settings) or await _done(session, reply_id, again=again)
    if why is not None or stage is None or settings is None:
        return why or "агент на этом этапе не работает"
    return target, stage, settings


async def _recorded(session: AsyncSession, ready: _Ready, draft: _Draft) -> DraftOutcome:
    """Черновик — в строку ответа; итог — с тем, что о нём узнает крючок этапа."""
    target, stage, settings = ready
    draft_id = await _store(session, target.reply.id, settings.id, draft, stage)
    notice = DraftNotice(
        draft_id=draft_id,
        reply_id=target.reply.id,
        thread_id=target.thread.id,
        stage=target.stage,
        status=draft.status,
        reason=draft.reason,
    )
    return DraftOutcome(reply_id=target.reply.id, notice=notice, tokens=draft.tokens)


async def announce(outcome: DraftOutcome) -> None:
    """Крючок этапа — после записи черновика и коммита: о черновике, который ждёт человека, —
    `on_draft`, о пропуске — `on_skip`, если этапу по нему есть что делать.

    Его сбой черновик не теряет: черновик уже в базе и виден в переписке, а сбой — в логе
    словами без догадки, что делал крючок (сообщение о черновике, передача лида), с именем
    крючка в `extra`. Повторы — забота крючка (у Telegram они свои).
    """
    notice = outcome.notice
    stage = None if notice is None else AGENT_STAGES.get(notice.stage)
    if notice is None or stage is None:
        return
    if notice.status is DraftStatus.SKIPPED:
        name, hook = "on_skip", stage.on_skip
    else:
        name, hook = "on_draft", stage.on_draft if notice.status in _ANNOUNCED else None
    if hook is None:
        return
    try:
        await hook(notice)
    except Exception:
        logger.exception(
            "агент: крючок этапа %s после черновика №%s упал — черновик цел",
            name,
            notice.draft_id,
            extra={"draft": notice.draft_id, "reply": notice.reply_id, "hook": name},
        )


async def wants_draft(session: AsyncSession, reply_id: int) -> bool:
    """Положен ли ответу черновик — до постановки задачи.

    Без агента на этапе задача не ставится вовсе: очередь не копит задачи,
    которым заведомо нечего делать. Отвечен ли ответ и есть ли черновик,
    решает сама задача — к её часу это может измениться.
    """
    target = await _target(session, reply_id)
    if target is None:
        return False
    settings = await AgentSettingsRepository(session).current(target.stage)
    return _refusal(target, AGENT_STAGES.get(target.stage), settings) is None


def _skipped(brief: Brief) -> _Draft:
    """Бриф решил не писать: `no_reply` — пропуск, `human` — человеку без текста."""
    assert brief.skip is not None
    status = DraftStatus.SKIPPED if brief.skip.kind is SkipKind.NO_REPLY else DraftStatus.ESCALATED
    meta = {**brief.meta, "skip": brief.skip.kind.value}
    return _Draft(status=status, body="", reason=brief.skip.reason, meta=meta, tokens=0)


async def _written(
    session: AsyncSession,
    stage: AgentStage,
    writer: Writer,
    conversation: Conversation,
    reply: ReplyModel,
    brief: Brief,
) -> _Draft:
    """Черновик модели по настройкам, переписке и фактам брифа."""
    meta: dict[str, Any] = dict(brief.meta)
    if conversation.cleaned:
        meta["cleaned"] = list(conversation.cleaned)
    if brief.skip is not None:  # «всё же написать» поверх пропуска брифа
        meta["forced_over_skip"] = {"kind": brief.skip.kind.value, "reason": brief.skip.reason}
    sign_as = outreach_cfg.SENDER_NAME.strip() if brief.sign_as is None else brief.sign_as
    composed = await guarding.compose(
        session,
        stage,
        writer,
        Request(
            stage=conversation.stage,
            settings=conversation.settings,
            turns=conversation.turns,
            sign_as=sign_as,
            parsed=_parsed(reply),
            facts=brief.facts,
            prompt=stage.prompt,
            model=stage.model,
            names=await _hidden_names(session, conversation, sign_as),
        ),
        incoming=_last_incoming(conversation.turns),
    )
    if composed.attempts:
        meta["attempts"] = composed.attempts
    return _Draft(
        status=DraftStatus.ESCALATED if composed.held else DraftStatus.DRAFTED,
        body=composed.body,
        reason=composed.reason,
        meta=meta,
        tokens=composed.tokens,
    )


#: Этапы, на которых черновик прячет от модели имена — наше и собеседников (12.10.2026,
#: «адреса и имена заменяются метками»). У продаж своё имя отправителя и свой контур
#: проверок — их запрос остаётся прежним.
_NAMED_STAGES = frozenset({Stage.DONORS, Stage.ADVERTISERS})


async def _hidden_names(
    session: AsyncSession, conversation: Conversation, sign_as: str
) -> tuple[str, ...]:
    """Имена, которые модель не видит: наше, которым подписан черновик, и имена
    собеседников из «От кого» их ответов в этой переписке."""
    if conversation.stage not in _NAMED_STAGES:
        return ()
    theirs = await session.scalars(
        select(ReplyModel.from_name)
        .where(ReplyModel.thread_id == conversation.thread_id, ReplyModel.from_name.is_not(None))
        .distinct()
    )
    return (sign_as, *(name for name in theirs if name))


def _last_incoming(turns: tuple[Turn, ...]) -> str:
    """Письмо собеседника, на которое отвечаем, — для судьи."""
    return next((turn.text for turn in reversed(turns) if not turn.ours), "")


async def _target(session: AsyncSession, reply_id: int) -> _Target | None:
    """Ответ с его перепиской и этапом. `None` — ответ ни к чему не привязан."""
    reply = await session.get(ReplyModel, reply_id)
    if reply is None:
        raise UnknownDraftReplyError(f"Ответа №{reply_id} нет — черновик писать не к чему")
    row = (
        await session.execute(
            select(ThreadModel, CampaignModel.stage)
            .join(CampaignModel, CampaignModel.id == ThreadModel.campaign_id)
            .where(ThreadModel.id == reply.thread_id)
        )
    ).first()
    if row is None:
        return None
    thread, stage = row
    return _Target(reply=reply, thread=thread, stage=stage)


def _refusal(
    target: _Target, stage: AgentStage | None, settings: AgentSettingsModel | None
) -> str | None:
    """Почему черновик не положен — по самому ответу, этапу и его настройкам."""
    if target.reply.kind is not ReplyKind.HUMAN:
        return f"это не письмо человека ({target.reply.kind.value})"
    if target.thread.status in _CLOSED:
        return f"переписка {target.thread.status.value} — отвечать в неё не будут"
    if stage is None:
        return f"на этапе «{target.stage.value}» агент переписку не ведёт"
    if settings is None:
        return "агент на этом этапе не настроен"
    if not settings.enabled:
        return "агент на этом этапе выключен"
    return None


async def _done(session: AsyncSession, reply_id: int, *, again: bool) -> str | None:
    """Ответ уже отвечен, по черновику решили — или он есть, а заново не просили."""
    answered = await session.scalar(
        select(MessageModel.id).where(MessageModel.answers_reply_id == reply_id).limit(1)
    )
    if answered is not None:
        return "на этот ответ уже ответили"
    status = await session.scalar(
        select(AgentDraftModel.status).where(AgentDraftModel.reply_id == reply_id)
    )
    if status in DECIDED:
        return f"по черновику уже решили ({status.value}) — заново он не пишется"
    if status is None or again:
        return None
    return "черновик уже написан"


async def _turns(
    session: AsyncSession, target: _Target
) -> tuple[tuple[Turn, ...], tuple[str, ...]]:
    letters = (
        await session.execute(
            select(MessageModel.sent_at, MessageModel.body).where(
                MessageModel.thread_id == target.thread.id,
                MessageModel.status.in_(_DELIVERED),
                MessageModel.body.is_not(None),
            )
        )
    ).all()
    replies = (
        await session.execute(
            select(ReplyModel.created_at, ReplyModel.raw_body).where(
                ReplyModel.thread_id == target.thread.id,
                ReplyModel.kind == ReplyKind.HUMAN,
                ReplyModel.id <= target.reply.id,
            )
        )
    ).all()
    return conversation([(at, body) for at, body in letters], [(at, raw) for at, raw in replies])


def conversation(
    letters: Sequence[tuple[datetime | None, str | None]],
    replies: Sequence[tuple[datetime, str]],
) -> tuple[tuple[Turn, ...], tuple[str, ...]]:
    """Переписка по времени: последние `MAX_TURNS` писем, каждое — с начала.

    Письма собеседника — после общей очистки (`agent/cleaning.py`); что она
    убрала — вторым значением, без повторов.
    """
    cleaned = [(at, clean(raw)) for at, raw in replies]
    timed = [(at or _EPOCH, Turn(ours=True, text=body or "")) for at, body in letters]
    timed += [(at, Turn(ours=False, text=found.text)) for at, found in cleaned]
    timed.sort(key=lambda pair: pair[0])
    turns = tuple(
        Turn(ours=turn.ours, text=turn.text[:MAX_TURN_CHARS]) for _, turn in timed[-MAX_TURNS:]
    )
    notes = dict.fromkeys(note for _, found in cleaned for note in found.notes)
    return turns, tuple(notes)


def _parsed(reply: ReplyModel) -> dict[str, str]:
    """Что разбор уже достал из ответа — модель не пересчитывает это заново."""
    found = {
        "price_white": reply.price_white,
        "price_grey": reply.price_grey,
        "currency": reply.currency,
        "placement": reply.placement,
    }
    return {name: str(value) for name, value in found.items() if value is not None}


def _plain(meta: Mapping[str, Any]) -> dict[str, Any]:
    """`meta` брифа — тем видом, что ляжет в JSONB: суммы и даты — строками."""
    loaded: dict[str, Any] = json.loads(json.dumps(dict(meta), ensure_ascii=False, default=str))
    return loaded


async def _store(
    session: AsyncSession,
    reply_id: int,
    settings_id: int,
    draft: _Draft,
    stage: AgentStage,
) -> int:
    """Черновик ответа — один: новый переписывает прежний одной вставкой.

    Задача очереди и кнопка «Написать заново» могут сойтись на одном ответе;
    вставка с заменой не даёт им упереться друг в друга. Переписывается
    только нерешённый черновик (`_done`), и решение при этом обнуляется.
    """
    values = {
        "settings_id": settings_id,
        "status": draft.status,
        "body": draft.body,
        "reason": draft.reason,
        "meta": _plain(draft.meta),
        "model": stage.model,
        "prompt_version": stage.prompt_version,
        "tokens": draft.tokens,
        "final_body": None,
        "edited": None,
        "decided_by": None,
        "decided_at": None,
        "reject_reason": None,
        "reject_kind": None,
        "sent_message_id": None,
    }
    upsert = (
        insert(AgentDraftModel)
        .values(reply_id=reply_id, **values)
        .on_conflict_do_update(
            constraint="uq_agent_drafts_reply",
            set_={**values, "updated_at": datetime.now(UTC)},
        )
        .returning(AgentDraftModel.id)
    )
    draft_id = await session.scalar(upsert)
    assert draft_id is not None  # вставка с заменой всегда возвращает строку
    return int(draft_id)
