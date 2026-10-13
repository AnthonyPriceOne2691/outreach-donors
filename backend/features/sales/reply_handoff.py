"""Передача лида по ответу — и чем она кончилась словами в снимке ответа (`model_parse`).

Передача одна (`handoff.start`: одна на диалог, задача Kommo и Telegram), триггеров три:
вид ответа «хочет говорить», новое письмо лида, уже переданного телемаркетологу, и ситуация
агента «хочет говорить» (`agent/handover.py`). Общее у них здесь: передача — после записи
вида, итог — словами в снимке ответа: ждёт ли он человека и почему (`outcome.sales_review`).

**Новое письмо переданного лида** (`Ask.again`) ждать человека в сервисе не должно: оно ушло
той же передачей — примечанием к той же сделке (через шлюз — сообщением со ссылкой на неё, без
сделки — ссылкой на диалог) и сообщением телемаркетологу «Лид написал снова». Вопрос, интерес и
«хочет говорить» снимаются с ожидания словами «передан телемаркетологу — письмо ушло в сделку»
(сделки нет — «…ушло ему ссылкой на диалог»). Остальные пути говорят своё и через «;» — куда
ушло письмо; вид, который не разобран, по-прежнему ждёт человека: без него, например, не встанет
стоп-лист по неуверенной отписке.

**Отказ передачи разбор не роняет**: вид записан, ответ ждёт человека, а почему — словами в
снимке (ревью стыков, B5). Сессия после сбоя базы не годится и для этой записи — тогда причина
только в журнале.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from sqlalchemy import exists, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.sales.handoff import HandoffError
from backend.features.sales.models import SalesHandoffModel
from backend.features.sales.reply_routes import Route

logger = logging.getLogger(__name__)

#: Чем кончился путь передачи, когда она заведена, — словами.
HANDED_OVER = "передан телемаркетологу"
#: И когда передачи нет — дальше в причине словами, почему.
NOT_HANDED_OVER = "передать лида не вышло"
#: Новое письмо переданного лида ушло той же передачей: в сделку — или ссылкой на диалог.
FORWARDED = f"{HANDED_OVER} — письмо ушло в сделку"
FORWARDED_LINK = f"{HANDED_OVER} — письмо ушло ему ссылкой на диалог"
NOT_FORWARDED = "письмо телемаркетологу не ушло"

#: Пути, где ответ ждал бы агента или передачи: у переданного лида не ждёт ни того, ни другого.
_SETTLED_BY_FORWARD = frozenset({Route.AGENT, Route.HANDOFF})

#: Передача лида по диалогу — `handoff.start(session, thread_id)`: коммитит сессию сама.
HandOver = Callable[[AsyncSession, int], Awaitable[object]]


@dataclass(frozen=True, slots=True)
class Ask:
    """Что передать и как сказать итог."""

    reply_id: int
    thread_id: int
    #: Вид словами — начало причины: «хочет говорить», «агент понял: хочет говорить».
    words: str
    #: Между видом и итогом: «хочет говорить: передан…», «агент понял: хочет говорить — передан…».
    joint: str = ": "
    #: Лид уже передан: письмо уходит той же передачей.
    again: bool = False
    #: Путь ответа и что о нём сказано до передачи — у письма переданного лида.
    route: Route | None = Route.HANDOFF
    waits: bool = True
    reason: str = ""


@dataclass(frozen=True, slots=True)
class Said:
    """Чем кончилось: ждёт ли ответ человека и почему; `handed` — передача заведена."""

    waits: bool
    reason: str
    handed: bool = True


async def handed(session: AsyncSession, thread_id: int | None) -> bool:
    """Передан ли лид этого диалога: передача заведена (ключ передачи — диалог)."""
    if thread_id is None:
        return False
    return bool(
        await session.scalar(select(exists().where(SalesHandoffModel.thread_id == thread_id)))
    )


async def pass_on(session: AsyncSession, ask: Ask, start: HandOver) -> Said | None:
    """Передать лида и записать итог в снимок ответа. `None` — снимок не записать: причина
    только в журнале, а вызывающий оставляет свой итог как был.

    Итог «передан» ложится в снимок до передачи и уходит в базу её коммитом — вместе со строкой
    передачи (`handoff.start` коммитит сам). Вторым коммитом он пропадал бы при сбое базы между
    ними: лид у телемаркетолога, а ответ ждёт человека, и повтор задачи его уже не берёт — «уже
    разобран» (ревью стыков, хвост R2 7.1). Отказ передачи итог перезаписывает (`_refused`)."""
    try:
        said = await _settled(session, ask)
        reply = await session.get(ReplyModel, ask.reply_id)
        if reply is not None:
            reply.model_parse = _noted(reply, said)
        await start(session, ask.thread_id)
    except Exception as exc:
        logger.exception(
            "продажи: передача лида не заведена — ответ разобран и ждёт человека",
            extra={"reply": ask.reply_id, "thread_id": ask.thread_id},
        )
        return await _refused(session, ask, exc)
    await session.commit()  # передача без новой работы (`handoff._reopen`) сама не коммитит
    return said


async def _settled(session: AsyncSession, ask: Ask) -> Said:
    """Передача заведена — что сказать. Письмо переданного лида: в сделку, если она есть."""
    if not ask.again:
        return Said(False, f"{ask.words}{ask.joint}{HANDED_OVER}")
    deal = await session.scalar(
        select(SalesHandoffModel.kommo_lead_id).where(SalesHandoffModel.thread_id == ask.thread_id)
    )
    forwarded = FORWARDED if deal else FORWARDED_LINK
    if ask.route in _SETTLED_BY_FORWARD:
        return Said(False, f"{ask.words}{ask.joint}{forwarded}")
    return Said(ask.waits, f"{ask.reason}; {forwarded}")


async def _refused(session: AsyncSession, ask: Ask, exc: Exception) -> Said | None:
    """Передачи нет: ответ ждёт человека, а почему — словами в снимке. Отказ передачи
    (`HandoffError`) говорит сам; чужой сбой — тип и журнал."""
    why = (
        str(exc)
        if isinstance(exc, HandoffError)
        else f"сбой ({type(exc).__name__}), подробности — в журнале задачи"
    )
    if not ask.again:
        reason = f"{ask.words}{ask.joint}{NOT_HANDED_OVER} — {why}; решает человек"
    elif ask.route in _SETTLED_BY_FORWARD:
        reason = f"{ask.words}{ask.joint}{NOT_FORWARDED} — {why}; решает человек"
    else:
        reason = f"{ask.reason}; {NOT_FORWARDED} — {why}"
    said = Said(True, reason, handed=False)
    try:
        reply = await session.get(ReplyModel, ask.reply_id)
        if reply is None:
            return said
        reply.model_parse = {**_noted(reply, said), "handoff_error": why}
        await session.commit()
    except SQLAlchemyError:
        logger.exception(
            "продажи: почему передачи нет — в снимок ответа не записать, причина в журнале",
            extra={"reply": ask.reply_id},
        )
        return None
    return said


def _noted(reply: ReplyModel, said: Said) -> dict[str, object]:
    """Снимок ответа с итогом передачи. Снимка ещё нет (потолок модели) — заводится записка;
    отказ прошлой попытки (`handoff_error`) снимается: итог — этой."""
    base = reply.model_parse or {"stage": Stage.SALES.value, "kind": None}
    kept = {key: value for key, value in base.items() if key != "handoff_error"}
    return {**kept, "waits": said.waits, "reason": said.reason}
