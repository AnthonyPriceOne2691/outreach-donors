"""«Закрыть без ответа» и ответ в переписке — решения человека у ответа продаж, который ждёт человека.

Слово владельца 09.11.2026. Подтверждения заявок служб поддержки стояли ответом человека: правило
«подтверждение заявки» пришло позже, а вид принятого ответа задним числом не пересчитывается. Снять
их с ожидания было нечем: у переписки продаж нет формы решения (`ThreadPage`: `reviewable = !sales
&& …`), а ожидание снимала только передача телемаркетологу (`handoff_manual`) — она завела бы сделку
в Kommo на службу поддержки. Ответ человека в переписке ожидание тоже не снимал: диалог, где лиду уже
ответили, оставался «ждёт человека».

**Что снимается.** Ответы человека этого диалога, которые ждут человека (`threads.review_of`), с
номером не больше ответа, который человек видел (`reply_id`): «после ответа» у писем собеседника —
по номеру, как у черновика (`agent.drafts.stale`). Пришедший позже человек не читал — тот ждёт
дальше. Снимок — словами решения (`handoff_manual.release`: «закрыт без ответа: <кто>», «ответили в
переписке: <кто>»), поля решения человека (`reviewed_by`, `reviewed_at`) — как у передачи: задача
разбора такой ответ больше не берёт («решён человеком»), лента показывает слова итогом под ответом
(`ThreadFeed`).

**Писем лиду и так нет**: ответ человека остановил цепочку при приёме (`outcome._human_answer`), и
решение её не трогает. Сделки в Kommo и сообщения в Telegram нет — это не передача. Вид ответа не
меняется: в воронке он остаётся ответом.

**Отказы «Закрыть без ответа» — словами и до записи**: диалога нет; диалог не продаж; ответа с таким
номером в диалоге нет; ничего не ждёт человека — повтор ничего не меняет. Ответ в переписке не
отказывает: письмо к этому месту уже ушло (`api/sales/close.release_answered`).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.outreach import stage_of
from backend.features.outreach.threads import review_of
from backend.features.sales.handoff_manual import release

logger = logging.getLogger(__name__)

#: Чем ответ снят с ожидания — словами решения; дальше — кто решил.
CLOSED = "закрыт без ответа"
ANSWERED = "ответили в переписке"


class CloseError(RuntimeError):
    """Закрыть нельзя — словами, почему и что делать."""


class UnknownThreadError(CloseError):
    """Диалога с таким номером нет."""


class CloseRefusedError(CloseError):
    """Диалог есть, но закрывать в нём нечего."""


@dataclass(frozen=True, slots=True)
class Closed:
    """Чем кончилось нажатие: какие ответы сняты с ожидания и сколько пришедших позже ждут дальше."""

    released: tuple[int, ...]
    later: int


async def close(
    session: AsyncSession, thread_id: int, *, reply_id: int, by: str, now: datetime | None = None
) -> Closed:
    """Закрыть без ответа: снять с ожидания ответы диалога по `reply_id` включительно.

    Коммитит сессию."""
    stage = await stage_of.of_thread(session, thread_id)  # номер больше столбца — тоже «нет»
    if stage is None:
        raise UnknownThreadError(f"диалога №{thread_id} нет — закрывать нечего")
    if stage is not Stage.SALES:
        raise CloseRefusedError(
            f"диалог №{thread_id} — не продаж: без ответа закрывают только ответ лида продаж"
        )
    replies = await _replies(session, thread_id)
    if not _has(replies, reply_id):
        raise CloseRefusedError(
            f"ответа №{reply_id} в диалоге №{thread_id} нет — откройте переписку заново"
        )
    at = now or datetime.now(UTC)
    released = _release_until(replies, reply_id, f"{CLOSED}: {by}", by=by, at=at)
    if not released:
        raise CloseRefusedError(
            f"в диалоге №{thread_id} ответ №{reply_id} и ответы до него уже не ждут человека — "
            "закрывать нечего"
        )
    await session.commit()
    later = sum(1 for reply in replies if reply.id > reply_id and _waits(reply))
    logger.info(
        "продажи: ответ закрыт без ответа",
        extra={"thread_id": thread_id, "released": released, "later": later},
    )
    return Closed(released, later)


async def answered(
    session: AsyncSession, thread_id: int, *, reply_id: int, by: str, now: datetime | None = None
) -> tuple[int, ...]:
    """Человек ответил в переписке на `reply_id`: снять с ожидания ответы продаж по него включительно.

    Переписка не продаж или ответ не этой переписки — ничего. Не коммитит: точку сохранения держит
    вызывающий."""
    if await stage_of.of_thread(session, thread_id) is not Stage.SALES:
        return ()
    replies = await _replies(session, thread_id)
    if not _has(replies, reply_id):
        return ()
    at = now or datetime.now(UTC)
    return _release_until(replies, reply_id, f"{ANSWERED}: {by}", by=by, at=at)


def said(done: Closed) -> str:
    """Итог нажатия словами — строкой под кнопкой и уведомлением."""
    count = len(done.released)
    head = "Закрыто без ответа" if count == 1 else f"Закрыто без ответа (ответов — {count})"
    if done.later == 0:
        tail = "переписка больше не ждёт человека"
    elif done.later == 1:
        tail = "новый ответ лида пришёл позже и ждёт человека"
    else:
        tail = f"пришедшие позже ответы лида ждут человека — {done.later}"
    return f"{head}: {tail}."


async def _replies(session: AsyncSession, thread_id: int) -> Sequence[ReplyModel]:
    """Ответы диалога по номеру — в том порядке, в каком их видит переписка."""
    found = await session.scalars(
        select(ReplyModel).where(ReplyModel.thread_id == thread_id).order_by(ReplyModel.id)
    )
    return found.all()


def _has(replies: Sequence[ReplyModel], reply_id: int) -> bool:
    return any(reply.id == reply_id for reply in replies)


def _waits(reply: ReplyModel) -> bool:
    return review_of(reply, Stage.SALES).waiting


def _release_until(
    replies: Sequence[ReplyModel], reply_id: int, words: str, *, by: str, at: datetime
) -> tuple[int, ...]:
    """Ждущие ответы по `reply_id` включительно — словами решения; остальные остаются как были."""
    return tuple(
        reply.id
        for reply in replies
        if reply.id <= reply_id and release(reply, words, by=by, at=at)
    )
