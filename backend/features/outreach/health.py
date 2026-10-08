"""Мягкие сигналы ящика — у этапа, чья политика их слушает (Ф4, срез 4.5b; у продаж).

`deferred` («приду позже») и мягкий отказ (`blocked`) говорят, что ящику пора сбавить ход,
задолго до отказов. Каждый — строка журнала здоровья; набралось `events` за сутки — лимит
ящика на сутки снижен (`cut`, фильтр `limits.screen`), тоже строкой журнала.

Пауза — по окну последних писем ящика (`window`), а не за всю жизнь: доля отказов или жалоб
не ниже порога от окна целиком — `senders.disable` с причиной словами. У этапа без политики
и у продаж, пока модуль не ответил о ней (договор моста), — прежнее правило парковки
(`letters/events._park_if_burning`).

Повтор события — не второй сигнал: платформа доставляет пачку «хотя бы один раз» и шлёт её
снова на любой не-2xx. Строка самого события несёт его номер у платформы (`event_id`):
событие, уже лежащее в журнале, слышано — ни второй строки, ни второго вердикта. Гонку двух
доставок одной пачки держит уникальный индекс: вставка проигравшей падает в своей точке
сохранения, а не роняет вебхук — иначе платформа повторила бы всю пачку, с событиями доноров.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.exc import IntegrityError
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
#: Уникальный индекс номера события (`SenderHealthModel`): его отказ — «событие уже записано».
EVENT_INDEX = "uq_sender_health_event"


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


def _note(
    session: AsyncSession,
    sender: SenderModel,
    kind: str,
    detail: str,
    at: datetime,
    event_id: str | None = None,
) -> None:
    session.add(
        SenderHealthModel(
            sender_id=sender.id, kind=kind, detail=detail[:256], at=at, event_id=event_id
        )
    )


async def listen(
    session: AsyncSession, box_id: int, event: DeliveryEvent, moment: datetime
) -> Heard:
    """Событие доставки письма с ящика `box_id` — глазами политики его этапа.

    Ящик без политики (доноры) выходит раньше журнала — прежним правилом. Событие, которое
    журнал уже знает по номеру, слышано сразу, до записи: повтор пачки не судит ящик дважды."""
    sender = await session.get(SenderModel, box_id)
    rules = None if sender is None else await _rules(session, sender)
    if sender is None or rules is None:
        return Heard(ruled=False)
    if await _known(session, event.event_id):
        return Heard(ruled=True)
    at = event.at or moment
    if not await _noted(session, sender, rules, event, at):
        return Heard(ruled=True)
    judged = event.kind in JUDGED and sender.enabled
    why = await _verdict(session, sender, rules, at) if judged else None
    if why is None:
        return Heard(ruled=True)
    disable(sender, why, now=at)
    _note(session, sender, "paused", why, at)
    return Heard(ruled=True, paused=sender.email)


async def _known(session: AsyncSession, event_id: str | None) -> bool:
    """Журнал уже знает событие с этим номером: платформа повторила пачку."""
    if event_id is None:
        return False
    found = await session.scalar(
        select(SenderHealthModel.id).where(SenderHealthModel.event_id == event_id)
    )
    return found is not None


async def _noted(
    session: AsyncSession,
    sender: SenderModel,
    rules: SoftSignals,
    event: DeliveryEvent,
    at: datetime,
) -> bool:
    """Строки самого события: мягкий сигнал (и снижение лимита на сутки) или жалоба.
    `False` — строку события только что записала другая доставка той же пачки."""
    row = _row(event)
    if row is None:
        return True
    kind, detail = row
    if not await _written(session, sender, kind, detail, at, event.event_id):
        return False
    if kind in SOFT_KINDS and await _soft_today(session, sender.id, at) == rules.events:
        cut = f"лимит снижен на сутки: {rules.events} мягких отказа"
        _note(session, sender, "limit_cut", cut, at)
    return True


def _row(event: DeliveryEvent) -> tuple[str, str] | None:
    """Строка журнала самого события — вид и подробность: мягкий сигнал или жалоба."""
    signal = _signal(event.kind, event.soft)
    if signal is not None:
        return signal, event.reason or signal
    return ("complaint", "жалоба на спам") if event.kind == "spamreport" else None


async def _written(
    session: AsyncSession,
    sender: SenderModel,
    kind: str,
    detail: str,
    at: datetime,
    event_id: str | None,
) -> bool:
    """Записать строку события. С номером — в своей точке сохранения: вставку, которую
    отверг уникальный индекс (ту же пачку только что записала другая доставка), откатывает
    она одна — транзакция вебхука жива, остальные события пачки идут дальше. Такой отказ —
    `False`; любой другой отказ базы — как был, исключением."""
    if event_id is None:
        _note(session, sender, kind, detail, at)
        return True
    try:
        async with session.begin_nested():
            _note(session, sender, kind, detail, at, event_id)
            await session.flush()
    except IntegrityError as exc:
        if EVENT_INDEX not in str(exc.orig):
            raise
        logger.info("сигналы ящика: событие %s уже записала другая доставка пачки", event_id)
        return False
    return True


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
