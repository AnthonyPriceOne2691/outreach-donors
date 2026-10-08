"""Воронка продаж: от очереди до передачи лида — лидами, по гипотезам и периоду.

Шаги: в очереди → отправлено → доставлено / отказ → ответ → лид передан. Открытия и клики
не считаются вовсе: пиксель вредит доставляемости. MQL и SQL ставит телемаркетолог в Kommo,
обратной синхронизации в MVP нет (решение владельца № 7) — воронка сервиса кончается
передачей.

**Каждое число — функцией** (`RULES`): шаг — условие на строке пути лида (`journey`), и одно
и то же условие считает экран, счётчики и выгрузку (`leads`) — как у сводки доноров
(`ops/overview.py`). Своё правило у экрана разошлось бы со счётом при первой же правке.

**Считаем лидов, а не письма.** Цепочка из трёх писем одному человеку — один отправленный
лид: путь лида — одна строка по его диалогу продаж (`sales_threads`, один диалог на лида).
Письма пути — только письма цепочки, первое и добивки; наши ответы в переписке
(`answers_reply_id`) уходят уже после ответа и шагов воронки не двигают.

**Период — по первому ушедшему письму лида.** Лид входит в воронку периода, если первое
письмо цепочки ушло в период; дальше считается всё, что с ним было потом, и после конца
периода: у шагов одна основа, и доли не выходят за сто процентов. У лида в очереди ничего
ещё не ушло — его период по тому, когда письмо встало в очередь.

**Отказ сильнее доставки.** Письмо с отказом — ушедшее, но не дошедшее (как у сводки
доноров). Лид, у которого вернулось хоть одно письмо цепочки, — в «отказ», а не в
«доставлено», даже если раньше письмо дошло: у письма так же — поздняя доставка отказ не
перебивает (`replies/repository.mark_bounced`). Ни доставки, ни отказа — письмо в пути.

**Ответ — тем же правилом, что у почты** (`letters/attempts.ANSWERS`): ответил человек или
попросил не писать. Автоответ и отказ доставки ответом не считаются.

**Передан — тем же правилом, что останавливает цепочку** (`handoff.handed_off_rule`):
передача заведена. Дошла ли она до Kommo и Telegram — забота самой передачи, у неё свои
повторы и тревоги.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ColumnElement, Subquery, and_, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import GONE_STATUSES, MessageStatus
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.letters.attempts import ANSWERS
from backend.features.sales.handoff import handed_off_rule
from backend.features.sales.models import (
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)

#: Письмо ушло — одно определение почты (`core/domain.GONE_STATUSES`): отказ доставки —
#: тоже ушедшее письмо, «отправляется» — ещё нет.
GONE = GONE_STATUSES


class Step(StrEnum):
    """Шаги воронки — в порядке пути лида."""

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    BOUNCED = "bounced"
    ANSWERED = "answered"
    HANDED_OFF = "handed_off"


@dataclass(frozen=True, slots=True)
class Period:
    """Полуинтервал `[since, until)`: пустая граница — без границы."""

    since: datetime | None = None
    until: datetime | None = None

    def holds(self, moment: ColumnElement[datetime]) -> ColumnElement[bool]:
        """Момент есть и лежит в периоде."""
        conditions: list[ColumnElement[bool]] = [moment.is_not(None)]
        if self.since is not None:
            conditions.append(moment >= self.since)
        if self.until is not None:
            conditions.append(moment < self.until)
        return and_(*conditions)


@dataclass(frozen=True, slots=True)
class Funnel:
    """Числа воронки — лидов на каждом шаге. Поля названы шагами (`Step`)."""

    queued: int = 0
    sent: int = 0
    delivered: int = 0
    bounced: int = 0
    answered: int = 0
    handed_off: int = 0

    def __add__(self, other: Funnel) -> Funnel:
        return Funnel(
            **{
                item.name: getattr(self, item.name) + getattr(other, item.name)
                for item in fields(self)
            }
        )


def journey() -> Subquery:
    """Путь лида одной строкой: диалог, гипотеза лида, когда ушло первое письмо цепочки,
    когда письмо встало в очередь, дошло ли и вернулось ли хоть одно.

    Время ухода — `sent_at`, а без него — время записи: ушедшее письмо без отметки времени
    не должно выпадать из периода молча."""
    letter = and_(
        MessageModel.thread_id == SalesThreadModel.thread_id,
        MessageModel.answers_reply_id.is_(None),
    )
    gone = MessageModel.status.in_(GONE)
    return (
        select(
            SalesThreadModel.lead_id.label("lead_id"),
            SalesThreadModel.thread_id.label("thread_id"),
            SalesLeadModel.hypothesis_id.label("hypothesis_id"),
            func.min(func.coalesce(MessageModel.sent_at, MessageModel.created_at))
            .filter(gone)
            .label("gone_at"),
            func.min(MessageModel.created_at)
            .filter(MessageModel.status == MessageStatus.QUEUED)
            .label("queued_at"),
            func.bool_or(MessageModel.status == MessageStatus.DELIVERED).label("delivered"),
            func.bool_or(MessageModel.status == MessageStatus.BOUNCED).label("bounced"),
        )
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .join(MessageModel, letter)
        .group_by(
            SalesThreadModel.lead_id, SalesThreadModel.thread_id, SalesLeadModel.hypothesis_id
        )
        .subquery("journey")
    )


Rule = Callable[[Subquery, Period], ColumnElement[bool]]


def queued(path: Subquery, period: Period) -> ColumnElement[bool]:
    """В очереди: письмо цепочки ждёт отправки, и ни одно ещё не ушло."""
    return and_(path.c.gone_at.is_(None), period.holds(path.c.queued_at))


def sent(path: Subquery, period: Period) -> ColumnElement[bool]:
    """Отправлено: первое письмо цепочки ушло в период. Лид — один, сколько бы писем ни ушло."""
    return period.holds(path.c.gone_at)


def bounced(path: Subquery, period: Period) -> ColumnElement[bool]:
    """Отказ: вернулось хоть одно письмо цепочки."""
    return and_(sent(path, period), path.c.bounced)


def delivered(path: Subquery, period: Period) -> ColumnElement[bool]:
    """Доставлено: письмо дошло, и ни одно не вернулось — отказ сильнее доставки."""
    return and_(sent(path, period), path.c.delivered, ~path.c.bounced)


def answered(path: Subquery, period: Period) -> ColumnElement[bool]:
    """Ответ: человек ответил сам или попросил не писать; автоответ — не ответ."""
    replied = exists().where(ReplyModel.thread_id == path.c.thread_id, ReplyModel.kind.in_(ANSWERS))
    return and_(sent(path, period), replied)


def handed_off(path: Subquery, period: Period) -> ColumnElement[bool]:
    """Лид передан: передача заведена — тем же правилом, каким переданному не пишут."""
    return and_(sent(path, period), handed_off_rule(path.c.lead_id))


#: Правило каждого шага — в порядке пути лида.
RULES: dict[Step, Rule] = {
    Step.QUEUED: queued,
    Step.SENT: sent,
    Step.DELIVERED: delivered,
    Step.BOUNCED: bounced,
    Step.ANSWERED: answered,
    Step.HANDED_OFF: handed_off,
}


async def counts(
    session: AsyncSession, period: Period, *, hypothesis_id: int | None = None
) -> dict[int, Funnel]:
    """Числа воронки по гипотезам — одним запросом, каждое правилом своего шага.
    Гипотезы без единого письма здесь нет: её строку с нулями даёт `board`."""
    path = journey()
    query = select(
        path.c.hypothesis_id, *(func.count().filter(rule(path, period)) for rule in RULES.values())
    ).group_by(path.c.hypothesis_id)
    if hypothesis_id is not None:
        query = query.where(path.c.hypothesis_id == hypothesis_id)
    found: dict[int, Funnel] = {}
    for hypothesis, *numbers in (await session.execute(query)).tuples():
        found[int(hypothesis)] = Funnel(
            **{step.value: int(n) for step, n in zip(RULES, numbers, strict=True)}
        )
    return found


async def leads(
    session: AsyncSession, step: Step, period: Period, *, hypothesis_id: int | None = None
) -> list[int]:
    """Лиды шага — тем же правилом, что его число: для выгрузки и сверки."""
    path = journey()
    query = select(path.c.lead_id).where(RULES[step](path, period)).order_by(path.c.lead_id)
    if hypothesis_id is not None:
        query = query.where(path.c.hypothesis_id == hypothesis_id)
    return [int(lead) for lead in (await session.scalars(query)).all()]


@dataclass(frozen=True, slots=True)
class Row:
    """Воронка одной гипотезы."""

    hypothesis_id: int
    name: str
    funnel: Funnel


@dataclass(frozen=True, slots=True)
class Board:
    """Экран воронки: строка на гипотезу, старшие первыми, и итог по ним."""

    rows: list[Row]
    total: Funnel


async def board(
    session: AsyncSession, period: Period, *, hypothesis_id: int | None = None
) -> Board:
    """Воронка для экрана: и гипотеза без писем стоит строкой с нулями — пустая гипотеза
    видна, а не пропадает. Итог — сумма строк: лид принадлежит одной гипотезе."""
    found = await counts(session, period, hypothesis_id=hypothesis_id)
    query = select(SalesHypothesisModel).order_by(SalesHypothesisModel.id)
    if hypothesis_id is not None:
        query = query.where(SalesHypothesisModel.id == hypothesis_id)
    rows = [
        Row(hypothesis.id, hypothesis.name, found.get(hypothesis.id, Funnel()))
        for hypothesis in (await session.scalars(query)).all()
    ]
    return Board(rows, sum((row.funnel for row in rows), Funnel()))
