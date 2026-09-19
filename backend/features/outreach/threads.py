"""Состояние диалога — считается по событиям, а не хранится полем.

Отдельное поле рассинхронизируется с письмами при первом же сбое, и тогда
список врёт именно там, где по нему принимают решения. Поэтому состояние
выводится из того, что произошло: последнего письма и последнего входящего.

Два правила, оплаченные чужим опытом:

**Автоответчик ответом не считается.** «Я в отпуске до понедельника»
не переводит диалог в «ответил» и не останавливает добивки — иначе
половина цепочек оборвётся ни на чём.

**Отказ доставки — тоже не ответ**, но он останавливает цепочку и метит
контакт: продолжать писать на несуществующий адрес значит жечь
репутацию домена отправителя.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from backend.features.core.domain import MessageStatus, ReplyKind
from backend.features.core.models.outreach import MessageModel, ReplyModel


class ThreadState(StrEnum):
    """Что видно в списке диалогов. Считается, не хранится."""

    QUEUED = "queued"  # письмо ещё не ушло
    WAITING = "waiting"  # ждём ответа
    REPLIED = "replied"  # ответил человек
    PRICED = "priced"  # из ответа получена цена
    BOUNCED = "bounced"  # отказ доставки
    UNSUBSCRIBED = "unsubscribed"  # отписался
    STOPPED = "stopped"  # цепочка остановлена руками


@dataclass(frozen=True, slots=True)
class ThreadSummary:
    """Строка списка диалогов."""

    state: ThreadState
    messages_sent: int
    last_event_at: datetime | None
    last_reply_at: datetime | None
    price_white: Decimal | None
    price_grey: Decimal | None
    currency: str | None


def _last_at(messages: Sequence[MessageModel], replies: Sequence[ReplyModel]) -> datetime | None:
    moments = [m.sent_at for m in messages if m.sent_at is not None]
    moments += [r.created_at for r in replies if r.created_at is not None]
    return max(moments) if moments else None


def _state(messages: Sequence[MessageModel], replies: Sequence[ReplyModel]) -> ThreadState:
    """Первое подошедшее правило и есть состояние.

    Порядок здесь — не деталь реализации, а сама договорённость: отписка
    сильнее ответа, ответ сильнее отказа доставки по старому письму,
    а цена сильнее просто ответа, потому что ради неё всё и затевалось.
    Поэтому правила лежат списком, а не цепочкой `if` — список видно
    целиком и переставить в нём строку значит изменить правило осознанно.
    """
    kinds = {r.kind for r in replies}
    statuses = {m.status for m in messages}
    has_price = any(r.price_white is not None or r.price_grey is not None for r in replies)

    rules: tuple[tuple[bool, ThreadState], ...] = (
        (ReplyKind.UNSUBSCRIBE in kinds, ThreadState.UNSUBSCRIBED),
        (has_price, ThreadState.PRICED),
        (ReplyKind.HUMAN in kinds, ThreadState.REPLIED),
        (MessageStatus.BOUNCED in statuses, ThreadState.BOUNCED),
        (MessageStatus.STOPPED in statuses, ThreadState.STOPPED),
        (bool({MessageStatus.SENT, MessageStatus.DELIVERED} & statuses), ThreadState.WAITING),
    )
    for matched, state in rules:
        if matched:
            return state
    return ThreadState.QUEUED


def summarize(messages: Sequence[MessageModel], replies: Sequence[ReplyModel]) -> ThreadSummary:
    """Свести письма и входящие в одну строку списка."""
    priced = next(
        (r for r in replies if r.price_white is not None or r.price_grey is not None), None
    )
    human = [r for r in replies if r.kind is ReplyKind.HUMAN]
    return ThreadSummary(
        state=_state(messages, replies),
        messages_sent=sum(
            1
            for m in messages
            if m.status in (MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.BOUNCED)
        ),
        last_event_at=_last_at(messages, replies),
        last_reply_at=max((r.created_at for r in human), default=None),
        price_white=priced.price_white if priced else None,
        price_grey=priced.price_grey if priced else None,
        currency=priced.currency if priced else None,
    )
