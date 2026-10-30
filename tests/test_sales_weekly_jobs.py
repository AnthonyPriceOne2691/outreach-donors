"""Недельный отчёт продаж — в группу продаж по расписанию, один раз за неделю (срез 6.3, A3).

Проход (`weekly_jobs.due`) заводит строку недели, когда пришло время `SALES_WEEKLY_REPORT_AT`
по Москве, и ставит задачу; задача (`weekly_jobs.send`) собирает отчёт функциями экранов и
шлёт его ботом продаж. Повтор прохода и задачи второго сообщения не шлёт. Telegram
недоступен — серия повторов сообщений бота продаж, затем тревога (A3); отчёт — на экране
(`test_sales_weekly_api.py`). Бот — поверх подставного Bot API, паузы бота не спят, время —
выдуманное, по Москве (`test_sales_weekly_rows.py`).
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from datetime import time, timedelta
from typing import Any

import httpx
import pytest
import rq
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from backend.config import sales as cfg
from backend.features.sales import handoff_jobs, telegram, weekly_jobs, weekly_text
from backend.features.sales.telegram import SalesBot
from backend.features.sales.telegram_series import MESSAGE_TRIES
from backend.features.sales.weekly_models import SalesWeeklyReportModel, WeeklyStatus
from backend.shared import queue as shared_queue
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import func, select, text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.migration_helpers import load_migration
from tests.test_sales_draft_notify import APP, GROUP, TOKEN, BotApi, _Closable, down, ok
from tests.test_sales_model import ROOT
from tests.test_sales_weekly_rows import WEEK, msk

#: Время отчёта на выдуманной неделе: понедельник 8 марта, 09:00 по Москве.
AT = msk(8, 9, 0)
REVISION = "b2716edd5d46_sales_weekly_reports.py"
MINUTE = timedelta(minutes=1)


class Alarms(list[str]):
    """Тревоги эксплуатации — в список: бот тревог в тестах не ходит в сеть."""

    async def __call__(self, said: str) -> bool:
        self.append(said)
        return True


@pytest.fixture(autouse=True)
def switched_on(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Продажи включены, отчёт — по понедельникам в 09:00, бот и группа заданы, паузы бота
    не спят."""
    monkeypatch.setattr(cfg, "ENABLED", True)
    monkeypatch.setattr(cfg, "WEEKLY_REPORT_AT", (0, time(9, 0)))
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_CHAT_ID", GROUP)
    monkeypatch.setattr(cfg, "APP_URL", APP)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(telegram, "_sleep", sleep)
    return slept


async def _rows(session: AsyncSession) -> list[SalesWeeklyReportModel]:
    found = await session.scalars(
        select(SalesWeeklyReportModel)
        .order_by(SalesWeeklyReportModel.id)
        .execution_options(populate_existing=True)
    )
    return list(found.all())


async def _send(
    session: AsyncSession, api: BotApi, alarms: Alarms, report: int, tries: int, at: Any
) -> dict[str, Any]:
    return await weekly_jobs.send(
        session, report, tries, SalesBot(api.client()), alert=alarms, now=lambda: at
    )


# --- по умолчанию отчёт в группу не уходит -------------------------------------------------------


def _default_time(monkeypatch: pytest.MonkeyPatch) -> tuple[int, time] | None:
    """Время отчёта по умолчанию — полем настроек, без окружения и `.env` разработчика."""
    monkeypatch.delenv("SALES_WEEKLY_REPORT_AT", raising=False)
    return cfg._weekly_at(cfg._Sales(_env_file=None).weekly_report_at)


def test_report_time_is_empty_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Всё, что продажи шлют наружу без человека, по умолчанию выключено: время отчёта — пусто."""
    assert _default_time(monkeypatch) is None


async def test_by_default_the_pass_sends_nothing_and_opens_no_week(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "WEEKLY_REPORT_AT", _default_time(monkeypatch))

    taken = await weekly_jobs.due(session, now=AT + timedelta(days=1))

    assert (taken, await _rows(session)) == ([], [])


# --- расписание и «один раз» ----------------------------------------------------------------------


def test_report_minute_is_the_setting_in_moscow_time(monkeypatch: pytest.MonkeyPatch) -> None:
    assert weekly_jobs.scheduled(WEEK) == AT
    monkeypatch.setattr(cfg, "WEEKLY_REPORT_AT", (2, time(10, 30)))
    assert weekly_jobs.scheduled(WEEK) == msk(10, 10, 30)
    monkeypatch.setattr(cfg, "WEEKLY_REPORT_AT", None)
    assert weekly_jobs.scheduled(WEEK) is None


async def test_pass_waits_for_the_report_minute_then_takes_the_week_once(
    session: AsyncSession,
) -> None:
    early = await weekly_jobs.due(session, now=AT - MINUTE)
    assert (early, await _rows(session)) == ([], [])

    taken = await weekly_jobs.due(session, now=AT)
    again = await weekly_jobs.due(session, now=AT + 5 * MINUTE)
    lost = await weekly_jobs.due(session, now=AT + 16 * MINUTE)

    [row] = await _rows(session)
    assert taken == [(row.id, 0)]
    assert again == [], "взятую строку следующий круг не берёт, пока задача не отработала"
    assert lost == [(row.id, 0)], "потерянную задачу проход ставит снова"
    assert (row.week_start, row.status) == (WEEK.start, WeeklyStatus.PENDING.value)
    assert row.due_at == AT + 31 * MINUTE


async def test_report_goes_once_a_second_pass_and_job_send_nothing(
    session: AsyncSession,
) -> None:
    # повтор прохода — без второго сообщения
    api, alarms = BotApi(ok()), Alarms()
    [(report, tries)] = await weekly_jobs.due(session, now=AT)

    done = await _send(session, api, alarms, report, tries, AT + MINUTE)
    later = await weekly_jobs.due(session, now=AT + 30 * MINUTE)
    twice = await _send(session, api, alarms, report, tries, AT + 31 * MINUTE)

    assert done == {"report": report, "status": "sent"}
    assert later == []
    assert twice == {
        "report": report,
        "skipped": "отчёт этой недели уже в группе — второй раз не шлём",
    }
    [sent] = api.seen
    assert sent["chat_id"] == GROUP
    assert sent["text"].startswith("Продажи — недельный отчёт за 01.03–07.03 (пн–вс, Москва)")
    assert sent["text"].endswith(f"{APP}/sales?tab=report&week=2027-03-01")
    [row] = await _rows(session)
    assert (row.status, row.sent_at, row.text, row.tries, row.due_at) == (
        WeeklyStatus.SENT.value,
        AT + MINUTE,
        sent["text"],
        0,
        None,
    )
    assert alarms == []


async def test_next_week_is_its_own_row_and_the_sent_one_stays(session: AsyncSession) -> None:
    api, alarms = BotApi(ok()), Alarms()
    [(first, _)] = await weekly_jobs.due(session, now=AT)
    await _send(session, api, alarms, first, 0, AT)

    following = await weekly_jobs.due(session, now=AT + timedelta(days=7))

    rows = await _rows(session)
    assert [row.week_start for row in rows] == [WEEK.start, WEEK.next.start]
    assert following == [(rows[1].id, 0)]
    assert len(api.seen) == 1


async def test_late_pass_sends_the_report_of_the_same_week(session: AsyncSession) -> None:
    """Процесс лежал в понедельник утром — отчёт уходит позже, но той же недели."""
    taken = await weekly_jobs.due(session, now=msk(10, 15, 40))

    [row] = await _rows(session)
    assert (taken, row.week_start) == ([(row.id, 0)], WEEK.start)


# --- A3: Telegram недоступен — повтор, затем тревога ---------------------------------------------


async def test_a3_telegram_down_is_retried_by_the_pass_then_one_alarm(
    session: AsyncSession, switched_on: list[float]
) -> None:
    # A3: Telegram недоступен — повтор и тревога
    api, alarms = BotApi(down()), Alarms()
    [(report, tries)] = await weekly_jobs.due(session, now=AT)
    moment, jobs = AT, 0
    while True:
        outcome = await _send(session, api, alarms, report, tries, moment)
        jobs += 1
        [row] = await _rows(session)
        if row.due_at is None:
            break
        assert outcome["status"] == "undelivered"
        assert "повтор не раньше" in (row.error or "")
        assert await weekly_jobs.due(session, now=row.due_at - MINUTE) == []
        moment = row.due_at
        [(report, tries)] = await weekly_jobs.due(session, now=moment)

    assert jobs == MESSAGE_TRIES
    assert len(api.seen) == MESSAGE_TRIES * telegram.ATTEMPTS
    assert (row.status, row.tries) == (WeeklyStatus.UNDELIVERED.value, 0)
    [alarm] = alarms
    assert f"не доставлен в группу продаж за {MESSAGE_TRIES} попыток" in alarm
    assert f"Отчёт на экране: {APP}/sales?tab=report&week=2027-03-01" in alarm
    assert alarm.endswith(weekly_jobs.ON_SCREEN.words)
    assert await weekly_jobs.due(session, now=moment + timedelta(days=1)) == []
    assert switched_on, "бот пробовал с паузами — они не спят"


async def test_pause_of_429_is_kept_by_the_next_try(session: AsyncSession) -> None:
    flood = httpx.Response(
        429, json={"ok": False, "description": "Too Many", "parameters": {"retry_after": 3600}}
    )
    [(report, tries)] = await weekly_jobs.due(session, now=AT)

    await _send(session, BotApi(flood), Alarms(), report, tries, AT)

    [row] = await _rows(session)
    assert row.due_at == AT + timedelta(seconds=3600)


async def test_permanent_refusal_alarms_at_once_without_retry(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_CHAT_ID", "")
    api, alarms = BotApi(ok()), Alarms()
    [(report, tries)] = await weekly_jobs.due(session, now=AT)

    await _send(session, api, alarms, report, tries, AT)

    [row] = await _rows(session)
    assert (row.status, row.due_at, api.seen) == (WeeklyStatus.UNDELIVERED.value, None, [])
    assert "SALES_TELEGRAM_GROUP_CHAT_ID" in (row.error or "")
    [alarm] = alarms
    assert "недельный отчёт за 01.03–07.03 не доставлен в группу продаж — чат группы" in alarm
    assert alarm.endswith(weekly_jobs.ON_SCREEN.words)


async def test_stale_retry_is_not_sent(session: AsyncSession) -> None:
    api = BotApi(down())
    [(report, _)] = await weekly_jobs.due(session, now=AT)
    await _send(session, api, Alarms(), report, 0, AT)

    stale = await _send(session, api, Alarms(), report, 0, AT + MINUTE)

    assert stale == {"report": report, "skipped": "эта попытка уже сделана — повторять нечего"}
    assert len(api.seen) == telegram.ATTEMPTS


# --- выключатель -------------------------------------------------------------------------------------


@pytest.mark.parametrize(("setting", "value"), [("ENABLED", False), ("WEEKLY_REPORT_AT", None)])
async def test_switched_off_takes_nothing_and_sends_nothing(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, setting: str, value: object
) -> None:
    [(report, tries)] = await weekly_jobs.due(session, now=AT)
    monkeypatch.setattr(cfg, setting, value)
    api = BotApi(ok())

    later = await weekly_jobs.due(session, now=AT + timedelta(days=1))
    outcome = await _send(session, api, Alarms(), report, tries, AT)

    assert (later, api.seen) == ([], [])
    assert outcome == {"report": report, "skipped": weekly_jobs.SWITCHED_OFF}
    [row] = await _rows(session)
    assert row.status == WeeklyStatus.PENDING.value


# --- проход продаж процесса разбора и очередь ------------------------------------------------------


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> None:
    """Проход продаж работает в транзакции теста; время отчёта — полночь понедельника: при
    любых настоящих часах прошлая неделя уже ждёт отчёта."""
    monkeypatch.setattr(handoff_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        handoff_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    monkeypatch.setattr(cfg, "WEEKLY_REPORT_AT", (0, time(0, 0)))


@pytest.mark.usefixtures("wired")
async def test_sales_pass_of_the_reaper_queues_the_weekly_report(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued: list[tuple[int, int]] = []
    monkeypatch.setattr(weekly_jobs, "queue_send", lambda *args: queued.append(args))

    await handoff_jobs.retry_pass()
    await handoff_jobs.retry_pass()

    [row] = await _rows(session)
    assert queued == [(row.id, 0)]


@pytest.mark.usefixtures("wired")
async def test_by_default_the_sales_pass_of_the_reaper_queues_no_report(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg, "WEEKLY_REPORT_AT", _default_time(monkeypatch))
    queued: list[tuple[int, int]] = []
    monkeypatch.setattr(weekly_jobs, "queue_send", lambda *args: queued.append(args))

    await handoff_jobs.retry_pass()

    assert (queued, await _rows(session)) == ([], [])


@pytest.mark.usefixtures("wired")
async def test_sales_pass_survives_a_queue_that_does_not_answer(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def refuse(*_args: int) -> None:
        raise RedisConnectionError("очередь не отвечает")

    monkeypatch.setattr(weekly_jobs, "queue_send", refuse)

    with caplog.at_level(logging.ERROR, logger=handoff_jobs.__name__):
        await handoff_jobs.retry_pass()

    assert "продажи: недельный отчёт не поставлен — очередь недоступна" in caplog.text
    [row] = await _rows(session)
    assert row.due_at is not None, "срок сдвинут — строку возьмёт следующий круг"


def test_report_goes_to_the_sales_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Очередь продаж (`sales`, воркер `worker-sales`). Redis не трогается — постановка
    подменена у самой очереди rq."""
    placed: list[tuple[str, str, tuple[Any, ...]]] = []

    def enqueue(self: rq.Queue, path: str, *args: Any, **_options: Any) -> None:
        placed.append((self.name, path, args))

    monkeypatch.setattr(rq.Queue, "enqueue", enqueue)

    weekly_jobs.queue_send(41, 2)

    assert placed == [(shared_queue.SALES_QUEUE_NAME, weekly_jobs.SEND_JOB, (41, 2))]


def test_job_remembers_why_it_failed_and_lets_the_queue_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(weekly_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(weekly_jobs, "check_storage", lambda: None)
    remembered: list[tuple[str, str]] = []

    class Job:
        id = "sales-weekly-test"

    async def broken(_report: int, _tries: int) -> dict[str, Any]:
        raise RuntimeError("база отвалилась")

    monkeypatch.setattr(weekly_jobs, "run_send", broken)
    monkeypatch.setattr(weekly_jobs, "get_current_job", Job)
    monkeypatch.setattr(
        weekly_jobs, "remember_job_error", lambda job_id, said: remembered.append((job_id, said))
    )

    with pytest.raises(RuntimeError, match="база отвалилась"):
        weekly_jobs.send_weekly_report(5, 0)

    assert remembered == [("sales-weekly-test", "RuntimeError: база отвалилась")]


def test_job_runs_the_core_in_its_own_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(weekly_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(weekly_jobs, "check_storage", lambda: None)

    async def done(report: int, tries: int) -> dict[str, Any]:
        return {"report": report, "tries": tries}

    monkeypatch.setattr(weekly_jobs, "run_send", done)

    assert weekly_jobs.send_weekly_report(31, 1) == {"report": 31, "tries": 1}


async def test_job_sends_with_its_own_session_and_client(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    api = BotApi(ok())
    monkeypatch.setattr(weekly_jobs, "_http", api.client)
    monkeypatch.setattr(weekly_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        weekly_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    [(report, tries)] = await weekly_jobs.due(session, now=AT)

    outcome = await weekly_jobs.run_send(report, tries)

    assert outcome == {"report": report, "status": "sent"}
    [sent] = api.seen
    assert json.dumps(sent, ensure_ascii=False).count("недельный отчёт") == 1


def test_weekly_job_imports_first_in_a_clean_process() -> None:
    """Путь задачи строкой — воркер импортирует его сам, первым."""
    module, _, name = weekly_jobs.SEND_JOB.rpartition(".")
    done = subprocess.run(
        [sys.executable, "-c", f"import {module}; assert callable({module}.{name})"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr


def test_link_without_the_service_address_names_the_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cfg, "APP_URL", "")

    assert weekly_text.link(WEEK) == "Продажи → Отчёт (SALES_APP_URL не задан)"


# --- настройка времени отчёта ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "parsed"),
    [
        ("MON 09:00", (0, time(9, 0))),
        ("fri 18:30", (4, time(18, 30))),
        ("  SUN 07:05 ", (6, time(7, 5))),
        ("", None),
    ],
)
def test_report_time_setting_is_read(raw: str, parsed: tuple[int, time] | None) -> None:
    assert cfg._weekly_at(raw) == parsed


@pytest.mark.parametrize("raw", ["MONDAY 09:00", "MON", "MON 9", "ПН 09:00", "MON 09:00+03:00"])
def test_report_time_typo_is_refused_in_words(raw: str) -> None:
    with pytest.raises(ValueError, match=r"SALES_WEEKLY_REPORT_AT .* например «MON 09:00»"):
        cfg._weekly_at(raw)


# --- ревизия журнала ---------------------------------------------------------------------------------


def _table_down_and_up(connection: Connection) -> tuple[bool, bool]:
    """Ревизия журнала вниз и вверх на соединении теста: подъём сьюта идёт подпроцессом."""
    migration = load_migration(REVISION)
    present = "SELECT to_regclass('sales_weekly_reports') IS NOT NULL"
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        gone = bool(connection.execute(text(present)).scalar())
        migration.upgrade()
    return gone, bool(connection.execute(text(present)).scalar())


async def test_weekly_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    assert await connection.run_sync(_table_down_and_up) == (False, True)


async def test_one_row_per_week_is_held_by_the_base(session: AsyncSession) -> None:
    await weekly_jobs.due(session, now=AT)
    await weekly_jobs.due(session, now=AT + 20 * MINUTE)
    await weekly_jobs.due(session, now=AT + 40 * MINUTE)

    assert await session.scalar(select(func.count()).select_from(SalesWeeklyReportModel)) == 1
