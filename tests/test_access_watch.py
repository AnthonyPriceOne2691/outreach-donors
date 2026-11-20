"""Сторож входов и прав — всплеск неудачных входов и проход (`ops/access_watch.py`), аудит 20.11.2026.

Здесь держится:

- **всплеск:** за окно не меньше порога всего или в одну набранную почту — тревога с числом,
  первой неудачей окна по Москве и самыми частыми почтами; «‹не почта›» порога одной почты не
  держит; записи вне окна и удачные входы не считаются; набранное не почтой (записи до 19.11.2026)
  в Telegram не уходит; пороги — из настроек;
- **проход:** одна «тревога» на всплеск, пока он идёт, — тишина, «прошло» — когда окно прошло без
  него; события доступа — тем же проходом и один раз через перезапуск; Telegram и Redis — после
  закрытия сессии;
- **вызов — как у сторожа тишины:** строки для `workers/reaper.py` работают общим циклом как есть;
- **опечатка в пороге не роняет процесс:** умолчание и громкая строка при старте.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from backend.config import access_alarms, storage
from backend.features.access.administration import create_user
from backend.features.access.login import NOT_AN_EMAIL
from backend.features.access.repository import actor_of
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel
from backend.features.ops import access_mark, access_watch, alarm_feed
from backend.features.ops.access_event_words import ADMIN
from backend.features.ops.access_mark import Mark
from backend.features.ops.access_watch import GUESSING
from backend.features.ops.alarm_feed import Feed
from backend.workers import health, ticker
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN, WATCH_MINUTE
from tests.test_access_watch_events import BOSS, NEW, account, stamp
from tests.test_alarm_concern import _plain
from tests.test_mail_watch_seams import FakeRedis
from tests.test_send_race import committed_sessions

ROOT = Path(__file__).resolve().parents[1]

OP = "op@team.example.test"
WHERE = "Где смотреть: экран «Журнал действий»."

#: Имя цикла в процессе разбора — как в строках для `workers/reaper.py`.
LOOP = "Сторож входов и прав"


async def failed(
    session: AsyncSession,
    email: str,
    *,
    ago: float,
    times: int = 1,
    reason: str = "пара не подошла",
) -> None:
    """Неудачные входы в журнале — `ago` минут до минуты прохода, как их пишет вход."""
    session.add_all(
        AuditLogModel(
            action=AuditAction.LOGIN_FAILED,
            details={"email": email, "reason": reason},
            created_at=WATCH_MINUTE - timedelta(minutes=ago),
        )
        for _ in range(times)
    )
    await session.flush()


def burst_text(detail: str) -> str:
    """«Тревога» всплеска, как её скажет лента: проход — минутой ленты (03:11 МСК)."""
    return f"тревога: Всплеск неудачных входов. С 03:11 МСК.\n{detail}.\n{WHERE}\n{GUESSING.words}"


# --- всплеск ---------------------------------------------------------------------------------


async def test_ten_failures_in_the_window_are_a_burst(session: AsyncSession) -> None:
    await failed(session, NOT_AN_EMAIL, ago=7, times=6)
    await failed(session, BOSS, ago=3, times=3)
    await failed(session, OP, ago=1, reason="учётка отключена")

    alarm = await access_watch.burst(session, WATCH_MINUTE)

    assert alarm is not None
    assert (alarm.code, alarm.title, alarm.screen) == (
        "login-burst",
        "Всплеск неудачных входов",
        "Журнал действий",
    )
    assert alarm.detail == (
        "10 неудачных входов за 10 минут — с 03:04 МСК; "
        f"чаще всего — ‹не почта› (6), {BOSS} (3), {OP} (1)"
    )


async def test_nine_spread_over_emails_are_not(session: AsyncSession) -> None:
    for number in range(9):
        await failed(session, f"user{number}@team.example.test", ago=2)

    assert await access_watch.burst(session, WATCH_MINUTE) is None


async def test_five_into_one_email_are_a_burst(session: AsyncSession) -> None:
    """Подбор пароля одной учётки — ему хватает и меньшего числа."""
    await failed(session, OP, ago=5, times=5)

    alarm = await access_watch.burst(session, WATCH_MINUTE)

    assert alarm is not None
    assert alarm.detail == f"5 неудачных входов за 10 минут — с 03:06 МСК; чаще всего — {OP} (5)"


async def test_not_an_email_is_no_account_to_guess(session: AsyncSession) -> None:
    """«‹не почта›» — не учётка: девять таких — не подбор одной почты (порог всего — десять)."""
    await failed(session, NOT_AN_EMAIL, ago=2, times=9)

    assert await access_watch.burst(session, WATCH_MINUTE) is None


async def test_only_failures_inside_the_window_count(session: AsyncSession) -> None:
    await failed(session, OP, ago=11, times=5)
    await failed(session, BOSS, ago=9, times=4)
    session.add(AuditLogModel(action=AuditAction.LOGIN, target="user:1", created_at=WATCH_MINUTE))
    await session.flush()

    assert await access_watch.burst(session, WATCH_MINUTE) is None
    await failed(session, BOSS, ago=1)
    assert await access_watch.burst(session, WATCH_MINUTE) is not None


async def test_typed_text_that_is_not_an_email_never_reaches_telegram(
    session: AsyncSession,
) -> None:
    """Записи до 19.11.2026 несли набранное как есть: пароль, набранный не в то поле, в тревогу не
    уходит — та же проверка, что у журнала (`attempted_email`)."""
    await failed(session, "P@ssw0rd", ago=4, times=6)
    await failed(session, "летний пароль 2026", ago=3, times=4)

    alarm = await access_watch.burst(session, WATCH_MINUTE)

    assert alarm is not None
    assert alarm.detail.endswith("чаще всего — ‹не почта› (10)")
    assert "ssw0rd" not in alarm.detail
    assert "летний" not in alarm.detail


async def test_thresholds_come_from_the_settings(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(access_alarms, "FAILED_LOGINS", 3)
    monkeypatch.setattr(access_alarms, "PER_EMAIL", 50)
    monkeypatch.setattr(access_alarms, "WINDOW_MIN", 30)
    await failed(session, OP, ago=20)
    await failed(session, BOSS, ago=2, times=2)

    alarm = await access_watch.burst(session, WATCH_MINUTE)

    assert alarm is not None
    assert alarm.detail == (
        f"3 неудачных входа за 30 минут — с 02:51 МСК; чаще всего — {BOSS} (2), {OP} (1)"
    )


def test_the_mark_is_plain_and_says_what_to_do() -> None:
    _plain(GUESSING)
    assert GUESSING.cause


def test_the_feed_is_its_own_and_ends_a_burst_after_a_calm_window() -> None:
    """Своя лента — своё сказанное (`alarm_feed.told_key`); «прошло» — когда целое окно прошло без
    всплеска."""
    assert access_watch.FEED.name == "access"
    assert access_watch.FEED.quiet_sec == access_alarms.WINDOW_MIN * 60
    assert access_watch.FEED.screen == "Журнал действий"


# --- проход ----------------------------------------------------------------------------------


@dataclass
class Clock:
    """Часы прохода: системные (минута слов ленты) и монотонные (срок тишины) — идут вместе."""

    wall: datetime = WATCH_MINUTE
    ticks: float = 0.0

    def advance(self, minutes: float) -> None:
        self.wall += timedelta(minutes=minutes)
        self.ticks += minutes * 60


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """Сторож — с чистого листа (лента и курсор — память процесса), база — тестовая, часы — свои."""
    found = Clock()
    feed = access_watch.FEED
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(alarm_feed, "wall_clock", lambda: found.wall)
    monkeypatch.setattr(
        access_watch,
        "FEED",
        Feed(feed.name, quiet_sec=feed.quiet_sec, clock=lambda: found.ticks, screen=feed.screen),
    )
    monkeypatch.setattr(access_watch, "MARK", Mark())
    return found


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Что сторож сказал в Telegram — лентой и событиями; Telegram принимает всё."""
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(alarm_feed, "send_alert", send)
    monkeypatch.setattr(access_mark, "send_alert", send)
    return said


async def test_a_burst_is_one_alarm_and_one_all_clear(clock: Clock, telegram: list[str]) -> None:
    async with committed_sessions() as factory:
        async with factory() as session:
            await failed(session, OP, ago=2, times=10)
            await session.commit()

        for _ in range(3):  # всплеск идёт — проход за проходом тишина
            await access_watch.watch()
        clock.advance(11)  # окно прошло без новых неудач
        await access_watch.watch()
        clock.advance(1)
        await access_watch.watch()

    assert telegram == [
        burst_text(f"10 неудачных входов за 10 минут — с 03:09 МСК; чаще всего — {OP} (10)"),
        "прошло: Всплеск неудачных входов. 03:11–03:22 МСК, 11 минут.",
    ]


async def test_events_ride_the_same_pass_and_survive_a_restart(
    clock: Clock, telegram: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    redis = FakeRedis()
    monkeypatch.setattr(alarm_feed, "connection", lambda: redis)
    async with committed_sessions() as factory:
        async with factory() as session:
            boss = await account(session, BOSS, UserRole.ADMIN)
            await create_user(session, email=NEW, role=UserRole.ADMIN, author=actor_of(boss))
            await stamp(session, WATCH_MINUTE - timedelta(minutes=1))
            await failed(session, OP, ago=1, times=5)
            await session.commit()

        await access_watch.watch()
        monkeypatch.setattr(access_watch, "MARK", Mark())  # перезапуск процесса разбора
        await access_watch.watch()

    assert telegram == [
        burst_text(f"5 неудачных входов за 10 минут — с 03:10 МСК; чаще всего — {OP} (5)"),
        f"тревога: Новый админ — {NEW}. В 03:10 МСК.\n"
        f"Заведена с ролью «Админ»; кто — {BOSS}.\n{WHERE}\n{ADMIN.words}",
    ]


async def test_the_pass_talks_to_telegram_outside_its_transaction(
    clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сессия — только на чтение: при открытой транзакции соединение висело бы «idle in
    transaction» на время ответа Telegram."""
    opened: list[AsyncSession] = []
    seen: list[bool] = []
    real = access_watch.burst

    async def spied(session: AsyncSession, at: datetime) -> object:
        opened.append(session)
        return await real(session, at)

    async def send(_text: str) -> bool:
        seen.append(opened[0].in_transaction())
        return True

    monkeypatch.setattr(access_watch, "burst", spied)
    monkeypatch.setattr(alarm_feed, "send_alert", send)
    monkeypatch.setattr(access_mark, "send_alert", send)
    async with committed_sessions() as factory:
        async with factory() as session:
            boss = await account(session, BOSS, UserRole.ADMIN)
            await create_user(session, email=NEW, role=UserRole.ADMIN, author=actor_of(boss))
            await stamp(session, WATCH_MINUTE - timedelta(minutes=1))
            await failed(session, OP, ago=1, times=10)
            await session.commit()

        await access_watch.watch()

    assert seen == [False, False]


# --- вызов из процесса разбора -----------------------------------------------------------------


def test_the_pass_is_called_like_the_silence_watch() -> None:
    """Как `reaper.watch`: корутина без доводов — тип прохода общего цикла (`ticker.Pass`)."""
    assert inspect.iscoroutinefunction(access_watch.watch)
    assert inspect.signature(access_watch.watch).parameters == {}
    assert access_watch.INTERVAL_SEC == 60.0


async def test_the_lines_for_the_reaper_work_as_given(
    clock: Clock, telegram: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Строки, которые добавит в `workers/reaper.py` его команда: `every(access_watch.INTERVAL_SEC,
    access_watch.watch, name="Сторож входов и прав")` — проход идёт общим циклом, отметка о жизни
    без неудач, здоровье процесса — без жалоб."""
    monkeypatch.setattr(ticker, "BEATS_DIR", tmp_path)
    beats: list[int] = []
    passed = asyncio.Event()
    real = ticker.beat

    def beat(name: str, *, allowed: float, failures: int) -> None:
        real(name, allowed=allowed, failures=failures)
        beats.append(failures)
        if len(beats) == 2:  # отметка после прохода
            passed.set()

    monkeypatch.setattr(ticker, "beat", beat)
    async with committed_sessions() as factory:
        async with factory() as session:
            await failed(session, OP, ago=2, times=10)
            await session.commit()

        task = asyncio.create_task(
            ticker.every(access_watch.INTERVAL_SEC, access_watch.watch, name=LOOP)
        )
        await asyncio.wait_for(passed.wait(), timeout=30)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert beats == [0, 0]
    assert [text.splitlines()[0] for text in telegram] == [
        "тревога: Всплеск неудачных входов. С 03:11 МСК."
    ]
    assert health.beat_problems(tmp_path) == []


# --- настройки -------------------------------------------------------------------------------

PROBE = (
    "from backend.config import access_alarms as c; "
    "print(c.FAILED_LOGINS, c.PER_EMAIL, c.WINDOW_MIN)"
)


def _started(settings: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Импорт настроек в подпроцессе: в этом процессе они уже прочитаны."""
    return subprocess.run(
        [sys.executable, "-c", PROBE],
        cwd=ROOT,
        env={**os.environ, **settings, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def test_the_defaults_are_ten_five_and_ten_minutes() -> None:
    assert (
        access_alarms.DEFAULT_FAILED_LOGINS,
        access_alarms.DEFAULT_PER_EMAIL,
        access_alarms.DEFAULT_WINDOW_MIN,
    ) == (10, 5, 10)


def test_good_settings_are_read() -> None:
    done = _started(
        {
            "ACCESS_ALARM_FAILED_LOGINS": "12",
            "ACCESS_ALARM_FAILED_LOGINS_PER_EMAIL": "4",
            "ACCESS_ALARM_WINDOW_MIN": "15",
        }
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["12", "4", "15"]
    assert "НАСТРОЙКА НЕ ПРОЧИТАНА" not in done.stderr


def test_a_typo_in_a_threshold_takes_the_default_and_says_so() -> None:
    """Опечатка не роняет процесс разбора, где живёт и сторож тишины: умолчание и громкая строка."""
    done = _started(
        {
            "ACCESS_ALARM_FAILED_LOGINS": "-3",
            "ACCESS_ALARM_FAILED_LOGINS_PER_EMAIL": "7",
            "ACCESS_ALARM_WINDOW_MIN": "десять",
        }
    )

    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == ["10", "7", "10"]
    assert "НАСТРОЙКА НЕ ПРОЧИТАНА — ACCESS_ALARM_WINDOW_MIN: в ней «десять»" in done.stderr
    assert "НАСТРОЙКА НЕ ПРОЧИТАНА — ACCESS_ALARM_FAILED_LOGINS: в ней «-3»" in done.stderr
