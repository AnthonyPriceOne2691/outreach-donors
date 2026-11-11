"""Потолки добивок ящика — часовой и суточный: счёт и сколько ждать тому, что сверх суточного.

**Часовой** (`OUTREACH_FOLLOWUP_PER_SENDER_PER_HOUR`, решение 21.09.2026) — чтобы сотня подошедших
добивок не ушла пачкой за минуту: почтовая платформа смотрит на скорость. В нём и перезапрос цены.

**Зачем рядом суточный.** Сроки добивок считаются в календарных днях, а письма уходят только в
окно получателя: добивки, чей срок выпал на выходные, подходят в понедельник все сразу. Часового
потолка на них мало: 09.11.2026 ящик отдал за день втрое больше своего дневного лимита первых
писем, направление — в два с половиной раза больше обычного дня, и назавтра почта получателей
стала отвергать часть писем как спам.

**Суточный** (`OUTREACH_FOLLOWUP_PER_SENDER_PER_DAY`, 11.11.2026). С ящика за сутки уходит не
больше потолка добивок цепочки. Сверх него добивка ждёт следующих суток: срок переносится на их
начало, цепочка жива, и наутро перенесённые стоят раньше подошедших за день. Сутки — по UTC,
письмо «в полёте» в счёте — как у дневного лимита первых писем (`repository.sent_today`): два
счёта одного ящика не расходятся в правилах. Перезапрос цены в суточный потолок не входит: он уже
в дневном лимите первых писем.

Одно правило у прохода добивок (`letters/followups._waits`), у перезапроса цены
(`letters/recheck_facts.py`), у плана рассылки (`letters/forecast_plan.Chains`) и у панели лимитов
(`ops/limit_mail.py`).
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.chain import FIRST_STEP, MAX_STEPS, RECHECK_STEP

HOUR, DAY = timedelta(hours=1), timedelta(days=1)


def chain_followup() -> ColumnElement[bool]:
    """Добивка цепочки: письмо после первого и до конца цепочки."""
    return and_(MessageModel.step > FIRST_STEP, MessageModel.step < MAX_STEPS)


def in_hourly_lane() -> ColumnElement[bool]:
    """Письмо в часовом лейне ящика: добивка цепочки или перезапрос цены. Одно правило у прохода
    добивок (`this_hour_of`) и у перезапроса (`letters/recheck_facts.py`)."""
    return or_(chain_followup(), MessageModel.step == RECHECK_STEP)


async def this_hour_of(session: AsyncSession, sender_id: int, now: datetime) -> int:
    """Сколько писем часового лейна ушло с этого ящика за последний час.

    Только шаги цепочки: ответ в переписке (`chain.ANSWER_STEP`) — тоже письмо с этого ящика, но
    не добивка, и считать его здесь значило бы отнимать часовой запас добивок у ящика, где человек
    ведёт разговор (ревью «Продаж» #162). И перезапрос цены (`chain.RECHECK_STEP`): он уходит
    пачкой по нажатию и без счёта давал бы всплеск с ящика (проверка 12.10.2026).
    """
    total = await session.scalar(
        select(func.count())
        .select_from(MessageModel)
        .where(MessageModel.sender_id == sender_id, in_hourly_lane())
        .where(MessageModel.sent_at >= now - HOUR)
    )
    return int(total or 0)


def day_start(moment: datetime) -> datetime:
    """Начало суток счёта писем: сутки — по UTC, как и всё остальное время в базе."""
    return datetime.combine(moment.date(), time.min, tzinfo=UTC)


def sent_since(since: datetime) -> ColumnElement[bool]:
    """Письмо ушло с `since` — или «в полёте» с этой минуты (захвачено отправкой, исход
    неизвестен): «ушло» пишется задним числом (`letters/unknown_outcome.py`), и без этого суточный
    счёт пускал лишнее письмо — «13 из 12» на стенде 26.10.2026."""
    flying = and_(MessageModel.status == MessageStatus.SENDING, MessageModel.updated_at >= since)
    return or_(MessageModel.sent_at >= since, flying)


async def today_by_box(session: AsyncSession, now: datetime) -> dict[int, int]:
    """Сколько добивок цепочки ушло сегодня с каждого ящика."""
    rows = await session.execute(
        select(MessageModel.sender_id, func.count())
        .where(MessageModel.sender_id.is_not(None), chain_followup())
        .where(sent_since(day_start(now)))
        .group_by(MessageModel.sender_id)
    )
    return {sender_id: count for sender_id, count in rows.all() if sender_id is not None}


async def today_of(session: AsyncSession, sender_id: int, now: datetime) -> int:
    """Сколько добивок цепочки ушло сегодня с этого ящика."""
    total = await session.scalar(
        select(func.count())
        .select_from(MessageModel)
        .where(MessageModel.sender_id == sender_id, chain_followup())
        .where(sent_since(day_start(now)))
    )
    return int(total or 0)


async def wait(session: AsyncSession, sender_id: int, now: datetime) -> timedelta | None:
    """Сколько ждать добивке этого ящика из-за суточного потолка — до начала следующих суток;
    `None` — потолок не выбран. Ноль в настройке держит все добивки: так их останавливают, не
    теряя сроков."""
    if await today_of(session, sender_id, now) < cfg.FOLLOWUP_PER_SENDER_PER_DAY:
        return None
    return day_start(now) + DAY - now
