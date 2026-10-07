"""Сторож почты этапов, чья политика его просит (Ф4, срез 4.5b; у продаж).

Три тишины, которых общий сторож (`silence.py`) не видит, — каждая «при X не может
быть Y», как у него:

- **ящик молчит:** письма переписки ждут его дольше `QUIET_MINUTES` (срок уже
  открыл окно получателя), а с ящика за это время не ушло ничего;
- **отправить некому:** первые письма этапа в очереди, а писать нечем — ни одного
  включённого ящика на домене без паузы и выдержки;
- **все на паузе:** у направления ни одного пишущего ящика.

Модуль продаж не ответил о политике — тоже тревога, и проход идёт дальше (договор моста).
Тревоги уходят в Telegram по смене состояния (`alarm_feed.py`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import stages
from backend.features.core.domain import MessageStatus, SenderStatus, Stage
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderModel,
    SendingDomainModel,
)
from backend.features.letters.chain import CHAINABLE, FIRST_STEP, MAX_STEPS
from backend.features.ops.alarms import Alarm

logger = logging.getLogger(__name__)

#: Сколько письмо переписки может ждать своего ящика: проход добивок идёт раз в минуту.
QUIET_MINUTES = 15


async def alarms(session: AsyncSession, now: datetime) -> list[Alarm]:
    """Тревоги почты этапов со сторожем в политике."""
    found: list[Alarm] = []
    for stage in Stage:
        try:
            policy = await stages.mail_policy(session, stage, f"Сторож почты «{stage.value}»")
        except stages.SalesNotConnectedError as exc:
            logger.info("сторож почты: %s", exc)
            title = f"Политика почты «{stage.value}» не получена"
            found.append(Alarm(code=f"no-policy:{stage.value}", title=title, detail=str(exc)))
            continue
        if policy.watch:
            found += await _of_stage(session, stage, now)
    return found


async def _of_stage(session: AsyncSession, stage: Stage, now: datetime) -> list[Alarm]:
    boxes = (await session.scalars(select(SenderModel).where(SenderModel.stage == stage))).all()
    writing = [box for box in boxes if box.enabled and box.status is not SenderStatus.PAUSED]
    found = [alarm for box in writing if (alarm := await _quiet(session, box, now)) is not None]
    if boxes and not writing:
        found.append(
            Alarm(
                code=f"all-paused:{stage.value}",
                title=f"Все ящики направления «{stage.value}» на паузе",
                detail=f"Ящиков {len(boxes)}, пишущих нет: письма направления стоят",
            )
        )
    queued = await session.scalar(
        select(func.count(MessageModel.id))
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(
            CampaignModel.stage == stage,
            MessageModel.status == MessageStatus.QUEUED,
            MessageModel.step == FIRST_STEP,
        )
    )
    if queued and not await _able(session, writing, now):
        found.append(
            Alarm(
                code=f"nobody-to-send:{stage.value}",
                title=f"Очередь «{stage.value}» есть — отправить некому",
                detail=(
                    f"В очереди {queued} первых писем, а пишущего ящика на домене без паузы "
                    "и выдержки нет. Включить ящик или домен на экране «Домены рассылки»"
                ),
            )
        )
    return found


async def _able(session: AsyncSession, writing: list[SenderModel], now: datetime) -> bool:
    """Есть ли пишущий ящик на домене без паузы и выдержки (лимиты дня — не поломка)."""
    rows = await session.scalars(
        select(SendingDomainModel).where(
            SendingDomainModel.domain.in_([box.domain for box in writing])
        )
    )
    shut = {
        row.domain
        for row in rows
        if row.paused_at is not None or (row.young_until is not None and now < row.young_until)
    }
    return any(box.domain not in shut for box in writing)


async def _quiet(session: AsyncSession, box: SenderModel, now: datetime) -> Alarm | None:
    """Письма переписки ждут ящик дольше `QUIET_MINUTES`, а с него не ушло ничего."""
    edge = now - timedelta(minutes=QUIET_MINUTES)
    waiting = await session.scalar(
        select(func.count(MessageModel.id)).where(
            MessageModel.sender_id == box.id,
            MessageModel.status.in_(CHAINABLE),
            MessageModel.step < MAX_STEPS - 1,
            MessageModel.next_action_at <= edge,
        )
    )
    if not waiting:
        return None
    if await session.scalar(
        select(MessageModel.id).where(MessageModel.sender_id == box.id, MessageModel.sent_at > edge)
    ):
        return None
    return Alarm(
        code=f"quiet-box:{box.email}",
        title=f"Ящик {box.email} молчит",
        detail=(
            f"{waiting} писем переписки ждут его дольше {QUIET_MINUTES} минут, а с него "
            "не ушло ничего: проход добивок не идёт или ящик упёрся в отказы почты"
        ),
    )
