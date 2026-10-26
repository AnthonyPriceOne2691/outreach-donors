"""Числа недельного отчёта продаж, которых нет на других экранах (срез 6.3).

Что экраны уже считают, отчёт берёт у них: воронку и письма по шагам — у воронки
(`funnel.board`, `funnel.letters`), контакты — у экрана гипотез (`browse.hypotheses`),
очередь — у экрана очереди (`queue.state`). Здесь — то, чего ни на одном экране нет: ответы
по видам, диалоги отчёта, лиды, переданные за неделю, жалобы и паузы ящиков, сообщения бота
продаж, которые не дошли, и разгон ящиков на неделю вперёд. Каждое — одной функцией, и зовёт
её одна сборка (`weekly.build`): текст в группу продаж, экран отчёта и выгрузка — три вида
одного отчёта и не расходятся. Разгон — не свой счёт, а мощность ящиков прогноза рассылки
(`outreach/capacity.capacity`): план недели и прогноз на те же дни — одно число.

**Ответ — по своему времени в неделе**, вид — правилами приёма, а у ответа человека — видом
модели продаж (`reply_kind.SalesKind`). Отказ доставки — не ответ лида: он в доставляемости.
Автоответ — своей строкой: ответом он не считается, как и в воронке (`letters/attempts.ANSWERS`).

**Диалог** — ответивший лид, которого ещё не передали телемаркетологу и чей адрес не закрыт:
состояние — правилом списка «Диалогов» (`OutreachRepository.threads`, `threads.state_of`).
Активный — с начала недели в нём было движение (письмо или ответ), молчащий — нет. Состояние
диалога считается, а не хранится, и прошлого у него нет: диалоги — на момент сборки отчёта.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as outreach_cfg
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import ReplyModel, SenderHealthModel, SenderModel
from backend.features.outreach.capacity import capacity
from backend.features.outreach.repository import OutreachRepository
from backend.features.outreach.threads import ThreadState
from backend.features.sales.funnel import Period
from backend.features.sales.handoff_text import dialog_link
from backend.features.sales.models import (
    HandoffTelegram,
    NoticeStatus,
    SalesDraftNoticeModel,
    SalesHandoffModel,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.reply_kind import SalesKind

#: Только ящики продаж: остальные направления в отчёте продаж не видны.
SALES = (Stage.SALES,)

#: Ответ человека, вида которого нет: модель не разобрала, не звалась или продажи выключены.
UNPARSED = "unparsed"
#: Автоответ — своей строкой отчёта: ответом он не считается.
AUTO = "auto_reply"
#: Виды ответа в порядке строки отчёта. «Просит не писать» — и правилами приёма, и словами
#: модели: для отчёта это один вид.
KINDS: tuple[str, ...] = (
    SalesKind.WANTS_TO_TALK.value,
    SalesKind.QUESTION.value,
    SalesKind.INTERESTED.value,
    SalesKind.REFERRAL.value,
    SalesKind.NOT_NOW.value,
    SalesKind.NOT_INTERESTED.value,
    SalesKind.UNSUBSCRIBE.value,
    UNPARSED,
)

#: Строки журнала здоровья ящика (`outreach/health.py`): жалоба на спам и пауза по окну писем.
COMPLAINT = "complaint"
PAUSED = "paused"

#: Состояния списка «Диалогов», в которых лид ответил человеком и адрес не закрыт.
TALKING = frozenset({ThreadState.REPLIED, ThreadState.SALES_PENDING})


def kind_of(rule: ReplyKind, snapshot: Mapping[str, Any] | None) -> str | None:
    """Вид ответа в отчёте: правилами приёма, у ответа человека — видом модели продаж.
    `None` — не ответ лида: отказ доставки."""
    match rule:
        case ReplyKind.BOUNCE:
            return None
        case ReplyKind.AUTO_REPLY:
            return AUTO
        case ReplyKind.UNSUBSCRIBE:
            return SalesKind.UNSUBSCRIBE.value
        case ReplyKind.HUMAN:
            said = (snapshot or {}).get("kind")
            return said if said in KINDS else UNPARSED


async def replies(session: AsyncSession, period: Period) -> dict[int, Counter[str]]:
    """Ответы лидов, пришедшие в период, — по гипотезам и видам (`kind_of`)."""
    rows = await session.execute(
        select(SalesLeadModel.hypothesis_id, ReplyModel.kind, ReplyModel.model_parse)
        .join(SalesThreadModel, SalesThreadModel.thread_id == ReplyModel.thread_id)
        .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
        .where(period.holds(ReplyModel.created_at))
    )
    found: dict[int, Counter[str]] = {}
    for hypothesis_id, rule, snapshot in rows.tuples():
        kind = kind_of(rule, snapshot)
        if kind is not None:
            found.setdefault(int(hypothesis_id), Counter())[kind] += 1
    return found


@dataclass(frozen=True, slots=True)
class Dialogs:
    """Диалоги гипотезы на момент сборки: активные и молчащие с начала недели, ждут человека."""

    active: int = 0
    silent: int = 0
    #: Из них ответ ждёт человека (`ThreadState.SALES_PENDING`).
    waiting: int = 0


async def dialogs(session: AsyncSession, since: datetime) -> dict[int, Dialogs]:
    """Диалоги по гипотезам — правилом списка «Диалогов»; переданные лиды — не диалоги отчёта:
    разговор с ними ведёт телемаркетолог."""
    rows = await OutreachRepository(session).threads(stages=SALES)
    owners = {
        thread_id: (int(hypothesis_id), int(lead_id))
        for thread_id, hypothesis_id, lead_id in (
            await session.execute(
                select(
                    SalesThreadModel.thread_id, SalesLeadModel.hypothesis_id, SalesLeadModel.id
                ).join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
            )
        ).tuples()
    }
    handed = set((await session.scalars(select(SalesHandoffModel.lead_id))).all())
    tally: dict[int, Counter[str]] = {}
    for row in rows:
        owner = owners.get(row.thread.id)
        if owner is None or owner[1] in handed or row.summary.state not in TALKING:
            continue
        moved = row.summary.last_event_at is not None and row.summary.last_event_at >= since
        counted = tally.setdefault(owner[0], Counter())
        counted["active" if moved else "silent"] += 1
        counted["waiting"] += row.summary.state is ThreadState.SALES_PENDING
    return {hypothesis: Dialogs(**counted) for hypothesis, counted in tally.items()}


@dataclass(frozen=True, slots=True)
class Handed:
    """Лид, переданный телемаркетологу за неделю: кто он и куда смотреть."""

    hypothesis_id: int
    hypothesis: str
    company: str
    site: str
    name: str
    email: str
    #: Ссылка, которую получил телемаркетолог (на сделку, без неё — на диалог); не получил —
    #: на диалог в сервисе.
    link: str
    at: datetime


async def handed(session: AsyncSession, period: Period) -> list[Handed]:
    """Лиды, чья передача заведена в период, — по времени передачи."""
    rows = await session.execute(
        select(SalesHandoffModel, SalesLeadModel, DomainModel.host, SalesHypothesisModel.name)
        .join(SalesLeadModel, SalesLeadModel.id == SalesHandoffModel.lead_id)
        .join(DomainModel, DomainModel.id == SalesLeadModel.domain_id)
        .join(SalesHypothesisModel, SalesHypothesisModel.id == SalesLeadModel.hypothesis_id)
        .where(period.holds(SalesHandoffModel.created_at))
        .order_by(SalesHandoffModel.created_at, SalesHandoffModel.id)
    )
    return [
        Handed(
            hypothesis_id=lead.hypothesis_id,
            hypothesis=name,
            company=lead.company or "",
            site=host,
            name=lead.name or "",
            email=lead.email,
            link=row.notified_link or dialog_link(row.thread_id),
            at=row.created_at,
        )
        for row, lead, host, name in rows.tuples()
    ]


@dataclass(frozen=True, slots=True)
class Pause:
    """Ящик продаж встал на паузу по окну писем — из журнала здоровья."""

    box: str
    reason: str
    at: datetime


@dataclass(frozen=True, slots=True)
class Mail:
    """Ящики продаж за неделю: жалобы и паузы — по своему времени, ящики — на момент сборки."""

    complaints: int
    pauses: list[Pause]
    #: Ящиков продаж всего — как на экране «Домены рассылки».
    boxes: int
    #: Из них сейчас не пишут: выключены или на паузе.
    idle: int


async def mail(session: AsyncSession, period: Period) -> Mail:
    """Жалобы и паузы ящиков продаж за период и ящики на момент сборки."""
    boxes = await OutreachRepository(session).senders(stages=SALES)
    rows = await session.execute(
        select(
            SenderHealthModel.kind,
            SenderHealthModel.detail,
            SenderHealthModel.at,
            SenderModel.email,
        )
        .join(SenderModel, SenderModel.id == SenderHealthModel.sender_id)
        .where(
            SenderModel.stage == Stage.SALES,
            SenderHealthModel.kind.in_((COMPLAINT, PAUSED)),
            period.holds(SenderHealthModel.at),
        )
        .order_by(SenderHealthModel.at, SenderHealthModel.id)
    )
    complaints, pauses = 0, []
    for kind, detail, at, email in rows.tuples():
        if kind == COMPLAINT:
            complaints += 1
        else:
            pauses.append(Pause(email, detail or "причина не записана", at))
    return Mail(complaints, pauses, len(boxes), sum(1 for box in boxes if not box.enabled))


async def undelivered(session: AsyncSession, period: Period) -> tuple[int, int]:
    """Сообщения бота продаж, которые не дошли и последний раз пробовались в период: о лидах
    телемаркетологу и о черновиках агента в группу."""
    leads = await session.scalar(
        select(func.count(SalesHandoffModel.id)).where(
            SalesHandoffModel.telegram == HandoffTelegram.UNDELIVERED,
            period.holds(SalesHandoffModel.updated_at),
        )
    )
    drafts = await session.scalar(
        select(func.count(SalesDraftNoticeModel.id)).where(
            SalesDraftNoticeModel.status == NoticeStatus.UNDELIVERED.value,
            period.holds(SalesDraftNoticeModel.updated_at),
        )
    )
    return int(leads or 0), int(drafts or 0)


@dataclass(frozen=True, slots=True)
class Day:
    """День недели плана и сколько первых писем ящики продаж могут в него отправить."""

    day: date
    letters: int


@dataclass(frozen=True, slots=True)
class Runway:
    """Разгон на неделю: включённых ящиков продаж и потолок первых писем по дням отправки."""

    boxes: int
    days: list[Day]
    #: Лимит первых писем направления продаж не прочитан из настроек
    #: (`outreach_cfg.direction_unread`): писем ноль, пока его не поправят, — «0 писем» по дням
    #: опечатку не выдаёт. Имя переменной — для администратора; `None` — прочитан.
    unread: str | None = None


async def runway(session: AsyncSession, days: Sequence[date], *, now: datetime) -> Runway:
    """Потолок первых писем на каждый день — мощностью ящиков прогноза рассылки
    (`outreach/capacity.capacity`, правило экрана «Домены рассылки»): потолок разгона ящика, лимит и
    закрытость домена, дневной лимит направления; выключенный ящик не пишет. Как у прогноза, ящик
    без первого письма начинает разгон в первый день, когда письма ещё уходят, считая от момента
    сборки отчёта `now` (`project_first_letter`, `capacity.first_letter_day`): иначе план недели и
    прогноз на те же дни разошлись бы на новых ящиках."""
    found = await capacity(session, Stage.SALES, days, project_first_letter=True, now=now)
    return Runway(
        found.boxes,
        [Day(day.day, day.letters) for day in found.days],
        outreach_cfg.direction_unread(Stage.SALES.value),
    )
