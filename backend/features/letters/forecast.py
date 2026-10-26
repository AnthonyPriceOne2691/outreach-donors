"""Прогноз рассылки этапа — экран «Рассылка»: очередь первых писем, мощность ящиков по дням с
разгоном, план на дни вперёд, факт за дни назад, ящики и срок очереди.

Только чтение: несколько сгруппированных запросов, дальше — чистый счёт (`forecast_plan.py`).
Правила — не свои: мощность — правило экрана «Домены рассылки», а сегодня — фильтр выбора ящика с
мягкими отказами (`outreach/capacity.py`); очередь — счёт пачки (`LetterRepository.queued_count`);
добивки — условие прохода добивок (`outreach.repository.followup_pending`); окно получателя и темп
автоотправки — политика этапа (`stages.mail_policy`); запас сборки и пояса получателей очереди
продаж — мост (`core/stage_forecast.py`): почта модуль продаж не импортирует.

**Первые письма — счётом дневного лимита** (`DAILY_STEPS`: первое письмо и перезапрос цены): так
считает кап ящика, и «ушло сегодня + остаток дня» сходится с мощностью дня. Добивки — шаги цепочки:
в кап они не входят (решение 21.09.2026) и показаны отдельно; их срок — по UTC-дню
`next_action_at`, просроченные — сегодня.

**Новые ящики и домены — сами**: прогноз читает ящики и строки доменов на каждый вопрос, своего
списка у него нет.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import assert_never
from zoneinfo import ZoneInfo

from sqlalchemy import ColumnElement, Date, and_, cast, func, literal_column, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core import stage_forecast, stages, window
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    SenderModel,
    SendingDomainModel,
)
from backend.features.letters.chain import FIRST_STEP, MAX_STEPS
from backend.features.letters.forecast_plan import (
    Clock,
    Eta,
    Mailboxes,
    PlanDay,
    Supply,
    first_open,
    plan,
)
from backend.features.letters.mailbox import SILENT
from backend.features.letters.repository import LetterRepository
from backend.features.outreach import capacity, health, limits
from backend.features.outreach.capacity import BoxToday, Rooms, Today
from backend.features.outreach.repository import DAILY_STEPS, OutreachRepository, followup_pending
from backend.features.outreach.senders import Warmup, warmup_state

logger = logging.getLogger(__name__)

#: Начало слов моста, когда письма этапа не уходят вовсе: «…: продажи к почте ещё не подключены».
WHAT = "Первые письма этапа не уходят"
#: Почему у включённых ящиков нет мощности, когда лимит направления прочитан.
SHUT = (
    "ящики этапа не пишут: их домены закрыты (пауза, выдержка, чужое направление) "
    "или дневной лимит направления — ноль"
)
#: Сутки по UTC: день в базе — `timezone('UTC', …)`, без подставляемого значения — так одно и то же
#: выражение стоит и в выборке, и в группировке.
_UTC: ColumnElement[str] = literal_column("'UTC'")


@dataclass(frozen=True, slots=True)
class Sent:
    """Ушло за день: первых писем (счётом капа) и добивок цепочки."""

    first: int = 0
    followups: int = 0


@dataclass(frozen=True, slots=True)
class Mailbox:
    """Ящик этапа в прогнозе: сегодня глазами выбора ящика, разгон и следующая ступень."""

    today: BoxToday
    warmup: Warmup
    #: Когда ящик поднимется на следующую ступень разгона и до скольких писем; пусто — не поднимется.
    next_step: tuple[date, int] | None = None


@dataclass(frozen=True, slots=True)
class Forecast:
    """Прогноз рассылки этапа целиком."""

    stage: Stage
    now: datetime
    queued: int
    #: Сколько ещё напишет сборка (у продаж); пусто — не узнать или у этапа такого счёта нет.
    extra: int | None
    #: Письма очереди, которые не уйдут, пока их не поправят: причина → писем (у продаж); пусто —
    #: не узнать или у этапа такого счёта нет.
    stuck: dict[str, int] | None
    clock: Clock
    #: Почему первые письма этапа не уходят вовсе — словами моста; пусто — уходят.
    blocked: str | None
    #: Лимит направления не прочитан из настроек — что поправить; пусто — прочитан.
    unread: str | None
    today: Today
    sent_today: Sent
    days: list[PlanDay]
    past: list[tuple[date, Sent]]
    eta: Eta
    mailboxes: list[Mailbox]


async def forecast(
    session: AsyncSession, stage: Stage, *, days: int, back: int, now: datetime | None = None
) -> Forecast:
    """Прогноз этапа на `days` дней от сегодня и факт за `back` дней назад — без записи."""
    moment = now or datetime.now(UTC)
    first_day = moment.date()
    policy = await _policy(session, stage)
    every = list((await session.scalars(select(SenderModel).order_by(SenderModel.id))).all())
    domains = await limits.sending_domains(session)
    found = capacity.today(
        every,
        stage=stage,
        sent=await OutreachRepository(session).sent_today(now=moment, first_only=True),
        domains=domains,
        cuts=await _cuts(session, stage, moment),
        now=moment,
    )
    queued = await LetterRepository(session).queued_count(stage=stage)
    answers = await _answers(session, stage, policy)
    clock = Clock(policy.window, answers.zone, policy.pace)
    first = first_open(clock, moment)
    unread = capacity.unread(stage)
    boxes = Mailboxes(
        left=found.left,
        capacity=found.capacity,
        later=_later(every, domains, stage, first),
        sent=found.sent,
        idle=_idle(found, unread),
        refused="; ".join(dict.fromkeys(b.refused for b in found.boxes if b.refused)) or None,
        blocked=await _blocked(session, stage),
    )
    due = await _due(session, stage, first_day, _midnight(first_day + timedelta(days=days)))
    supply = Supply(queued, answers.extra, sum((answers.stuck or {}).values()))
    planned, eta = plan(moment, days, clock, boxes, supply, due)
    by_day = await _sent_by_day(session, stage, _midnight(first_day - timedelta(days=back)))
    past = [first_day - timedelta(days=ago) for ago in range(back, 0, -1)]
    return Forecast(
        stage=stage,
        now=moment,
        queued=queued,
        extra=answers.extra,
        stuck=answers.stuck,
        clock=clock,
        blocked=boxes.blocked,
        unread=unread,
        today=found,
        sent_today=by_day.get(first_day, Sent()),
        days=planned,
        past=[(day, by_day.get(day, Sent())) for day in past],
        eta=eta,
        mailboxes=[_mailbox(box, moment, first) for box in found.boxes],
    )


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _later(
    every: Sequence[SenderModel],
    domains: Mapping[str, SendingDomainModel],
    stage: Stage,
    first: date,
) -> Callable[[date], Rooms]:
    """Мощность будущего дня — правилом экрана «Домены рассылки» (`capacity.rooms_on`); ящик, ещё не
    писавший, напишет первое письмо в первый день, когда письма ещё уходят (`first`)."""
    limit = cfg.direction_limit(stage.value)
    return lambda day: capacity.rooms_on(every, domains, stage, limit, day, first=first)


def _idle(found: Today, unread: str | None) -> str:
    """Почему у ящиков этапа нет мощности вовсе — словами."""
    if unread is not None:
        return unread
    if not any(box.sender.enabled for box in found.boxes):
        return SILENT
    return SHUT


def _mailbox(box: BoxToday, now: datetime, first: date) -> Mailbox:
    step = capacity.next_step(box.sender, now.date(), first=first)
    return Mailbox(box, warmup_state(box.sender, now=now), step)


async def _policy(session: AsyncSession, stage: Stage) -> stages.MailPolicy:
    """Политика этапа — окно получателя и темп автоотправки. Модуль продаж не ответил — пустая:
    письма продаж тогда не уходят вовсе, и это скажет `_blocked`."""
    try:
        return await stages.mail_policy(session, stage, WHAT)
    except stages.SalesNotConnectedError as exc:
        logger.info("прогноз рассылки: политики этапа нет — %s", exc, extra={"stage": stage.value})
        return stages.CURRENT


async def _blocked(session: AsyncSession, stage: Stage) -> str | None:
    """Почему первые письма этапа сейчас не уходят вовсе — словами моста (продажи не подключены и
    чего не хватает); `None` — уходят. Отказ — ответ экрану, а не ошибка: строкой журнала, без
    исключения."""
    try:
        await stages.check_connected(session, stage, WHAT)
    except stages.SalesNotConnectedError as exc:
        logger.info("прогноз рассылки: %s", exc, extra={"stage": stage.value})
        return str(exc)
    return None


async def _cuts(session: AsyncSession, stage: Stage, now: datetime) -> dict[int, int]:
    """Сниженные на сутки лимиты ящиков (`health.cuts`); модуль продаж не ответил о политике —
    снижений не узнать, и сегодняшняя мощность их не считает (строка журнала)."""
    try:
        return await health.cuts(session, stage, now)
    except stages.SalesNotConnectedError as exc:
        logger.info(
            "прогноз рассылки: снижения лимитов не узнать — %s", exc, extra={"stage": stage.value}
        )
        return {}


@dataclass(frozen=True, slots=True)
class _Answers:
    """Что о письмах этапа знает его модуль: запас сборки, пояс очереди и застрявшие письма."""

    extra: int | None = None
    zone: ZoneInfo | None = None
    stuck: dict[str, int] | None = None


async def _answers(session: AsyncSession, stage: Stage, policy: stages.MailPolicy) -> _Answers:
    """Ответы модуля этапа прогнозу. У продаж — мостом (`core/stage_forecast.py`): сколько первых
    писем ещё напишет сборка, пояса получателей очереди (только при окне) и письма, которые не
    уйдут, пока их не поправят. У доноров и рекламодателей очередь собирает человек, окна нет."""
    match stage:
        case Stage.DONORS | Stage.ADVERTISERS:
            return _Answers()
        case Stage.SALES:
            zones = await stage_forecast.sales_zones(session) if policy.window else None
            return _Answers(
                await stage_forecast.sales_supply(session),
                _common(zones or {}),
                await stage_forecast.sales_stuck(session),
            )
        case _:
            assert_never(stage)


def _common(zones: Mapping[str | None, int]) -> ZoneInfo | None:
    """Самый частый пояс получателей очереди — в нём прогноз считает часы окна; равные — по имени,
    чтобы ответ не скакал; имени нет в базе поясов — следующий (`window.zone_of`). `None` — поясов
    не знает никто."""
    ranked = sorted(((count, name) for name, count in zones.items() if name), reverse=True)
    return window.zone_of(name for _, name in ranked)


async def _due(
    session: AsyncSession, stage: Stage, first_day: date, until: datetime
) -> dict[date, int]:
    """Добивки этапа по UTC-дню срока — условием прохода добивок (`followup_pending`); просроченные —
    сегодня: проход возьмёт их первым же заходом."""
    day = cast(func.timezone(_UTC, MessageModel.next_action_at), Date)
    rows = await session.execute(
        select(day, func.count())
        .select_from(MessageModel)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(*followup_pending(), CampaignModel.stage == stage)
        .where(MessageModel.next_action_at < until)
        .group_by(day)
    )
    due: Counter[date] = Counter()
    for found, count in rows.tuples().all():
        due[max(found, first_day)] += count
    return dict(due)


async def _sent_by_day(session: AsyncSession, stage: Stage, since: datetime) -> dict[date, Sent]:
    """Ушедшие письма этапа по UTC-дням с `since`: первые — счётом капа (`DAILY_STEPS`), добивки —
    шаги цепочки. Одним запросом на все дни."""
    day = cast(func.timezone(_UTC, MessageModel.sent_at), Date)
    chained = and_(MessageModel.step > FIRST_STEP, MessageModel.step < MAX_STEPS)
    rows = await session.execute(
        select(
            day,
            func.count().filter(MessageModel.step.in_(DAILY_STEPS)),
            func.count().filter(chained),
        )
        .select_from(MessageModel)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(CampaignModel.stage == stage, MessageModel.sent_at >= since)
        .group_by(day)
    )
    return {found: Sent(first, later) for found, first, later in rows.tuples().all()}
