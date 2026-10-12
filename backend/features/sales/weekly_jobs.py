"""Недельный отчёт продаж — в группу продаж по расписанию, один раз за неделю (срез 6.3).

**По умолчанию не уходит.** Всё, что продажи шлют наружу без человека, по умолчанию выключено:
отправку включает человек днём и временем `SALES_WEEKLY_REPORT_AT` по Москве (рекомендуемое
«MON 09:00»). С этой минуты отчёт за прошлую неделю (пн–вс) ждёт отправки. Проход продаж
процесса разбора (`handoff_jobs.retry_pass`, раз в `HANDOFF_PASS_SEC`) заводит строку недели и
ставит задачу в очередь продаж; Telegram зовёт задача, а не разбор: его сбой — повтор позже, а не
мёртвый сервис.
Проход опоздал (процесс лежал) — отчёт уходит позже, но той же недели; неделя прошла — уже
следующий отчёт: прошлый на экране, его выгрузка там же.

**Один раз.** Строка недели одна (`uq_sales_weekly_reports_week`): повтор прохода её не
задваивает, а задача отправленный отчёт второй раз не шлёт. Взятой строке проход сразу
сдвигает срок на `HANDOFF_RETRY_SEC`: следующий круг не поставит вторую задачу, пока первая не
отработала, а потерянную — поставит. Окно двойной отправки то же, что у остальных сообщений
бота продаж: Telegram принял, а строка не записалась — повтор пошлёт отчёт ещё раз.

**Telegram недоступен** — серия повторов сообщений бота продаж (`telegram_series.py`): сеть,
5xx или 429 — повтор проходом через 5, 10, 20, 40 мин, у 429 — не раньше паузы Telegram;
попыток пять, после последней — тревога эксплуатации другим ботом. Постоянный отказ (нет токена,
чат группы не задан, бот не в группе) — тревога сразу. Отчёт от исхода не зависит: экран и
выгрузка считают его заново.

**Продажи выключены (`SALES_ENABLED`) или время отчёта не задано — отчёта в группу нет:** проход
строк не заводит и не берёт, задача в Telegram не ходит.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from rq import get_current_job
from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import sales as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.runs.failures import described
from backend.features.sales import weekly, weekly_text
from backend.features.sales.telegram import SalesBot, TelegramError
from backend.features.sales.telegram_series import MESSAGE_TRIES, after_failure
from backend.features.sales.weekly import Week
from backend.features.sales.weekly_models import SalesWeeklyReportModel, WeeklyStatus
from backend.shared.alerts import send_alert
from backend.shared.logs import setup_logging
from backend.shared.queue import remember_job_error, sales_queue, with_retries

logger = logging.getLogger(__name__)

#: Путь задачи строкой: очередь импортирует её в воркере (`shared/queue.py`).
SEND_JOB = "backend.features.sales.weekly_jobs.send_weekly_report"

#: Почему отчёта в группу нет.
SWITCHED_OFF = (
    "продажи выключены (SALES_ENABLED) или время отчёта не задано (SALES_WEEKLY_REPORT_AT) — "
    "отчёт в группу не уходит"
)

Alert = Callable[[str], Awaitable[object]]
Clock = Callable[[], datetime]


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _http() -> httpx.AsyncClient:
    """Клиент на одну задачу. Отдельной функцией — её подменяет тест: сети в тестах нет."""
    return httpx.AsyncClient()


def switched_on() -> bool:
    """Уходит ли отчёт в группу: продажи включены и время отчёта задано."""
    return cfg.ENABLED and cfg.WEEKLY_REPORT_AT is not None


def scheduled(week: Week) -> datetime | None:
    """С какой минуты отчёт недели ждёт отправки: день и время `SALES_WEEKLY_REPORT_AT`
    недели, что идёт за ней, по Москве. `None` — время не задано."""
    if cfg.WEEKLY_REPORT_AT is None:
        return None
    day, at = cfg.WEEKLY_REPORT_AT
    return datetime.combine(week.next.start + timedelta(days=day), at, tzinfo=weekly.WEEK_ZONE)


async def due(session: AsyncSession, *, now: datetime) -> list[tuple[int, int]]:
    """Проход: время отчёта прошлой недели пришло — строка недели (одна на неделю); затем
    строки, которым пора, — номер и неудач в серии. Срок взятых сдвигается сразу."""
    if not switched_on():
        return []
    week = Week.last_complete(now)
    moment = scheduled(week)
    if moment is not None and now >= moment:
        await session.execute(
            insert(SalesWeeklyReportModel)
            .values(week_start=week.start, status=WeeklyStatus.PENDING.value, tries=0, due_at=now)
            .on_conflict_do_nothing(constraint="uq_sales_weekly_reports_week")
        )
    taken = (
        await session.execute(
            select(SalesWeeklyReportModel.id, SalesWeeklyReportModel.tries)
            .where(
                SalesWeeklyReportModel.status != WeeklyStatus.SENT.value,
                SalesWeeklyReportModel.due_at <= now,
            )
            .order_by(SalesWeeklyReportModel.due_at)
            .with_for_update(skip_locked=True)
        )
    ).all()
    found = [(int(report_id), int(tries)) for report_id, tries in taken]
    if found:
        await session.execute(
            update(SalesWeeklyReportModel)
            .where(SalesWeeklyReportModel.id.in_([report_id for report_id, _ in found]))
            .values(due_at=now + timedelta(seconds=cfg.HANDOFF_RETRY_SEC))
        )
    await session.commit()
    return found


def queue_send(report_id: int, tries: int) -> None:
    """Поставить отправку отчёта в очередь продаж; очередь недоступна — `RedisError`
    вызывающему (проход пишет строку в журнал, срок уже сдвинут)."""
    sales_queue().enqueue(SEND_JOB, report_id, tries, **with_retries())


async def send(
    session: AsyncSession,
    report_id: int,
    tries: int,
    bot: SalesBot,
    *,
    alert: Alert,
    now: Clock = _utcnow,
) -> dict[str, Any]:
    """Отчёт недели строки — в группу продаж: собран функциями экранов в минуту отправки.
    Отправленный и опоздавший повтор (`tries` не тот) не идут."""
    if not switched_on():
        return {"report": report_id, "skipped": SWITCHED_OFF}
    row = await session.get(SalesWeeklyReportModel, report_id, populate_existing=True)
    why = _not_now(row, tries)
    if row is None or why is not None:
        return {"report": report_id, "skipped": why}
    report = await weekly.build(session, Week(row.week_start), now=now())
    text = weekly_text.message(report)
    failure = await _deliver(bot, report_id, text)
    row.text = text
    if failure is None:
        row.status, row.sent_at, row.error = WeeklyStatus.SENT.value, now(), None
        row.tries, row.due_at = 0, None
        await session.commit()
        return {"report": report_id, "status": row.status}
    failed = after_failure(row.tries, failure, now(), told=False)
    row.status, row.error = WeeklyStatus.UNDELIVERED.value, failed.error(failure)
    row.tries, row.due_at = failed.tries, failed.due_at
    await session.commit()
    if failed.alarm:
        await alert(_alarm(report, failed.spent, failure))
    return {"report": report_id, "status": row.status, "error": row.error}


def _not_now(row: SalesWeeklyReportModel | None, tries: int) -> str | None:
    """Почему не слать: строки нет, отчёт уже ушёл или эту попытку уже сделала другая задача."""
    if row is None:
        return "строки недели нет — отправлять нечего"
    if row.status == WeeklyStatus.SENT.value:
        return "отчёт этой недели уже в группе — второй раз не шлём"
    if row.due_at is None or row.tries != tries:
        return "эта попытка уже сделана — повторять нечего"
    return None


async def _deliver(bot: SalesBot, report_id: int, text: str) -> TelegramError | None:
    """В группу продаж. `None` — доставлено, иначе — отказ бота словами."""
    if not cfg.TELEGRAM_GROUP_CHAT_ID:
        logger.warning("продажи: чат группы продаж не задан", extra={"report": report_id})
        return TelegramError(
            "чат группы не задан — заполнить SALES_TELEGRAM_GROUP_CHAT_ID (номер печатает "
            "`outreach sales-telegram-chat-id`)",
            permanent=True,
        )
    try:
        await bot.send(cfg.TELEGRAM_GROUP_CHAT_ID, text)
    except TelegramError as exc:
        logger.warning(
            "продажи: недельный отчёт не доставлен", extra={"report": report_id, "error": str(exc)}
        )
        return exc
    return None


def _alarm(report: weekly.WeeklyReport, spent: int | None, failure: TelegramError) -> str:
    """Тревога эксплуатации: попытки кончились или отказ постоянный; отчёт — на экране."""
    tried = "" if spent is None else f" за {spent} попыток из {MESSAGE_TRIES} — повторов больше нет"
    return (
        f"продажи: недельный отчёт за {report.week.words()} не доставлен в группу продаж{tried} — "
        f"{failure}. Отчёт на экране: {weekly_text.link(report.week)}"
    )


async def run_send(report_id: int, tries: int) -> dict[str, Any]:
    """Одна отправка: своя сессия и свой клиент httpx на задачу."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with _http() as http, factory() as session:
            return await send(session, report_id, tries, SalesBot(http), alert=send_alert)
    finally:
        await engine.dispose()


def send_weekly_report(report_id: int, tries: int) -> dict[str, Any]:
    """Задача очереди: недельный отчёт продаж в группу продаж.

    Недоставка в Telegram — исход задачи (срок следующего повтора или тревога), а не падение.
    Падает задача на базе — её повторит очередь, а причина ляжет рядом с задачей."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(run_send(report_id, tries))
    except Exception as exc:
        job = get_current_job()
        if job is not None:
            remember_job_error(job.id, described(exc))
        raise
