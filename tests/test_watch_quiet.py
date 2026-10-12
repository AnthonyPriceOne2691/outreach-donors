"""Сторож с проходом раз в 2 минуты — не громче прежнего прохода раз в 10 минут (ревью #337).

Проход стал чаще ради срока «тревога не позже 15 минут» (`tests/test_mail_watch_retired.py`, его
тесты не ослаблены). Человеку и журналу от этого громче быть не должно:

- «прошло» — по времени (`reaper.QUIET_SEC`), а не по числу проходов: тревога, пропавшая меньше
  чем на 20 минут, пары «тревога / прошло» не даёт; «прошло» — через 20 минут после прохода,
  который видел её последним, как при двух проходах раз в 10 минут;
- «провайдер не отвечает» снимает только свежий опрос: прежний удачный ответ между опросами —
  не наблюдение, при любых интервалах;
- строка журнала о действующей тревоге — раз в 10 минут, о новой — сразу; «тихо и правильно» —
  тоже раз в 10 минут.

Проходы — настоящий `reaper.watch` под часами теста: тревоги базы и опрос провайдеров
подставные, Telegram — список с минутой прохода.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

import pytest
from backend.config import storage
from backend.features.ops import alarm_feed, silence
from backend.features.ops.alarm_feed import Feed
from backend.features.ops.alarms import Alarm
from backend.workers import reaper
from tests.conftest import TEST_DSN

#: Проход сторожа, секунд.
PASS = reaper.WATCHDOG_INTERVAL_SEC
QUIET = Alarm(code="quiet-box:x", title="Ящик x молчит", detail="ждут его")
OTHER = Alarm(code="quiet-box:y", title="Ящик y молчит", detail="ждут и его")
DOWN = Alarm(code=silence.PROVIDERS_ALARM, title="Провайдер не отвечает", detail="Не отвечает: X")


@dataclass
class Watch:
    """Мир прохода: часы, тревоги базы, провайдеры; что ушло в Telegram и когда их спросили."""

    now: float = 0.0
    db: list[Alarm] = field(default_factory=list)
    down: bool = False
    asked: list[float] = field(default_factory=list)
    said: list[tuple[float, str]] = field(default_factory=list)

    async def until(self, end: float, *, db: Sequence[Alarm] = (), down: bool = False) -> None:
        """Проходы раз в `PASS` от текущей минуты до `end` включительно в одном состоянии."""
        self.db, self.down = list(db), down
        while self.now <= end:
            await reaper.watch()
            self.now += PASS


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> Watch:
    """Свои часы у ленты, опроса провайдеров и журнала сторожа — настройки как у процесса."""
    state = Watch()

    def tick() -> float:
        return state.now

    async def alarms(_session: object, *, stages: object) -> list[Alarm]:
        return list(state.db)

    async def probe() -> Alarm | None:
        state.asked.append(state.now)
        return DOWN if state.down else None

    async def telegram(text: str) -> bool:
        state.said.append((state.now, text))
        return True

    monkeypatch.setattr(storage, "DSN", TEST_DSN)  # сессия прохода не открывает соединения
    monkeypatch.setattr(silence, "alarms", alarms)
    monkeypatch.setattr(reaper, "probe_providers", probe)
    monkeypatch.setattr(alarm_feed, "send_alert", telegram)
    feed = Feed(reaper.FEED.name, quiet_sec=reaper.FEED.quiet_sec, clock=tick)
    monkeypatch.setattr(reaper, "FEED", feed)
    monkeypatch.setattr(reaper, "PROVIDERS", reaper.Providers(clock=tick))
    monkeypatch.setattr(reaper, "JOURNAL", silence.Journal(every=reaper.JOURNAL.every, clock=tick))
    return state


def test_the_watchdog_waits_no_less_than_the_ten_minute_pass_did() -> None:
    """Срок тишины ленты сторожа — не меньше прежних двух проходов раз в 10 минут."""
    assert reaper.FEED.quiet_sec == reaper.QUIET_SEC >= 2 * 600


async def test_an_alarm_gone_for_less_than_twenty_minutes_is_not_over(world: Watch) -> None:
    """Тревога пропала на 18 минут и вернулась (ящик на часовом потолке добивок) — одно
    «тревога»; пропала насовсем — «прошло» через 20 минут после прохода, видевшего её последним.
    Счёт проходов сказал бы «прошло» через 4 минуты и «тревога» снова."""
    await world.until(0, db=[QUIET])
    await world.until(9 * PASS)  # нет девять проходов: 18 минут
    await world.until(10 * PASS, db=[QUIET])  # вернулась
    seen_last = world.now - PASS
    await world.until(seen_last + 2 * reaper.QUIET_SEC)

    assert world.said == [
        (0.0, "тревога: Ящик x молчит. ждут его"),
        (seen_last + reaper.QUIET_SEC, "прошло: Ящик x молчит"),
    ]


async def test_the_providers_alarm_ends_after_two_fresh_good_answers(world: Watch) -> None:
    """Опрос — раз в 10 минут, между опросами — прежний ответ. «Прошло» — на втором свежем
    удачном опросе, как при проходе раз в 10 минут, а не через проход после первого."""
    await world.until(0, down=True)
    await world.until(3 * reaper.PROVIDERS_INTERVAL_SEC)

    assert world.asked == [0.0, 600.0, 1200.0, 1800.0]
    assert world.said == [
        (0.0, "тревога: Провайдер не отвечает. Не отвечает: X"),
        (2 * reaper.PROVIDERS_INTERVAL_SEC, "прошло: Провайдер не отвечает"),
    ]


async def test_a_stale_good_answer_does_not_end_the_providers_alarm(
    world: Watch, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Опрос реже срока тишины — одного удачного ответа, свежего и повторённого из памяти
    проходами после него, мало: «прошло» говорит только второй свежий опрос."""
    monkeypatch.setattr(reaper, "PROVIDERS_INTERVAL_SEC", 2 * reaper.QUIET_SEC)
    interval = reaper.PROVIDERS_INTERVAL_SEC
    await world.until(0, down=True)
    await world.until(2 * interval)

    assert world.asked == [0.0, interval, 2 * interval]
    assert world.said == [
        (0.0, "тревога: Провайдер не отвечает. Не отвечает: X"),
        (2 * interval, "прошло: Провайдер не отвечает"),
    ]


def _lines(caplog: pytest.LogCaptureFixture, level: int, words: str) -> int:
    """Строк журнала сторожа такого уровня с такими словами."""
    return sum(
        1
        for record in caplog.records
        if record.name == silence.__name__
        and record.levelno == level
        and words in record.getMessage()
    )


async def test_the_journal_tells_of_an_active_alarm_every_ten_minutes(
    world: Watch, caplog: pytest.LogCaptureFixture
) -> None:
    """Тревога держится 20 минут — строк о ней три (0, 10, 20 минут), а не по одной на каждый из
    11 проходов; новая — строкой на первом же проходе."""
    with caplog.at_level(logging.INFO, logger=silence.__name__):
        await world.until(PASS, db=[QUIET])
        await world.until(2 * PASS, db=[QUIET, OTHER])
        other_at_once = _lines(caplog, logging.WARNING, OTHER.title)
        await world.until(2 * reaper.JOURNAL_INTERVAL_SEC, db=[QUIET, OTHER])

    assert other_at_once == 1
    assert _lines(caplog, logging.WARNING, QUIET.title) == 3
    assert _lines(caplog, logging.WARNING, OTHER.title) == 2  # 4 и 14 минута


async def test_a_calm_journal_says_so_every_ten_minutes(
    world: Watch, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger=silence.__name__):
        await world.until(2 * reaper.JOURNAL_INTERVAL_SEC)

    assert _lines(caplog, logging.INFO, "тихо и правильно") == 3
