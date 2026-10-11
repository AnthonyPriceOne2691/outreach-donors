"""Проход автоотправки по расписанию: тумблеры, тревога «через 15 минут» и журнал по смене.

Тревога — подставная (`send_alert` запоминает текст): живого Telegram в тестах нет. Сессия
прохода — тестовая: `_pass` подменён проходом на ней, часы — у теста. Тревога о неизвестном
исходе судится на настоящей базе: письмо «отправляется», письма в окне, свободный кап.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from backend.config import alerts as alerts_cfg
from backend.config import sales as sales_cfg
from backend.features.core.domain import MessageStatus
from backend.features.sales import autobuild, autosend, autosend_jobs
from backend.features.sales.autosend import Pass, Stop
from backend.features.sales.autosend_jobs import ALARM, RESUMED, Watch
from backend.workers import reaper
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_autosend import JANE, OLGA, _letters
from tests.test_sales_send import _transports

ROOT = Path(__file__).resolve().parents[1]
NOW = w.NOW
MINUTE = timedelta(minutes=1)
UNKNOWN = "исход письма №7 неизвестен: оно «отправляется» — выдуманные слова теста"


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Тревоги прохода — списком; Telegram их принимает."""
    said: list[str] = []

    async def alert(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(autosend_jobs, "send_alert", alert)
    return said


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def switched_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)


def _passes(monkeypatch: pytest.MonkeyPatch, work: Callable[[datetime], Awaitable[Pass]]) -> None:
    """Проход `tick` — этой функцией вместо своей сессии и почты из настроек."""
    monkeypatch.setattr(autosend_jobs, "_pass", work)


# --- тумблеры ----------------------------------------------------------------------------------


@pytest.mark.parametrize(("enabled", "autosend_on"), [(True, False), (False, True)])
async def test_switched_off_the_pass_opens_nothing(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, autosend_on: bool
) -> None:
    called: list[datetime] = []

    async def forbidden(now: datetime) -> Pass:
        called.append(now)
        raise AssertionError("проход открыл сессию при выключенном тумблере")

    _passes(monkeypatch, forbidden)
    monkeypatch.setattr(sales_cfg, "ENABLED", enabled)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", autosend_on)

    await autosend_jobs.tick(now=NOW, watch=Watch())

    assert called == []


async def test_switched_on_the_pass_runs_and_its_failure_goes_up_to_the_loop(
    monkeypatch: pytest.MonkeyPatch, switched_on: None, told: list[str]
) -> None:
    """Упавший проход — исключение дальше: цикл считает неудачи подряд (здоровье разбора)."""
    called: list[datetime] = []

    async def failing(now: datetime) -> Pass:
        called.append(now)
        raise RuntimeError("база не ответила")

    _passes(monkeypatch, failing)

    with pytest.raises(RuntimeError, match="база не ответила"):
        await autosend_jobs.tick(now=NOW, watch=Watch())

    assert called == [NOW]


# --- тревога «через 15 минут» ------------------------------------------------------------------


async def test_unknown_outcome_over_15_minutes_alarms_once_and_going_again_alarms_once_more(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    switched_on: None,
    told: list[str],
) -> None:
    letters = await _letters(session, world, JANE, OLGA)
    letters[0].status = MessageStatus.SENDING
    await session.flush()
    source = _transports()

    async def on_test_base(now: datetime) -> Pass:
        return await autosend.run(session, source, now=now)

    _passes(monkeypatch, on_test_base)
    watch = Watch()

    for minutes in (0, 5, 14):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    quiet = list(told)
    for minutes in (15, 16, 40):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    alarmed = list(told)

    letters[0].status = MessageStatus.SENT
    await session.flush()
    for minutes in (41, 42, 90):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)

    assert quiet == []
    [alarm] = alarmed
    assert alarm.startswith(ALARM)
    assert f"исход письма №{letters[0].id} неизвестен" in alarm
    # Пошла снова — вторая тревога, одна; письмо из очереди ушло.
    assert told[1:] == [f"{RESUMED} (стояла: {alarm.removeprefix(ALARM + '. ')})"]
    await session.refresh(letters[1])
    assert letters[1].status is MessageStatus.SENT


async def test_no_alarm_while_nothing_is_due_even_if_the_outcome_is_unknown(
    told: list[str],
) -> None:
    """Писем в окне нет (ночь) — проход ничего не обязан слать: стоять не тревожно."""
    watch = Watch()

    for minutes in range(0, 60, 5):
        await watch.see(Pass(due=False, stop=Stop.UNKNOWN, stopped=UNKNOWN), NOW + MINUTE * minutes)

    assert told == []


async def test_waiting_for_the_box_pause_is_not_an_alarm(told: list[str]) -> None:
    """Ящики на паузе между письмами — проход ждёт по делу, хоть и дольше 15 минут."""
    watch = Watch()

    for minutes in range(0, 60, 5):
        await watch.see(
            Pass(due=True, stop=Stop.RESTING, stopped="ящики на паузе"), NOW + MINUTE * minutes
        )

    assert told == []


async def test_failing_pass_alarms_after_15_minutes_by_the_last_known_due(
    told: list[str],
) -> None:
    watch = Watch()
    boom = RuntimeError("database is down")

    await watch.see(Pass(due=True, stop=Stop.RESTING, stopped="ящики на паузе"), NOW)
    await watch.failed(boom, NOW + MINUTE * 14)
    quiet = list(told)
    await watch.failed(boom, NOW + MINUTE * 15)
    await watch.failed(boom, NOW + MINUTE * 30)

    assert quiet == []
    assert told == [
        f"{ALARM}. проход падает — техническая ошибка (RuntimeError); трассировка в журнале разбора"
    ]


async def test_telegram_not_accepting_the_alarm_is_said_again_on_the_next_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tried: list[str] = []

    async def refused(text: str) -> bool:
        tried.append(text)
        return False

    monkeypatch.setattr(autosend_jobs, "send_alert", refused)
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "made-up-bot-token")
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_CHAT_ID", "-100")
    watch = Watch(idle_since=NOW, due=True)
    stalled = Pass(due=True, stop=Stop.UNKNOWN, stopped=UNKNOWN)

    await watch.see(stalled, NOW + MINUTE * 15)
    await watch.see(stalled, NOW + MINUTE * 16)

    assert len(tried) == 2
    assert watch.told is None


# --- журнал по смене ---------------------------------------------------------------------------


async def test_journal_says_a_stop_on_change_and_a_refusal_once_per_letter(
    caplog: pytest.LogCaptureFixture, told: list[str]
) -> None:
    caplog.set_level(logging.INFO, logger="backend.features.sales.autosend_jobs")
    watch = Watch()
    outside = Pass(stop=Stop.OUTSIDE, stopped="вне окна получателя у всех писем очереди (3)")
    refused = Pass(due=True, refused={11: "стоп-лист"}, stop=Stop.RESTING, stopped="паузы")

    for minutes in range(3):
        await watch.see(outside, NOW + MINUTE * minutes)
    for minutes in range(3, 6):
        await watch.see(refused, NOW + MINUTE * minutes)
    await watch.see(outside, NOW + MINUTE * 6)

    lines = [record.getMessage() for record in caplog.records]
    assert lines == [
        "продажи: автоотправка стоит — вне окна получателя у всех писем очереди (3)",
        "продажи: автоотправка — письмо №11 не ушло: стоп-лист",
        "продажи: автоотправка стоит — вне окна получателя у всех писем очереди (3)",
    ]


# --- проводка процесса разбора ------------------------------------------------------------------


def test_reaper_runs_both_sales_passes_with_their_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[tuple[float, str]] = []

    async def every(interval: float, _work: Any, *, name: str) -> None:
        started.append((interval, name))

    monkeypatch.setattr(reaper, "every", every)
    monkeypatch.setattr(reaper, "check_storage", lambda: None)

    reaper.main()

    assert (sales_cfg.AUTOSEND_PASS_SEC, "Автоотправка писем продаж") in started
    assert (sales_cfg.AUTOBUILD_PASS_SEC, "Автосборка очереди продаж") in started


async def test_reaper_loops_are_the_sales_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    called: list[str] = []

    async def sending() -> None:
        called.append("автоотправка")

    async def building() -> None:
        called.append("автосборка")

    monkeypatch.setattr(autosend_jobs, "tick", sending)
    monkeypatch.setattr(autobuild, "tick", building)

    await reaper.autosend()
    await reaper.autobuild()

    assert called == ["автоотправка", "автосборка"]


def test_reaper_does_not_load_the_sales_passes_at_start() -> None:
    """Модуль продаж — первым проходом цикла: сломанный импорт продаж не мешает разбору подняться."""
    code = (
        "import sys, backend.workers.reaper; "
        "print([name for name in ('backend.features.sales.autosend_jobs', "
        "'backend.features.sales.autobuild') if name in sys.modules])"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert done.stdout.strip() == "[]", done.stderr
