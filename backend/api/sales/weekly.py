"""Недельный отчёт продаж — под правом `sales`: восемь разделов за неделю, по гипотезам, и CSV.

`GET /sales/weekly?week=2026-10-05` — без записи: отчёт недели (её понедельник по Москве; пусто —
прошлая неделя), собранный теми же функциями экранов, что и сообщение в группу продаж
(`features/sales/weekly.py`), разделы — теми же словами (`weekly_text.sections`), и как отчёт
этой недели ушёл в группу (`sales_weekly_reports`). `GET /sales/weekly.csv?week=…` — те же числа
файлом для Excel. Экран своего счёта не ведёт: отчёт, экран и выгрузка не расходятся.

Не понедельник или неделя, что ещё идёт, — 422 словами.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.config import sales as sales_cfg
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import weekly, weekly_jobs, weekly_text
from backend.features.sales.weekly import Numbers, Week, WeeklyReport
from backend.features.sales.weekly_models import SalesWeeklyReportModel, WeeklyStatus

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`) — как воронка.
router = APIRouter()

_seller = Depends(needs(Permission.SALES))

NOT_MONDAY = "неделя отчёта начинается с понедельника — выберите понедельник"
NOT_OVER = "эта неделя ещё не кончилась — отчёт бывает за прошедшую неделю"

#: День недели расписания — словами строки экрана.
_DAYS = ("понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье")


class WeeklyNumbers(BaseModel):
    """Числа гипотезы или итога за неделю — те же, что в разделах и выгрузке."""

    loaded: int
    ready: int
    rejected: int
    cleaning: int
    #: Воронка недели — лиды по первому письму, как на экране воронки за те же даты.
    queued: int
    sent: int
    delivered: int
    bounced: int
    answered: int
    handed_off: int
    #: Писем цепочки за неделю по шагам, первое — первым.
    letters: list[int]
    #: Ответов за неделю (без автоответов) и по видам — ключи `weekly_counts.KINDS` и
    #: `auto_reply`.
    answers: int
    replies: dict[str, int]
    active: int
    silent: int
    waiting: int
    #: Передано телемаркетологу за неделю — по времени передачи.
    handed: int
    #: План: готовых лидов без письма и первых писем в очереди.
    unwritten: int
    letters_queued: int

    @classmethod
    def of(cls, n: Numbers) -> WeeklyNumbers:
        return cls(
            loaded=n.loaded,
            ready=n.ready,
            rejected=n.rejected,
            cleaning=n.cleaning,
            queued=n.funnel.queued,
            sent=n.funnel.sent,
            delivered=n.funnel.delivered,
            bounced=n.funnel.bounced,
            answered=n.funnel.answered,
            handed_off=n.funnel.handed_off,
            letters=list(n.steps),
            answers=n.answers,
            replies=dict(n.replies),
            active=n.dialogs.active,
            silent=n.dialogs.silent,
            waiting=n.dialogs.waiting,
            handed=n.handed,
            unwritten=n.unwritten,
            letters_queued=n.queued,
        )


class WeeklyRow(BaseModel):
    hypothesis_id: int
    name: str
    numbers: WeeklyNumbers


class WeeklySection(BaseModel):
    """Раздел отчёта: первая строка — итог, следующие — подробности."""

    key: str
    title: str
    lines: list[str]


class WeeklyDelivery(BaseModel):
    """Как отчёт недели ушёл в группу продаж.

    `sent` — Telegram принял; `pending` — задача отправки в пути; `undelivered` — не дошёл
    (`due_at` — когда повтор, пусто — повторов больше нет); `none` — в группу не отправлялся;
    `off` — в группу отчёт не уходит (продажи выключены или время не задано)."""

    status: Literal["sent", "pending", "undelivered", "none", "off"]
    sent_at: datetime | None = None
    due_at: datetime | None = None
    error: str | None = None
    #: Когда отчёт уходит в группу — словами; пусто — не уходит.
    schedule: str | None = None


class SalesWeeklyView(BaseModel):
    """Экран недельного отчёта: неделя, разделы словами, числа по гипотезам, отправка."""

    week: date
    last_day: date
    #: Прошедшие недели для выбора, новые первыми.
    weeks: list[date]
    built_at: datetime
    sections: list[WeeklySection]
    rows: list[WeeklyRow]
    total: WeeklyNumbers
    delivery: WeeklyDelivery


def _week(start: date | None, now: datetime) -> Week:
    """Неделя запроса: понедельник, неделя прошла; пусто — прошлая неделя."""
    if start is None:
        return Week.last_complete(now)
    if start.weekday() != 0:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NOT_MONDAY)
    chosen = Week(start)
    if chosen.until > now:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, NOT_OVER)
    return chosen


def schedule() -> str | None:
    """Когда отчёт уходит в группу — «по понедельникам в 09:00 по Москве»; не уходит — `None`."""
    if not weekly_jobs.switched_on() or sales_cfg.WEEKLY_REPORT_AT is None:
        return None
    day, at = sales_cfg.WEEKLY_REPORT_AT
    return f"в {_DAYS[day]} в {at:%H:%M} по Москве, за прошлую неделю"


def delivery(row: SalesWeeklyReportModel | None) -> WeeklyDelivery:
    """Исход отправки отчёта недели — из журнала; строки нет — не отправлялся или не уходит."""
    planned = schedule()
    if row is None:
        return WeeklyDelivery(status="none" if planned else "off", schedule=planned)
    said: Literal["sent", "pending", "undelivered"] = (
        "sent"
        if row.status == WeeklyStatus.SENT.value
        else "pending"
        if row.status == WeeklyStatus.PENDING.value
        else "undelivered"
    )
    return WeeklyDelivery(
        status=said, sent_at=row.sent_at, due_at=row.due_at, error=row.error, schedule=planned
    )


def clock() -> datetime:
    """Сейчас — зависимостью: «прошлая неделя» и «неделя ещё идёт» тест считает от своих часов."""
    return datetime.now(UTC)


async def _report(session: AsyncSession, start: date | None, now: datetime) -> WeeklyReport:
    return await weekly.build(session, _week(start, now), now=now)


@router.get("/weekly", response_model=SalesWeeklyView, summary="Недельный отчёт продаж")
async def read_weekly(
    week: date | None = Query(default=None, description="понедельник недели; пусто — прошлая"),
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
    now: datetime = Depends(clock),
) -> SalesWeeklyView:
    report = await _report(session, week, now)
    row = await session.scalar(
        select(SalesWeeklyReportModel).where(SalesWeeklyReportModel.week_start == report.week.start)
    )
    return SalesWeeklyView(
        week=report.week.start,
        last_day=report.week.days[-1],
        weeks=[one.start for one in weekly.recent(report.built_at)],
        built_at=report.built_at,
        sections=[
            WeeklySection(key=part.key, title=part.title, lines=part.lines)
            for part in weekly_text.sections(report)
        ],
        rows=[
            WeeklyRow(
                hypothesis_id=row.hypothesis_id,
                name=row.name,
                numbers=WeeklyNumbers.of(row.numbers),
            )
            for row in report.rows
        ],
        total=WeeklyNumbers.of(report.total),
        delivery=delivery(row),
    )


@router.get("/weekly.csv", summary="Недельный отчёт продаж файлом")
async def export_weekly(
    week: date | None = Query(default=None, description="понедельник недели; пусто — прошлая"),
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
    now: datetime = Depends(clock),
) -> Response:
    """Те же числа, что на экране и в группе, — строками таблицы для Excel."""
    report = await _report(session, week, now)
    return Response(
        content=weekly_text.to_csv(report),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="sales-week-{report.week.start}.csv"'
        },
    )
