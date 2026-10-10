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

**Перекрытый ответ разбора не ждёт** (`superseded_by`): после него донор
назвал цену, и она принята — цена переписки теперь из того ответа. Правило
одно на числа, карточку переписки и подтверждение: экран не зовёт разбирать
перекрытый ответ, а сервер не даёт его подтвердить.

**Ответ рекламодателя — лид, а не цена.** Его не разбирают
(`replies.outcome.ADVERTISER_LEAD`), и «ждёт разбора» с формой цены
было бы неправдой: подтверждение цены для него — отказ. Диалог Этапа 2
ждёт человека, пока хоть один ответ не взят в работу, — новый ответ
после взятого снова работа.

**Ответ лида продаж — своё состояние.** Ни цены, ни лида рекламодателя
в нём нет: его вид разбирает модуль продаж, а ждёт ли ответ человека и почему,
читается из снимка разбора (`replies.outcome.sales_review`). «Ответил человек»
спрятало бы работу за словом «всё хорошо».
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any, Protocol, assert_never

from backend.features.core.domain import GONE_STATUSES, MessageStatus, ReplyKind, Stage
from backend.features.replies.outcome import (
    AUTO_REPLY_WITH_SUM,
    names_a_sum,
    sales_closed_address,
    sales_review,
    waiting_for_review,
)


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
    SALES_PENDING = "sales_pending"  # ответил лид продаж — ждёт, пока почта их подключит


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


class LetterFacts(Protocol):
    """Что правило читает у письма — и только это.

    Карточке переписки письма приходят целиком (`MessageModel`), списку диалогов —
    без темы и тела (`repository.ListedLetter`, аудит 10.10.2026). Правило у обоих одно.
    """

    @property
    def status(self) -> MessageStatus: ...

    @property
    def sent_at(self) -> datetime | None: ...


class ReplyFacts(Protocol):
    """Что правило читает у ответа — и только это.

    Карточке ответы приходят целиком (`ReplyModel`), списку и числам меню — без
    текста, адресов и разбора цен (`repository.ListedReply`, аудит 10.10.2026).
    Понадобится правилу новое поле — сначала строка здесь, и mypy покажет выборку
    списка, где его нет: иначе список молча разошёлся бы с карточкой.
    """

    #: Пусто у ответа, ещё не записанного в базу, — так его строят проверки правил.
    @property
    def id(self) -> int | None: ...

    @property
    def kind(self) -> ReplyKind: ...

    @property
    def created_at(self) -> datetime: ...

    #: Читается только у автоответа на письмо донору: в нём ищется сумма (`review_of`).
    @property
    def raw_body(self) -> str: ...

    @property
    def confidence(self) -> float | None: ...

    @property
    def reviewed_at(self) -> datetime | None: ...

    @property
    def price_white(self) -> Decimal | None: ...

    @property
    def price_grey(self) -> Decimal | None: ...

    @property
    def currency(self) -> str | None: ...

    @property
    def placement(self) -> str | None: ...

    #: Читается только у ответа продаж: «ждёт ли» и «закрыт ли адрес» (`outcome.sales_review`).
    @property
    def model_parse(self) -> Mapping[str, Any] | None: ...


def _last_at(messages: Sequence[LetterFacts], replies: Sequence[ReplyFacts]) -> datetime | None:
    moments = [m.sent_at for m in messages if m.sent_at is not None]
    moments += [r.created_at for r in replies if r.created_at is not None]
    return max(moments) if moments else None


#: Правило состояния: условие и что оно значит.
_Rules = tuple[tuple[bool, ThreadState], ...]


def state_of(
    statuses: Collection[MessageStatus],
    replies: Sequence[ReplyFacts],
    stage: Stage = Stage.DONORS,
) -> ThreadState:
    """Первое подошедшее правило и есть состояние.

    Порядок здесь — не деталь реализации, а сама договорённость: отписка
    сильнее ответа, ответ сильнее отказа доставки по старому письму,
    а цена сильнее просто ответа, потому что ради неё всё и затевалось.
    Поэтому правила лежат списком, а не цепочкой `if` — список видно
    целиком и переставить в нём строку значит изменить правило осознанно.

    Что считать ответом, решает этап: у донора — цена и её разбор,
    у рекламодателя — лид, у продаж — ответ, который ждёт своего разбора.

    От писем правилу нужны только статусы — их оно и принимает: числа «Обзора»
    и меню берут из базы статусы писем диалога, а не сами письма (аудит 10.10.2026).
    """
    kinds = {r.kind for r in replies}
    answered = _answered(replies, stage)

    rules: _Rules = (
        (ReplyKind.UNSUBSCRIBE in kinds, ThreadState.UNSUBSCRIBED),
        *answered,
        (MessageStatus.BOUNCED in statuses, ThreadState.BOUNCED),
        (MessageStatus.STOPPED in statuses, ThreadState.STOPPED),
        (
            MessageStatus.SENT in statuses or MessageStatus.DELIVERED in statuses,
            ThreadState.WAITING,
        ),
    )
    for matched, state in rules:
        if matched:
            return state
    return ThreadState.QUEUED


def _answered(replies: Sequence[ReplyFacts], stage: Stage) -> _Rules:
    """Правила ответа — по этапу рассылки, целиком: новый этап — ошибка mypy."""
    match stage:
        case Stage.DONORS:
            return _answer_rules(replies)
        case Stage.ADVERTISERS:
            return _lead_rules(replies)
        case Stage.SALES:
            human = [r for r in replies if r.kind is ReplyKind.HUMAN]
            closed = any(sales_closed_address(r.model_parse) for r in human)
            waiting = any(review_of(r, stage).waiting for r in human)
            return (
                (closed, ThreadState.UNSUBSCRIBED),
                (waiting, ThreadState.SALES_PENDING),
                (bool(human), ThreadState.REPLIED),
            )
        case _:
            assert_never(stage)


def _lead_rules(replies: Sequence[ReplyFacts]) -> _Rules:
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
    #: Почему ответ ждёт человека не по уверенности разбора — словами:
    #: автоответ с суммой в валюте, ответ лида продаж. Пусто — обычный ответ.
    reason: str | None = None


def review_of(reply: ReplyFacts, stage: Stage = Stage.DONORS) -> Review:
    """Ждёт ли ответ человека — у донора. У рекламодателя разбора цены нет
    вовсе: его ответ — лид, и сумма в нём — его расход, а не цена. Ответ
    человека в продажах ждёт, пока вид не разобран или путь вида — человек.

    Сумма в автоответе считается по сохранённому тексту и только у
    автоответа: у остальных видов она на ожидание не влияет, а читать
    текст каждого ответа ради списка незачем.
    """
    match stage:
        case Stage.ADVERTISERS:
            return Review(waiting=False)
        case Stage.SALES:
            human = reply.kind is ReplyKind.HUMAN
            waits, why = sales_review(reply.model_parse)
            pending = human and waits and reply.reviewed_at is None
            return Review(waiting=pending, reason=why if human else None)
        case Stage.DONORS:
            pass
        case _:
            assert_never(stage)
    priced = reply.kind is ReplyKind.AUTO_REPLY and names_a_sum(reply.raw_body)
    waiting = waiting_for_review(
        reply.kind, reply.confidence, reviewed=reply.reviewed_at is not None, names_a_sum=priced
    )
    return Review(waiting=waiting, reason=AUTO_REPLY_WITH_SUM if priced else None)


def _answer_rules(replies: Sequence[ReplyFacts]) -> _Rules:
    """Ответ донора: ждёт разбора, цена, «не продаём», «бесплатно», просто ответил."""
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
    priced = settled_price(replies)
    waiting = any(waits and _later_price(priced, r) is None for r, waits in judged)

    return (
        # Первым — пока хоть один ответ ждёт человека и не перекрыт: ровно его
        # карточка переписки и зовёт разбирать. До 10.10.2026 «ждёт разбора» стояло
        # после цены, и ответ, пришедший после принятой цены, ждал человека только
        # на экране — меню, список и «Обзор» его не считали (проверка прода 10.10.2026).
        # Ответ, перекрытый ценой, не ждёт — и цена остаётся ценой.
        (waiting, ThreadState.NEEDS_REVIEW),
        (has_price, ThreadState.PRICED),
        (declined, ThreadState.DECLINED),
        (free, ThreadState.FREE),
        (ReplyKind.HUMAN in kinds, ThreadState.REPLIED),
    )


def _order(reply: ReplyFacts) -> tuple[datetime, int]:
    """Порядок ответов переписки: по времени прихода, при равном — по номеру."""
    return (reply.created_at, reply.id or 0)


def _later_price(priced: ReplyFacts | None, reply: ReplyFacts) -> ReplyFacts | None:
    """Принятая цена переписки (`settled_price`), если она пришла позже ответа."""
    return priced if priced is not None and _order(reply) < _order(priced) else None


def superseded_by(reply: ReplyFacts, replies: Sequence[ReplyFacts]) -> ReplyFacts | None:
    """Ответ, который перекрыл этот: пришёл позже, и цена в нём принята. Пусто — не перекрыт.

    Перекрытый ответ разбора не ждёт: цена переписки и карточки донора — из более
    позднего ответа (`settled_price`), и подтверждение старого записало бы в карточку
    прежнюю цену поверх новой. Проверка прода 10.10.2026: донор написал «250 $»
    (разбор не уверен, 60 %), следом уточнил «150 $» (уверен, 93 %, цена в карточке),
    а карточка переписки звала подтвердить 250. Перекрывает только цена: «спасибо»
    без цены после неуверенного разбора его не снимает — разбор по-прежнему ждёт.
    Цена бывает только у ответа донора: у лида и ответа продаж перекрывать нечем.
    """
    return _later_price(settled_price(replies), reply)


def settled_price(replies: Sequence[ReplyFacts]) -> ReplyFacts | None:
    """Ответ, чья цена — цена переписки: последний, где цена принята.

    Принята — подтверждена человеком или разобрана уверенно: так же цена
    попадает в карточку донора (`replies/repository.store_price`), и колонка
    «Цена» в «Диалогах» с карточкой не расходится. До 06.10.2026 бралась
    первая попавшаяся сумма в любом ответе — даже ждущем человека: в списке
    стояло 250 $ из неподтверждённого ответа, в карточке — 150 $ из следующего.
    """
    settled = [
        reply
        for reply in replies
        if (reply.price_white is not None or reply.price_grey is not None)
        and not waiting_for_review(
            reply.kind, reply.confidence, reviewed=reply.reviewed_at is not None
        )
    ]
    return max(settled, key=_order, default=None)


def summarize(
    messages: Sequence[LetterFacts],
    replies: Sequence[ReplyFacts],
    stage: Stage = Stage.DONORS,
) -> ThreadSummary:
    """Свести письма и входящие в одну строку списка."""
    priced = settled_price(replies)
    human = [r for r in replies if r.kind is ReplyKind.HUMAN]
    return ThreadSummary(
        state=state_of({m.status for m in messages}, replies, stage),
        messages_sent=sum(1 for m in messages if m.status in GONE_STATUSES),
        last_event_at=_last_at(messages, replies),
        last_reply_at=max((r.created_at for r in human), default=None),
        price_white=priced.price_white if priced else None,
        price_grey=priced.price_grey if priced else None,
        currency=priced.currency if priced else None,
    )
