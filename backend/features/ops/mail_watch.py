"""Сторож почты этапов, чья политика его просит (Ф4, срез 4.5b; у продаж).

Три тишины, которых общий сторож (`silence.py`) не видит, — каждая «при X не может
быть Y», как у него:

- **ящик молчит:** письма переписки ждут его дольше `QUIET_MINUTES` (срок уже
  открыл окно получателя), а с ящика за это время не ушло ничего;
- **отправить некому:** первые письма этапа в очереди, а писать нечем — ни одного
  включённого ящика на домене, открытом для этапа (правило фильтра отправки: без паузы,
  выдержки и строки за другим направлением);
- **все на паузе:** у направления ни одного пишущего ящика.

Модуль продаж не ответил о политике — тоже тревога, и проход идёт дальше (договор моста).
Запрос сторожа упал в базе — тревога «сторож этапа не досчитал»: проверка этапа идёт в своей
точке сохранения, и остальные этапы и правила сторожа тишины проверяются как обычно.
Тревоги уходят в Telegram по смене состояния (`alarm_feed.py`).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
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
from backend.features.outreach import limits

logger = logging.getLogger(__name__)

#: Сколько письмо переписки может ждать своего ящика: проход добивок идёт раз в минуту.
QUIET_MINUTES = 15


async def alarms(session: AsyncSession, now: datetime) -> list[Alarm]:
    """Тревоги почты этапов со сторожем в политике. Несохранённое вызывающего сбрасывается до
    проверок: его сбой всплывает как есть, а не тревогой «сторож этапа не досчитал»."""
    await session.flush()
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
            found += await _counted(session, stage, now)
    return found


async def _counted(session: AsyncSession, stage: Stage, now: datetime) -> list[Alarm]:
    """Тревоги этапа — или одна, «сторож этапа не досчитал»: запрос упал в базе. Этап считается
    в своей точке сохранения — упавший запрос не прерывает транзакцию сторожа, и остальные
    этапы и правила идут дальше; трасса — в журнале, причина — в тревоге."""
    try:
        async with session.begin_nested():
            return await _of_stage(session, stage, now)
    except SQLAlchemyError as exc:
        logger.warning("сторож почты «%s»: запрос упал в базе", stage.value, exc_info=True)
        return [_not_counted(stage, exc)]


def _not_counted(stage: Stage, exc: SQLAlchemyError) -> Alarm:
    """Тревога «сторож этапа не досчитал» — с первой строкой ошибки базы (SQL — в журнале)."""
    lines = str(exc).strip().splitlines()
    reason = lines[0] if lines else type(exc).__name__
    return Alarm(
        code=f"watch-failed:{stage.value}",
        title=f"Сторож почты «{stage.value}» не досчитал",
        detail=(
            f"Запрос сторожа упал в базе: {reason}. Остальные этапы и правила сторож проверил; "
            "трасса — в журнале"
        ),
    )


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
    if queued and not await _able(session, stage, writing, now):
        found.append(
            Alarm(
                code=f"nobody-to-send:{stage.value}",
                title=f"Очередь «{stage.value}» есть — отправить некому",
                detail=(
                    f"В очереди {queued} первых писем, а пишущего ящика на открытом домене нет: "
                    "ящики выключены или домен на паузе, на выдержке, записан за другим "
                    "направлением. Что с доменом — на экране «Домены рассылки»"
                ),
            )
        )
    return found


async def _able(
    session: AsyncSession, stage: Stage, writing: list[SenderModel], now: datetime
) -> bool:
    """Есть ли пишущий ящик на домене, открытом для этапа: правило фильтра отправки
    (`limits.domain_shut` — чужое направление, пауза, выдержка; лимиты дня — не поломка)."""
    rows = await session.scalars(
        select(SendingDomainModel).where(
            SendingDomainModel.domain.in_([box.domain for box in writing])
        )
    )
    shut = {row.domain for row in rows if limits.domain_shut(row, stage, now) is not None}
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
