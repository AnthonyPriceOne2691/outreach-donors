"""Недельный отчёт продаж в формате подрядчика (срез 6.3): восемь разделов за прошлую неделю.

**Формат подрядчика** — каждый понедельник: контакты, отправки, доставляемость, ответы, диалоги,
лиды поимённо, проблемы, план; «цифры из системы, сверяемы». Неделя — с понедельника по
воскресенье по Москве (`Week`), числа — по гипотезам и всего.

**Каждое число — функцией экрана.** Своего счёта у отчёта нет:
- контакты — функцией экрана гипотез (`browse.hypotheses`) по лидам, заведённым за неделю;
- воронка недели (в очереди, отправлено, доставлено, отказ, ответ, передан) — функцией экрана
  воронки (`funnel.board`) за ту же неделю: лиды, а не письма, по первому письму лида;
- очередь плана — функцией экрана очереди (`queue.state`);
- чего на экранах нет — функциями рядом (`funnel.letters`, `weekly_counts.py`).
Одна сборка (`build`) кормит текст в группу продаж, экран отчёта и выгрузку CSV
(`weekly_text.py`): три вида одного отчёта не расходятся.

**MQL и SQL не считаем:** их ставит телемаркетолог в Kommo, обратной синхронизации нет. Лид
отчёта — «передан телемаркетологу».

**Диалоги, тревоги сторожа и план — на момент сборки:** состояние диалога и тревоги считаются,
а не хранятся, очередь и разгон — о следующей неделе.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from itertools import zip_longest
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as sales_cfg
from backend.features.letters.chain import MAX_STEPS
from backend.features.ops import mail_watch
from backend.features.sales import (
    browse,
    connection,
    funnel,
    handoff_console,
    queue,
    weekly_counts,
)
from backend.features.sales.funnel import Funnel, Period
from backend.features.sales.models import LeadStatus
from backend.features.sales.weekly_counts import AUTO, KINDS, Dialogs, Handed, Mail, Runway

#: Неделя отчёта — по Москве: с полуночи понедельника до полуночи следующего.
WEEK_ZONE = ZoneInfo("Europe/Moscow")
WEEK = timedelta(days=7)
#: Сколько прошлых недель предлагает экран отчёта.
WEEKS_SHOWN = 12
#: Виды ответа отчёта и автоответ — все названы, хоть и нулём.
REPLY_KEYS: tuple[str, ...] = (*KINDS, AUTO)


@dataclass(frozen=True, slots=True, order=True)
class Week:
    """Неделя отчёта: с понедельника `start` по воскресенье включительно, по Москве."""

    start: date

    @classmethod
    def containing(cls, moment: datetime) -> Week:
        """Неделя, в которой лежит момент, — по часам Москвы, а не сервера."""
        local = moment.astimezone(WEEK_ZONE).date()
        return cls(local - timedelta(days=local.weekday()))

    @classmethod
    def last_complete(cls, now: datetime) -> Week:
        """Прошлая неделя — последняя целиком прошедшая к моменту `now`."""
        return cls(cls.containing(now).start - WEEK)

    @property
    def since(self) -> datetime:
        return datetime.combine(self.start, time.min, tzinfo=WEEK_ZONE)

    @property
    def until(self) -> datetime:
        return datetime.combine(self.start + WEEK, time.min, tzinfo=WEEK_ZONE)

    @property
    def period(self) -> Period:
        """Полуинтервал недели — тем же типом, что период экрана воронки."""
        return Period(self.since, self.until)

    @property
    def days(self) -> list[date]:
        return [self.start + timedelta(days=offset) for offset in range(WEEK.days)]

    @property
    def next(self) -> Week:
        return Week(self.start + WEEK)

    def words(self) -> str:
        """«05.10–11.10» — так неделю называют отчёт и экран."""
        return f"{self.start:%d.%m}–{self.days[-1]:%d.%m}"


def recent(now: datetime) -> list[Week]:
    """Прошедшие недели для выбора на экране — новые первыми."""
    last = Week.last_complete(now)
    return [Week(last.start - WEEK * back) for back in range(WEEKS_SHOWN)]


@dataclass(frozen=True, slots=True)
class Numbers:
    """Числа одной гипотезы или всех за неделю — каждое функцией экрана или рядом с ним."""

    #: Контакты: лидов заведено за неделю и в каком они состоянии (`browse.hypotheses`).
    loaded: int = 0
    ready: int = 0
    rejected: int = 0
    cleaning: int = 0
    #: Воронка недели — лиды по первому письму (`funnel.board`).
    funnel: Funnel = field(default_factory=Funnel)
    #: Писем цепочки за неделю по шагам, первое — нулевым (`funnel.letters`).
    steps: tuple[int, ...] = (0,) * MAX_STEPS
    #: Ответы за неделю по видам и автоответы (`weekly_counts.replies`); названы все.
    replies: dict[str, int] = field(default_factory=lambda: dict.fromkeys(REPLY_KEYS, 0))
    #: Диалоги на момент сборки (`weekly_counts.dialogs`).
    dialogs: Dialogs = field(default_factory=Dialogs)
    #: Лидов передано телемаркетологу за неделю — по времени передачи.
    handed: int = 0
    #: План: готовых лидов без письма и первых писем в очереди (`queue.state`).
    unwritten: int = 0
    queued: int = 0

    @property
    def answers(self) -> int:
        """Ответов за неделю: все виды, кроме автоответа — он ответом не считается."""
        return sum(n for kind, n in self.replies.items() if kind != AUTO)

    def __add__(self, other: Numbers) -> Numbers:
        return Numbers(
            loaded=self.loaded + other.loaded,
            ready=self.ready + other.ready,
            rejected=self.rejected + other.rejected,
            cleaning=self.cleaning + other.cleaning,
            funnel=self.funnel + other.funnel,
            steps=tuple(a + b for a, b in zip_longest(self.steps, other.steps, fillvalue=0)),
            replies={key: self.replies[key] + other.replies[key] for key in REPLY_KEYS},
            dialogs=Dialogs(
                self.dialogs.active + other.dialogs.active,
                self.dialogs.silent + other.dialogs.silent,
                self.dialogs.waiting + other.dialogs.waiting,
            ),
            handed=self.handed + other.handed,
            unwritten=self.unwritten + other.unwritten,
            queued=self.queued + other.queued,
        )


@dataclass(frozen=True, slots=True)
class Row:
    """Числа одной гипотезы."""

    hypothesis_id: int
    name: str
    numbers: Numbers


@dataclass(frozen=True, slots=True)
class Problem:
    """Тревога или сбой: что и подробности словами. `now` — состояние на момент сборки
    (сторож, передачи, подключение), а не событие недели."""

    title: str
    detail: str
    now: bool = False


@dataclass(frozen=True, slots=True)
class Plan:
    """План на следующую неделю: разгон ящиков по дням отправки."""

    week: Week
    runway: Runway


@dataclass(frozen=True, slots=True)
class WeeklyReport:
    """Отчёт недели целиком: строки гипотез и итог, ящики, лиды, проблемы, план."""

    week: Week
    built_at: datetime
    rows: list[Row]
    total: Numbers
    mail: Mail
    leads: list[Handed]
    problems: list[Problem]
    plan: Plan


@dataclass(frozen=True, slots=True)
class _Found:
    """Что насчитали функции экранов — по гипотезам."""

    loaded: dict[int, dict[str, int]]
    steps: dict[int, list[int]]
    replies: dict[int, dict[str, int]]
    dialogs: dict[int, Dialogs]
    handed: dict[int, int]
    queues: dict[int, queue.QueueState]

    def numbers(self, hypothesis_id: int, found: Funnel) -> Numbers:
        states = self.loaded.get(hypothesis_id, {})
        replies = self.replies.get(hypothesis_id, {})
        line = self.queues[hypothesis_id]
        return Numbers(
            loaded=sum(states.values()),
            ready=states.get(LeadStatus.READY.value, 0),
            rejected=states.get(LeadStatus.REJECTED.value, 0),
            cleaning=states.get(LeadStatus.NEW.value, 0),
            funnel=found,
            steps=tuple(self.steps.get(hypothesis_id, [0] * MAX_STEPS)),
            replies={kind: replies.get(kind, 0) for kind in REPLY_KEYS},
            dialogs=self.dialogs.get(hypothesis_id, Dialogs()),
            handed=self.handed.get(hypothesis_id, 0),
            unwritten=line.unwritten,
            queued=line.queued,
        )


async def build(session: AsyncSession, week: Week, *, now: datetime) -> WeeklyReport:
    """Отчёт недели — функциями экранов за ту же неделю. Ничего не пишет."""
    period = week.period
    board = await funnel.board(session, period)
    leads = await weekly_counts.handed(session, period)
    handed: dict[int, int] = {}
    for lead in leads:
        handed[lead.hypothesis_id] = handed.get(lead.hypothesis_id, 0) + 1
    found = _Found(
        loaded={row.hypothesis.id: row.states for row in await browse.hypotheses(session, period)},
        steps=await funnel.letters(session, period),
        replies={key: dict(n) for key, n in (await weekly_counts.replies(session, period)).items()},
        dialogs=await weekly_counts.dialogs(session, week.since),
        handed=handed,
        queues={
            row.hypothesis_id: await queue.state(session, row.hypothesis_id) for row in board.rows
        },
    )
    rows = [
        Row(row.hypothesis_id, row.name, found.numbers(row.hypothesis_id, row.funnel))
        for row in board.rows
    ]
    mail = await weekly_counts.mail(session, period)
    sending = [day for day in week.next.days if day.weekday() in sales_cfg.SEND_DAYS]
    return WeeklyReport(
        week=week,
        built_at=now,
        rows=rows,
        total=sum((row.numbers for row in rows), Numbers()),
        mail=mail,
        leads=leads,
        problems=await _problems(session, period, now=now, mail=mail),
        plan=Plan(week.next, await weekly_counts.runway(session, sending)),
    )


async def _problems(
    session: AsyncSession, period: Period, *, now: datetime, mail: Mail
) -> list[Problem]:
    """Тревоги и сбои: сторож почты продаж и подключение — на момент сборки, паузы ящиков и
    недоставленные сообщения — недели, передачи, где Kommo решает человек, — на сейчас."""
    found = [
        Problem(alarm.title, alarm.detail, now=True)
        for alarm in await mail_watch.alarms(session, now, stages=weekly_counts.SALES)
    ]
    missing = await connection.missing(session)
    if missing:
        found.append(Problem("Отправка продаж не готова", "; ".join(missing), now=True))
    found += [
        Problem(
            f"Ящик {pause.box} встал на паузу {pause.at.astimezone(WEEK_ZONE):%d.%m %H:%M} по Москве",
            pause.reason,
        )
        for pause in mail.pauses
    ]
    found += [
        Problem(
            f"Передача №{row.id} ждёт решения в Kommo",
            f"{row.email}: {handoff_console.KOMMO_WORDS[row.kommo]}"
            + (f" — {row.reason}" if row.reason else ""),
            now=True,
        )
        for row in await handoff_console.waiting(session)
    ]
    leads, drafts = await weekly_counts.undelivered(session, period)
    if leads:
        found.append(
            Problem(
                "Сообщения о лидах не доставлены",
                f"{leads} — телемаркетолог их не получил; лиды ждут в переписке продаж",
            )
        )
    if drafts:
        found.append(
            Problem(
                "Сообщения о черновиках агента не доставлены",
                f"{drafts}: черновики целы и ждут человека в переписке",
            )
        )
    return found
