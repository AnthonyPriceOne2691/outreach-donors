"""Сторож почты этапов, чья политика его просит (Ф4, срез 4.5b; у продаж).

Четыре тишины, которых общий сторож (`silence.py`) не видит, — каждая «при X не может
быть Y», как у него:

- **ящик молчит:** письма переписки ждут его дольше `QUIET_MINUTES` (срок уже
  открыл окно получателя), а с ящика за это время не ушло ничего;
- **ящик выбыл:** выключен, на паузе, удалён или его домен на паузе — правило отправки
  (`letters/mailbox.silence_of`), — а письма переписок (добивки по сроку, ответы в очереди)
  ждут именно его: с другого ящика они не уйдут. Тревога по смене состояния — от первого
  прохода после того, как ящик выбыл;
- **отправить некому:** первые письма этапа в очереди, а писать нечем — ни одного
  включённого ящика на домене, открытом для этапа (правило фильтра отправки: без паузы,
  выдержки и строки за другим направлением);
- **все на паузе:** у направления ни одного пишущего ящика.

**Срок — не позже 15 минут от минуты, когда ящик перестал отправлять** (ТЗ): ящик выбыл —
тревога с ближайшим проходом сторожа (раз в `workers/reaper.WATCHDOG_INTERVAL_SEC`, 2 мин);
ящик молчит — порог `QUIET_MINUTES` плюс проход. Своё молчание автоотправка первых писем
сторожит сама.

Модуль продаж не ответил о политике — тоже тревога, и проход идёт дальше (договор моста).
Запрос сторожа упал в базе — тревога «сторож этапа не досчитал»: проверка этапа идёт в своей
точке сохранения, и остальные этапы и правила сторожа тишины проверяются как обычно.
Тревоги уходят в Telegram по смене состояния (`alarm_feed.py`).

**Этапы — те, что видит спрашивающий** (`stages`, решение Anthony 10.10.2026, П2б): тревог
о почте продаж без права «Продажи» на экране нет, и модуль продаж о политике тогда не
спрашивается. Тревога несёт свой этап (`Alarm.stage`). Фоновый проход — все этапы.

**Этап в заголовке — словом** («Очередь продаж есть — отправить некому», `alarms.STAGE_GENITIVE`),
код этапа — только в коде тревоги: заголовок стоит полосой над вкладками «Продаж» и уходит в
Telegram (ревью стыков R7, 7.1).
"""

from __future__ import annotations

import logging
from collections.abc import Collection
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

# Мост к модулю продаж — под своим именем: `stages` здесь — этапы, которые видит спрашивающий.
from backend.features.core import stages as mail_bridge
from backend.features.core.domain import MessageStatus, SenderStatus, Stage
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderModel,
    SendingDomainModel,
)
from backend.features.letters import mailbox
from backend.features.letters.chain import ANSWER_STEP, CHAINABLE, FIRST_STEP, MAX_STEPS
from backend.features.ops.alarms import STAGE_GENITIVE, Alarm, direction
from backend.features.outreach import limits

logger = logging.getLogger(__name__)

#: Сколько письмо переписки может ждать своего ящика, прежде чем ящик «молчит». Проход добивок
#: идёт раз в минуту по 20 писем: утренняя волна в сотню подошедших сходит за 5–6 минут, и
#: десять минут с поломкой её не путают. Срок тревоги по ТЗ — 15 минут: порог, проход сторожа
#: и опрос провайдеров до их таймаутов — меньше 14 минут (`workers/reaper.WATCHDOG_INTERVAL_SEC`).
QUIET_MINUTES = 10


async def alarms(session: AsyncSession, now: datetime, *, stages: Collection[Stage]) -> list[Alarm]:
    """Тревоги почты этапов со сторожем в политике — этапов, которые видит спрашивающий
    (`stages`); умолчания нет (ревью продаж к #304). Несохранённое вызывающего сбрасывается
    до проверок: его сбой всплывает как есть, а не тревогой «сторож этапа не досчитал»."""
    await session.flush()
    found: list[Alarm] = []
    for stage in (one for one in Stage if one in stages):
        whose = STAGE_GENITIVE[stage]
        try:
            policy = await mail_bridge.mail_policy(session, stage, f"Сторож почты {whose}")
        except mail_bridge.SalesNotConnectedError as exc:
            logger.info("сторож почты: %s", exc)
            title = f"Политика почты {whose} не получена"
            code = f"no-policy:{stage.value}"
            found.append(Alarm(code=code, title=title, detail=str(exc), stage=stage))
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
        title=f"Сторож почты {STAGE_GENITIVE[stage]} не досчитал",
        detail=(
            f"Запрос сторожа упал в базе: {reason}. Остальные этапы и правила сторож проверил; "
            "трасса — в журнале"
        ),
        stage=stage,
    )


async def _of_stage(session: AsyncSession, stage: Stage, now: datetime) -> list[Alarm]:
    boxes = (
        await session.scalars(
            select(SenderModel).where(SenderModel.stage == stage).order_by(SenderModel.id)
        )
    ).all()
    writing = [box for box in boxes if box.enabled and box.status is not SenderStatus.PAUSED]
    found = [alarm for box in boxes if (alarm := await _of_box(session, box, now)) is not None]
    found += await _deleted(session, stage, now)
    if boxes and not writing:
        found.append(
            Alarm(
                code=f"all-paused:{stage.value}",
                title=f"Все ящики {STAGE_GENITIVE[stage]} на паузе",
                detail=f"Ящиков {len(boxes)}, пишущих нет: письма направления стоят",
                stage=stage,
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
                title=f"Очередь {STAGE_GENITIVE[stage]} есть — отправить некому",
                detail=(
                    f"В очереди {queued} первых писем, а пишущего ящика на открытом домене нет: "
                    "ящики выключены или домен на паузе, на выдержке, записан за другим "
                    "направлением. Что с доменом — на экране «Домены рассылки»"
                ),
                stage=stage,
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


async def _of_box(session: AsyncSession, box: SenderModel, now: datetime) -> Alarm | None:
    """Ящик выбыл — не ждут ли его письма переписок; пишет — не молчит ли он."""
    silent = await mailbox.silence_of(session, box, gone=box.email)
    if silent is None:
        return await _quiet(session, box, now)
    return await _retired(session, box.stage, box, silent, now)


async def _deleted(session: AsyncSession, stage: Stage, now: datetime) -> list[Alarm]:
    """Ящик переписки удалён: письма этапа ушли с ящика, которого больше нет, и их добивки ждут."""
    silent = await mailbox.silence_of(session, None, gone="первого письма") or ""
    alarm = await _retired(session, stage, None, silent, now)
    return [] if alarm is None else [alarm]


async def _retired(
    session: AsyncSession, stage: Stage, box: SenderModel | None, silent: str, now: datetime
) -> Alarm | None:
    """Ящик выбыл, а письма переписок ждут именно его — тревога со словами отказа отправки
    (`silent`). Код — ящик, а не число писем: число меняется, а тревога — одна, пока ящик
    не вернулся или ждать стало нечего (`alarm_feed.py`). `box` пусто — ящик удалён."""
    scheduled, due = await _followups(session, stage, box, now)
    answers = 0 if box is None else await _answers(session, box)
    if not scheduled + answers:
        return None
    if box is None:
        code = f"retired-box:deleted:{stage.value}"
        title = f"Ящик переписки удалён — {direction(stage)}"
    else:
        code, title = f"retired-box:{box.email}", f"Ящик {box.email} выбыл"
    return Alarm(
        code=code,
        title=title,
        detail=(
            f"Писем переписки ждут его: {scheduled + answers} — добивок {scheduled} (срок уже "
            f"наступил у {due}), ответов {answers}. {silent}"
        ),
        stage=stage,
    )


async def _followups(
    session: AsyncSession, stage: Stage, box: SenderModel | None, now: datetime
) -> tuple[int, int]:
    """Добивки, которые уйдут только с этого ящика: срок назначен у письма, ушедшего с него
    (с ящика предыдущего письма уходит добивка, `followups.Claimed.sender_id`), — все и те, чей
    срок уже наступил. `box` пусто — ящик удалён: письма этапа с пустым ящиком."""
    own = (
        MessageModel.sender_id == box.id
        if box is not None
        else MessageModel.sender_id.is_(None)
        & MessageModel.campaign_id.in_(select(CampaignModel.id).where(CampaignModel.stage == stage))
    )
    total, due = (
        await session.execute(
            select(
                func.count(MessageModel.id),
                func.count(MessageModel.id).filter(MessageModel.next_action_at <= now),
            ).where(
                own,
                MessageModel.status.in_(CHAINABLE),
                MessageModel.step < MAX_STEPS - 1,
                MessageModel.next_action_at.is_not(None),
            )
        )
    ).one()
    return int(total), int(due)


async def _answers(session: AsyncSession, box: SenderModel) -> int:
    """Ответы в очереди, которые уйдут только с этого ящика: ящик переписки — ящик её первого
    письма (`answers._thread_sender`). В переписку удалённого ящика ответ не заводится — отказ
    на кнопке."""
    threads = select(MessageModel.thread_id).where(
        MessageModel.step == FIRST_STEP, MessageModel.sender_id == box.id
    )
    found = await session.scalar(
        select(func.count(MessageModel.id)).where(
            MessageModel.step >= ANSWER_STEP,
            MessageModel.status == MessageStatus.QUEUED,
            MessageModel.thread_id.in_(threads),
        )
    )
    return int(found or 0)


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
        stage=box.stage,
    )
