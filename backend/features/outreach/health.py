"""Мягкие сигналы ящика — у этапа, чья политика их слушает (Ф4, срез 4.5b; у продаж).

`deferred` («приду позже») и мягкий отказ (`blocked`) говорят, что ящику пора сбавить ход,
задолго до отказов. Каждый — строка журнала здоровья; набралось `events` за сутки — лимит
ящика на сутки снижен (`cut`, фильтр `limits.screen`), тоже строкой журнала.

Пауза — по окну последних писем ящика (`window`), а не за всю жизнь: доля отказов или жалоб
не ниже порога от окна целиком — `senders.disable` с причиной словами. У этапа без политики
и у продаж, пока модуль не ответил о ней (договор моста), — прежнее правило парковки
(`letters/events._park_if_burning`).
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel, SenderHealthModel, SenderModel
from backend.features.outreach.senders import disable, warmup_state

if TYPE_CHECKING:
    from backend.features.letters.events import DeliveryEvent

logger = logging.getLogger(__name__)

#: Мягкие сигналы: «приду позже» и мягкий отказ принимающего сервера.
SOFT_KINDS = ("deferred", "blocked")
#: События, после которых ящик судится по окну последних писем.
JUDGED = frozenset({"bounce", "dropped", "blocked", "spamreport"})
DAY = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class SoftSignals:
    """Пороги мягких сигналов этапа: их задаёт политика (у продаж — модуль продаж)."""

    events: int = 3  # мягких отказов за сутки — и лимит ящика снижен
    cut: float = 0.5  # во сколько раз
    window: int = 50  # последних писем ящика в окне
    bounces: float = 0.05
    complaints: float = 0.001  # `SALES_COMPLAINT_PAUSE`


@dataclass(frozen=True, slots=True)
class Heard:
    """Что событие сделало с ящиком: `ruled` — судит политика этапа, а не прежнее правило;
    `paused` — адрес ящика, вставшего на паузу."""

    ruled: bool
    paused: str | None = None


def _note(session: AsyncSession, sender: SenderModel, kind: str, detail: str, at: datetime) -> None:
    session.add(SenderHealthModel(sender_id=sender.id, kind=kind, detail=detail[:256], at=at))


async def listen(
    session: AsyncSession, box_id: int, event: DeliveryEvent, moment: datetime
) -> Heard:
    """Событие доставки письма с ящика `box_id` — глазами политики его этапа."""
    sender = await session.get(SenderModel, box_id)
    rules = None if sender is None else await _rules(session, sender)
    if sender is None or rules is None:
        return Heard(ruled=False)
    kind, at = event.kind, event.at or moment
    signal = _signal(kind, event.soft)
    if signal is not None:
        _note(session, sender, signal, event.reason or signal, at)
        if await _soft_today(session, sender.id, at) == rules.events:
            cut = f"лимит снижен на сутки: {rules.events} мягких отказа"
            _note(session, sender, "limit_cut", cut, at)
    if kind == "spamreport":
        _note(session, sender, "complaint", "жалоба на спам", at)
    why = await _verdict(session, sender, rules, at) if kind in JUDGED and sender.enabled else None
    if why is None:
        return Heard(ruled=True)
    disable(sender, why, now=at)
    _note(session, sender, "paused", why, at)
    return Heard(ruled=True, paused=sender.email)


async def _rules(session: AsyncSession, sender: SenderModel) -> SoftSignals | None:
    """Пороги этапа ящика; модуль продаж не ответил — прежнее правило (мост сказал почему)."""
    try:
        return (await stages.mail_policy(session, sender.stage, f"Ящик {sender.email}")).soft
    except stages.SalesNotConnectedError as exc:
        logger.info("сигналы ящика: прежнее правило парковки — %s", exc)
        return None


def _signal(kind: str, soft: bool) -> str | None:
    """Мягкий ли это сигнал: `deferred` или мягкий отказ (`blocked`, отказ с `type: blocked`)."""
    if kind == "deferred":
        return "deferred"
    return "blocked" if kind == "blocked" or (kind == "bounce" and soft) else None


def _soft_day(at: datetime) -> tuple[ColumnElement[bool], ...]:
    """Мягкий сигнал за сутки до `at` — одно условие на снижение и на счёт."""
    return SenderHealthModel.kind.in_(SOFT_KINDS), SenderHealthModel.at > at - DAY


async def _soft_today(session: AsyncSession, sender_id: int, at: datetime) -> int:
    found = await session.scalar(
        select(func.count()).where(SenderHealthModel.sender_id == sender_id, *_soft_day(at))
    )
    return int(found or 0)


async def _verdict(
    session: AsyncSession, sender: SenderModel, rules: SoftSignals, at: datetime
) -> str | None:
    """Пауза по окну последних писем: причина словами или `None`."""
    last = (
        select(MessageModel.status, MessageModel.sent_at)
        .where(MessageModel.sender_id == sender.id, MessageModel.sent_at.is_not(None))
        .order_by(MessageModel.sent_at.desc())
        .limit(rules.window)
        .subquery()
    )
    tally = func.count().filter(last.c.status == MessageStatus.BOUNCED)
    bounced, since = (await session.execute(select(tally, func.min(last.c.sent_at)))).one()
    complaints = await session.scalar(
        select(func.count()).where(
            SenderHealthModel.sender_id == sender.id,
            SenderHealthModel.kind == "complaint",
            SenderHealthModel.at >= (since or at),
        )
    )
    for count, rate, what in (
        (bounced, rules.bounces, "отказов"),
        (complaints, rules.complaints, "жалоб"),
    ):
        if count and count >= math.ceil(round(rate * rules.window, 9)):
            return f"{what} {count} в окне {rules.window} писем — порог {rate:.1%}: пауза"
    return None


async def cuts(session: AsyncSession, stage: Stage, now: datetime) -> dict[int, int]:
    """Сниженные на сегодня лимиты ящиков этапа: `events` мягких отказов за сутки."""
    rules = (await stages.mail_policy(session, stage, f"Выбор ящика «{stage.value}»")).soft
    if rules is None:
        return {}
    rows = await session.execute(
        select(SenderModel, func.count(SenderHealthModel.id))
        .join(SenderHealthModel, SenderHealthModel.sender_id == SenderModel.id)
        .where(SenderModel.stage == stage, *_soft_day(now))
        .group_by(SenderModel.id)
    )
    return {
        sender.id: int(warmup_state(sender, now=now).allowance * rules.cut)
        for sender, count in rows.all()
        if count >= rules.events
    }
