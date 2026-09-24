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

**«Ждёт разбора» — состояние, а не пометка.** Ответ человека, из которого
цена не извлеклась уверенно, требует действия: по нему надо принять
решение руками. Показывать такой диалог как «ответил» значит прятать
очередь работы внутри слова, которое звучит как «всё хорошо».

**Ответ рекламодателя — лид, а не цена.** Его не разбирают
(`replies.outcome.ADVERTISER_LEAD`), и «ждёт разбора» с формой цены
было бы неправдой: подтверждение цены для него — отказ. Диалог Этапа 2
ждёт человека, пока хоть один ответ не взят в работу, — новый ответ
после взятого снова работа.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.replies.outcome import waiting_for_review


class ThreadState(StrEnum):
    """Что видно в списке диалогов. Считается, не хранится."""

    QUEUED = "queued"  # письмо ещё не ушло
    WAITING = "waiting"  # ждём ответа
    REPLIED = "replied"  # ответил человек
    NEEDS_REVIEW = "needs_review"  # ответил, но цену подтверждает человек
    PRICED = "priced"  # из ответа получена цена
    DECLINED = "declined"  # донор ответил: размещений не продаёт
    FREE = "free"  # платных не берёт, гостевой пост — бесплатно
    BOUNCED = "bounced"  # отказ доставки
    UNSUBSCRIBED = "unsubscribed"  # отписался
    STOPPED = "stopped"  # цепочка остановлена руками
    LEAD = "lead"  # ответил рекламодатель — лид ждёт человека
    LEAD_TAKEN = "lead_taken"  # лид взят в работу


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


#: Правило состояния: условие и что оно значит.
_Rules = tuple[tuple[bool, ThreadState], ...]


def _state(
    messages: Sequence[MessageModel],
    replies: Sequence[ReplyModel],
    stage: Stage = Stage.DONORS,
) -> ThreadState:
    """Первое подошедшее правило и есть состояние.

    Порядок здесь — не деталь реализации, а сама договорённость: отписка
    сильнее ответа, ответ сильнее отказа доставки по старому письму,
    а цена сильнее просто ответа, потому что ради неё всё и затевалось.
    Поэтому правила лежат списком, а не цепочкой `if` — список видно
    целиком и переставить в нём строку значит изменить правило осознанно.

    Что считать ответом, решает этап: у донора — цена и её разбор,
    у рекламодателя — лид.
    """
    kinds = {r.kind for r in replies}
    statuses = {m.status for m in messages}
    answered = _lead_rules(replies) if stage is Stage.ADVERTISERS else _answer_rules(replies)

    rules: _Rules = (
        (ReplyKind.UNSUBSCRIBE in kinds, ThreadState.UNSUBSCRIBED),
        *answered,
        (MessageStatus.BOUNCED in statuses, ThreadState.BOUNCED),
        (MessageStatus.STOPPED in statuses, ThreadState.STOPPED),
        (bool({MessageStatus.SENT, MessageStatus.DELIVERED} & statuses), ThreadState.WAITING),
    )
    for matched, state in rules:
        if matched:
            return state
    return ThreadState.QUEUED


def _lead_rules(replies: Sequence[ReplyModel]) -> _Rules:
    """Ответ рекламодателя: лид ждёт человека, пока его не взяли в работу."""
    human = [r for r in replies if r.kind is ReplyKind.HUMAN]
    return (
        (any(r.reviewed_at is None for r in human), ThreadState.LEAD),
        (bool(human), ThreadState.LEAD_TAKEN),
    )


def _answer_rules(replies: Sequence[ReplyModel]) -> _Rules:
    """Ответ донора: цена, «не продаём», «бесплатно», ждёт разбора, просто ответил."""
    kinds = {r.kind for r in replies}
    # Цена считается полученной, только если её не ждёт человек: иначе
    # диалог с неуверенным разбором выглядел бы законченным, а список
    # диалогов врал бы именно там, где по нему принимают решения.
    has_price = any(
        (r.price_white is not None or r.price_grey is not None)
        and not waiting_for_review(r.kind, r.confidence, reviewed=r.reviewed_at is not None)
        for r in replies
    )

    # «Не продаём» — законченный ответ, как и цена: работы по нему нет,
    # а в списке он не должен выглядеть как «ответил, что-то непонятное».
    declined = any(
        r.placement == "declines"
        and not waiting_for_review(r.kind, r.confidence, reviewed=r.reviewed_at is not None)
        for r in replies
    )

    free = any(
        r.placement == "free"
        and not waiting_for_review(r.kind, r.confidence, reviewed=r.reviewed_at is not None)
        for r in replies
    )

    waiting = any(
        waiting_for_review(r.kind, r.confidence, reviewed=r.reviewed_at is not None)
        for r in replies
    )

    return (
        (has_price, ThreadState.PRICED),
        (declined, ThreadState.DECLINED),
        (free, ThreadState.FREE),
        # Раньше «ответил»: у обоих состояний ответ уже есть, но одно
        # требует работы, а другое нет, и по списку принимают решения.
        (waiting, ThreadState.NEEDS_REVIEW),
        (ReplyKind.HUMAN in kinds, ThreadState.REPLIED),
    )


def summarize(
    messages: Sequence[MessageModel],
    replies: Sequence[ReplyModel],
    stage: Stage = Stage.DONORS,
) -> ThreadSummary:
    """Свести письма и входящие в одну строку списка."""
    priced = next(
        (r for r in replies if r.price_white is not None or r.price_grey is not None), None
    )
    human = [r for r in replies if r.kind is ReplyKind.HUMAN]
    return ThreadSummary(
        state=_state(messages, replies, stage),
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
