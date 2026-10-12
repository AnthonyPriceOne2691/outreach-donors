"""Оффер рекламодателю одним словом: собрано, ушло, ответил — по его диалогам Этапа 2.

Нужен спискам Этапа 2 — списку рекламодателей с выгрузкой и приёмке по донору: им
важен исход оффера домену, а не каждое письмо цепочки. Считается выражением базы,
а не питоном по строкам: по слову фильтруют и считают страницы, и правило в питоне
разошлось бы с фильтром на первой же странице.

**Слово — одно на домен, по старшинству.** Отписка сильнее ответа, ответ — ушедшего
письма, ушедшее — собранного (добивка в очереди после ушедшего первого — «ушло»),
собранное — не дошедшего (новая попытка после отказа доставки — снова «собрано»), не
дошедшее — снятого человеком. Отписка первой и ответ раньше отказа доставки — как
у состояния диалога (`outreach/threads.state_of`).

**Только письма Этапа 2** — рассылок рекламодателям, по их диалогам. Письмо донору на
том же домене — другой разговор: что второго первого письма не будет, держит сборка
(`letters/attempts.py`), а здесь вопрос — что с оффером.

**«Ушло» — платформа приняла письмо** (`GONE_STATUSES`): отказ доставки — тоже ушедшее
письмо, просто не дошедшее, так ушедшие считают главная и диалоги. Слово строки их
различает («ушло» и «не дошло»), а приёмка считает оба (`gone`).

**Один проход, а не подзапрос на строку** (проверка ветки 12.10.2026). Признаки
оффера считаются группировкой диалогов Этапа 2 по домену (`OfferFlags`) и
присоединяются к строкам: счётчики списка и приёмка проходят таблицу писем один раз,
а не по восемь подзапросов на каждого рекламодателя.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import ColumnElement, Select, Subquery, case, false, func, or_, select
from sqlalchemy.orm import aliased

from backend.features.core.domain import (
    GONE_STATUSES,
    MessageStatus,
    ReplyKind,
    Stage,
    ThreadStatus,
)
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)


class OfferState(StrEnum):
    """Что с оффером домену — словом экрана и выгрузки (`OFFER_TITLES`)."""

    UNSUBSCRIBED = "unsubscribed"
    REPLIED = "replied"
    SENT = "sent"
    QUEUED = "queued"
    BOUNCED = "bounced"
    STOPPED = "stopped"
    NONE = "none"


#: Слова экрана и выгрузки — одним местом: файл рядом с экраном не читается выгрузкой
#: из другой системы (`donors/export.py`).
OFFER_TITLES: dict[OfferState, str] = {
    OfferState.UNSUBSCRIBED: "отписался",
    OfferState.REPLIED: "ответил",
    OfferState.SENT: "ушло",
    OfferState.QUEUED: "собрано",
    OfferState.BOUNCED: "не дошло",
    OfferState.STOPPED: "снято",
    OfferState.NONE: "не собрано",
}

#: Письмо в очереди или в пути: исход ещё не известен.
_PENDING = (MessageStatus.QUEUED, MessageStatus.SENDING)
#: Письмо приняла платформа и не вернула отказом.
_DELIVERED = (MessageStatus.SENT, MessageStatus.DELIVERED)


def _any(condition: ColumnElement[bool]) -> ColumnElement[bool]:
    """Хоть одна строка группы подходит."""
    return func.coalesce(func.bool_or(condition), false())


def _status_in(statuses: Collection[MessageStatus]) -> ColumnElement[bool]:
    return _any(MessageModel.status.in_(statuses))


def _by_domain(thread: Any, *columns: Any) -> Select[Any]:
    """Диалоги рассылок Этапа 2, сгруппированные по домену, и `columns` над ними."""
    mailing = aliased(CampaignModel)
    return (
        select(thread.domain_id.label("domain_id"), *columns)
        .select_from(thread)
        .join(mailing, mailing.id == thread.campaign_id)
        .where(mailing.stage == Stage.ADVERTISERS)
        .group_by(thread.domain_id)
    )


def _letters() -> Subquery:
    """Письма Этапа 2 по домену: какие исходы у них есть."""
    thread = aliased(ThreadModel)
    return (
        _by_domain(
            thread,
            _status_in(_DELIVERED).label("delivered"),
            _status_in(_PENDING).label("pending"),
            _status_in((MessageStatus.BOUNCED,)).label("bounced"),
            _status_in((MessageStatus.STOPPED,)).label("stopped"),
            _status_in(GONE_STATUSES).label("gone"),
        )
        .join(MessageModel, MessageModel.thread_id == thread.id)
        .subquery("offer_letters")
    )


def _answers() -> Subquery:
    """Ответы в диалогах Этапа 2 по домену. Отписка кнопкой ставит отметку диалогу
    без ответа (`letters/optout.py`) — она считается наравне с ответом-отпиской."""
    thread = aliased(ThreadModel)
    unsubscribed = or_(
        ReplyModel.kind == ReplyKind.UNSUBSCRIBE, thread.status == ThreadStatus.UNSUBSCRIBED
    )
    replied = or_(ReplyModel.kind == ReplyKind.HUMAN, thread.status == ThreadStatus.REPLIED)
    return (
        _by_domain(
            thread,
            _any(unsubscribed).label("unsubscribed"),
            _any(replied).label("replied"),
        )
        .outerjoin(ReplyModel, ReplyModel.thread_id == thread.id)
        .subquery("offer_answers")
    )


@dataclass(frozen=True, slots=True)
class OfferFlags:
    """Признаки оффера по домену — две группировки, присоединяемые к строкам.

    Новый экземпляр — свои подзапросы: два присоединения в одном запросе не делят
    имён.
    """

    letters: Subquery
    answers: Subquery

    @classmethod
    def build(cls) -> OfferFlags:
        return cls(letters=_letters(), answers=_answers())

    def joined(self, statement: Select[Any], domain_id: Any) -> Select[Any]:
        """Присоединить признаки к выборке по номеру домена строки."""
        return statement.outerjoin(self.letters, self.letters.c.domain_id == domain_id).outerjoin(
            self.answers, self.answers.c.domain_id == domain_id
        )

    def _flag(self, column: Any) -> ColumnElement[bool]:
        return func.coalesce(column, false())

    def state(self) -> ColumnElement[str]:
        """Слово оффера — первое подошедшее правило по старшинству (см. модуль)."""
        letters, answers = self.letters.c, self.answers.c
        return case(
            (self._flag(answers.unsubscribed), OfferState.UNSUBSCRIBED.value),
            (self._flag(answers.replied), OfferState.REPLIED.value),
            (self._flag(letters.delivered), OfferState.SENT.value),
            (self._flag(letters.pending), OfferState.QUEUED.value),
            (self._flag(letters.bounced), OfferState.BOUNCED.value),
            (self._flag(letters.stopped), OfferState.STOPPED.value),
            else_=OfferState.NONE.value,
        )

    def gone(self) -> ColumnElement[bool]:
        """Домену ушёл оффер: платформа приняла хоть одно письмо Этапа 2 (`GONE_STATUSES`
        — принято или не дошло)."""
        return self._flag(self.letters.c.gone)
