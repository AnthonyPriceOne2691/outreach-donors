"""Письмо, отбитое молчащим DNS получателя, возвращается в работу (02.11.2026).

Отказ гасит цепочку (`events._bounced`): первое письмо кончается, у добивки срок погашен, а лид
остаётся жив — и молчит, потому что написать ему больше некому. Проверка перед отправкой
(`recipient_dns.py`) такие отказы почти убирает, но не все: у платформы свои резолверы, и домен,
ответивший нам, ей мог не ответить, а письма, отбитые до неё, уже лежат отказами. Чужой сбой не
должен стоить разговора: проход возвращает такие письма туда, откуда они ушли.

**Тем же путём, что «Вернуть в очередь»** (`unknown_outcome._back`): первое письмо — в очередь, его
заберёт сама очередь; добивке возвращается срок на предыдущем письме, и её заберёт проход добивок
с ящика переписки; цепочка кончилась (адресат ответил, отписался) — добивка не возвращается. Ответ
и перезапрос цены не возвращаются вовсе: их отправил человек, и решать снова — ему. Дальше письма
судит обычная отправка: DNS молчит — ждёт, домен в стоп-листе — стоит.

**Не сразу.** Письмо в очереди с событием платформы считается ушедшим (`events._settle_if_pending`:
событие доказывает приём), и запоздавшая отсрочка той же попытки сняла бы его с очереди. События
одной попытки приходят минутами: `SETTLE` их пережидает.

**Один раз и недолго.** Тот же текст отказа дают и домены со сломанной записью MX: DNS им отвечает,
проверка письмо пропускает, платформа отказывает снова. Вторая попытка отличает одно от другого,
третья — письмо по кругу. Что письмо уже возвращали, видно без отдельной колонки: его первая
отсрочка (`deferred_since`) остаётся и старше нынешней отправки. Поэтому она при возврате не
очищается, а если отсрочек не было — ставится. Дольше `WINDOW` от начала беды письмо не возвращается.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import settle
from backend.features.letters.chain import MAX_STEPS
from backend.features.letters.delivery_trouble import Trouble, refused_kind
from backend.features.letters.unknown_outcome import alive_link, is_followup

logger = logging.getLogger(__name__)

#: Сколько ждать после отправки: события одной попытки приходят минутами (шапка модуля).
SETTLE = timedelta(minutes=30)

#: Дольше этого от начала беды письмо не возвращается.
WINDOW = timedelta(days=3)

#: Первая отсрочка старше отправки на столько — письмо уже возвращали. Часы платформы и наши
#: расходятся на секунды, а повтор уходит не раньше `SETTLE` после прошлой попытки.
RETURNED_BEFORE = timedelta(minutes=10)


def _returnable(message: MessageModel) -> bool:
    """Отказ — молчание DNS получателя, и это первая попытка письма."""
    kind = refused_kind(
        message.failure_reason,
        deferred_kind=message.deferred_kind,
        deferred_reason=message.deferred_reason,
    )
    if kind is not Trouble.RECIPIENT_DNS or message.sent_at is None:
        return False
    since = message.deferred_since
    return since is None or message.sent_at - since < RETURNED_BEFORE


async def _return(session: AsyncSession, message: MessageModel, now: datetime) -> bool:
    """Вернуть письмо в работу; `False` — цепочка кончилась или его вывел другой путь."""
    previous = await alive_link(session, message) if is_followup(message) else None
    if is_followup(message) and previous is None:
        return False
    returned = await settle.leave(
        session,
        message,
        was=MessageStatus.BOUNCED,
        status=MessageStatus.QUEUED,
        sent_at=None,
        delivered_at=None,
        failure_reason=None,
        deferred_at=None,
        deferred_attempts=None,
        deferred_reason=None,
        deferred_kind=None,
        # Остаётся: по ней видно, что письмо уже возвращали (шапка модуля).
        deferred_since=message.deferred_since or message.sent_at,
    )
    if not returned:
        return False
    if previous is not None:
        previous.next_action_at = now
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=None,
        target=f"message:{message.id}",
        details={"действие": "письмо вернулось в работу: отказ из-за молчания DNS получателя"},
        stage=await settle.stage_of(session, message),
    )
    return True


async def return_letters(session: AsyncSession, *, now: datetime) -> int:
    """Вернуть в работу письма, отбитые молчащим DNS получателя; сколько вернулось. Без
    фиксации: её делает вызывающий."""
    rows = await session.scalars(
        select(MessageModel)
        .where(
            MessageModel.status == MessageStatus.BOUNCED,
            MessageModel.step < MAX_STEPS,
            MessageModel.sent_at <= now - SETTLE,
            func.coalesce(MessageModel.deferred_since, MessageModel.sent_at) > now - WINDOW,
        )
        .order_by(MessageModel.id)
    )
    returned = 0
    for message in [found for found in rows.all() if _returnable(found)]:
        returned += await _return(session, message, now)
    if returned:
        logger.info("письма: %s вернулись в работу — отказ из-за молчания DNS получателя", returned)
    return returned
