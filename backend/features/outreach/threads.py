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
очередь работы внутри слова, которое звучит как «всё хорошо». Туда же —
автоответ с суммой в валюте (`replies.outcome.AUTO_REPLY_WITH_SUM`):
модель его не разбирала, и без человека цена в нём пропала бы.

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
from backend.features.replies.outcome import AUTO_REPLY_WITH_SUM, names_a_sum, waiting_for_review


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


@dataclass(frozen=True, slots=True)
class Review:
    """Ждёт ли ответ человека и почему — одно правило для списка и карточки."""

    waiting: bool
    #: Почему цену в ответе не человека смотрит человек, — словами.
    #: Пусто — обычный ответ, его ждут по уверенности разбора.
    reason: str | None = None


def review_of(reply: ReplyModel, stage: Stage = Stage.DONORS) -> Review:
    """Ждёт ли ответ человека — у донора. У рекламодателя разбора цены нет
    вовсе: его ответ — лид, и сумма в нём — его расход, а не цена.

    Сумма в автоответе считается по сохранённому тексту и только у
    автоответа: у остальных видов она на ожидание не влияет, а читать
    текст каждого ответа ради списка незачем.
    """
    if stage is Stage.ADVERTISERS:
        return Review(waiting=False)
    priced = reply.kind is ReplyKind.AUTO_REPLY and names_a_sum(reply.raw_body)
    waiting = waiting_for_review(
        reply.kind, reply.confidence, reviewed=reply.reviewed_at is not None, names_a_sum=priced
    )
    return Review(waiting=waiting, reason=AUTO_REPLY_WITH_SUM if priced else None)


def _answer_rules(replies: Sequence[ReplyModel]) -> _Rules:
    """Ответ донора: цена, «не продаём», «бесплатно», ждёт разбора, просто ответил."""
    kinds = {r.kind for r in replies}
    judged = [(r, review_of(r).waiting) for r in replies]
    # Цена считается полученной, только если её не ждёт человек: иначе
    # диалог с неуверенным разбором выглядел бы законченным, а список
    # диалогов врал бы именно там, где по нему принимают решения.
    # «Не продаём» — законченный ответ, как и цена: работы по нему нет,
    # а в списке он не должен выглядеть как «ответил, что-то непонятное».
    settled = [r for r, waits in judged if not waits]
    has_price = any(r.price_white is not None or r.price_grey is not None for r in settled)
    declined = any(r.placement == "declines" for r in settled)
    free = any(r.placement == "free" for r in settled)
    waiting = len(settled) < len(judged)

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
