"""Проход автоотправки по расписанию: тумблеры, предохранитель почты, тревога «через 15 минут»
и журнал по смене.

Тревога — подставная (`send_alert` запоминает текст): живого Telegram в тестах нет. Сессия
прохода — тестовая: `_pass` подменён проходом на ней, часы — у теста. Тревога о неизвестном
исходе судится на настоящей базе: письмо «отправляется» дольше `STUCK_AFTER`, письма в окне,
свободный кап. Счёт 15 минут — от первого прохода, который стоит не по делу: пауза ящиков и
чужая передача, застигнутая в пути, минут для тревоги не копят.
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
from backend.features.letters.unknown_outcome import STUCK_AFTER
from backend.features.ops.alarms import marked
from backend.features.sales import autobuild, autosend, autosend_jobs
from backend.features.sales.autosend import Pass, Stop
from backend.features.sales.autosend_jobs import ALARM, RESUMED, STALLED, Watch
from backend.workers import reaper
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_autosend import IVAN, JANE, OLGA, _letters, _sendgrid
from tests.test_sales_send import _transports

ROOT = Path(__file__).resolve().parents[1]
NOW = w.NOW
MINUTE = timedelta(minutes=1)
UNKNOWN = "исход письма №7 неизвестен: оно «отправляется» — выдуманные слова теста"
RESTING = Pass(due=True, stop=Stop.RESTING, stopped="ящики на паузе")
FAILED = "проход падает — техническая ошибка (RuntimeError); трассировка в журнале разбора"
#: Свой список адресов почты продаж — предохранитель не снят.
OWN = "me@ours.example.test"
#: Ещё получатели — чтобы писем в окне хватило на сорок минут пауз одного ящика.
MORE = ("mia@delta.example.test", "leo@epsilon.example.test")


def _stall(alarm: str) -> str:
    """Почему стояла: слова тревоги без её начала и без пометки последней строкой."""
    return alarm.removeprefix(f"{ALARM}. ").removesuffix(f"\n{STALLED.words}")


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
    """Тумблеры включены, предохранитель почты продаж снят — что бы ни стояло в `.env`."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "")


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


async def test_a_set_allowlist_keeps_autosend_off_and_the_journal_says_why_once(
    monkeypatch: pytest.MonkeyPatch,
    switched_on: None,
    told: list[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Список разрешённых адресатов почты продаж не пуст — автоотправка не включается: проход
    не открывает сессии (иначе брал бы каждое письмо окна в «отправляется» и возвращал в
    очередь — две записи на письмо в общую базу каждую минуту). Журнал говорит почему — строкой
    по смене, а не каждую минуту; тревоги нет: так решил администратор. Список пуст — проход
    идёт."""
    caplog.set_level(logging.INFO, logger="backend.features.sales.autosend_jobs")
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", OWN)
    ran: list[datetime] = []

    async def counted(now: datetime) -> Pass:
        ran.append(now)
        return Pass()

    _passes(monkeypatch, counted)
    watch = Watch()

    for minutes in range(3):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    guarded = list(ran)
    monkeypatch.setenv("OUTREACH_SALES_ALLOWED_RECIPIENTS", "")
    await autosend_jobs.tick(now=NOW + MINUTE * 3, watch=watch)

    assert guarded == []
    assert ran == [NOW + MINUTE * 3]
    [line] = caplog.records
    assert line.levelno == logging.WARNING
    assert line.getMessage() == (
        "продажи: автоотправка стоит — список разрешённых адресатов почты продаж не пуст "
        "(OUTREACH_SALES_ALLOWED_RECIPIENTS) — письма уходят только на свои адреса, кнопкой "
        "«Отправить очередь» или по одному"
    )
    assert told == []


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
    letters[0].updated_at = NOW - STUCK_AFTER
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
    assert told[1:] == [f"{RESUMED} (стояла: {_stall(alarm)})"]
    await session.refresh(letters[1])
    assert letters[1].status is MessageStatus.SENT


async def test_platform_refusing_every_letter_alarms_after_15_minutes_and_then_resumes(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    switched_on: None,
    told: list[str],
) -> None:
    """Ключ не принят (401) на каждом проходе: проход берёт одно письмо и стоит не по делу.
    Через 15 минут — тревога словами «платформа отказывает: …», без ключа и адреса; ключ
    починили — письмо ушло, «прошло» одной строкой."""
    letters = await _letters(session, world, JANE, OLGA)
    refusing, asked = _sendgrid(401)
    mail = {"now": refusing}

    async def on_test_base(now: datetime) -> Pass:
        return await autosend.run(session, mail["now"], now=now)

    _passes(monkeypatch, on_test_base)
    watch = Watch()

    for minutes in (0, 5, 10, 14):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)
    quiet = list(told)
    await autosend_jobs.tick(now=NOW + MINUTE * 15, watch=watch)
    alarmed = list(told)
    mail["now"] = _transports()
    await autosend_jobs.tick(now=NOW + MINUTE * 16, watch=watch)

    assert quiet == []
    # По одному письму на проход — не всё окно.
    assert asked == [JANE] * 5
    [alarm] = alarmed
    assert alarm.startswith(f"{ALARM}. платформа отказывает: ключ платформы не принят (401)")
    assert JANE not in alarm
    assert "made-up-sales-key" not in alarm
    assert told[1:] == [f"{RESUMED} (стояла: {_stall(alarm)})"]
    await session.refresh(letters[0])
    assert letters[0].status is MessageStatus.SENT


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
        await watch.see(RESTING, NOW + MINUTE * minutes)

    assert told == []


async def test_a_brief_stall_after_a_box_pause_over_15_minutes_is_no_alarm(
    told: list[str],
) -> None:
    """Пауза ящиков — 20 минут, затем проход застал письмо в блоке «Исход неизвестен» (событие
    платформы решило его минутой позже) и ещё через полчаса один раз упал. Остановка — минута,
    а не двадцать одна: ни тревоги, ни «прошло» в общем канале."""
    watch = Watch()

    for minutes in range(21):
        await watch.see(RESTING, NOW + MINUTE * minutes)
    await watch.see(Pass(due=True, stop=Stop.UNKNOWN, stopped=UNKNOWN), NOW + MINUTE * 21)
    await watch.see(Pass(due=True, sent={7: "box@made-up.example.test"}), NOW + MINUTE * 22)
    for minutes in range(23, 45):
        await watch.see(RESTING, NOW + MINUTE * minutes)
    await watch.failed(RuntimeError("база не ответила"), NOW + MINUTE * 45)
    await watch.see(RESTING, NOW + MINUTE * 46)

    assert told == []


async def test_failing_pass_alarms_15_minutes_after_its_first_failure_by_the_last_known_due(
    told: list[str],
) -> None:
    """Проход падает после паузы ящиков дольше 15 минут: счёт — от первого падения, а не от
    последнего ушедшего письма; «обязан слать» — по последнему удачному проходу. Пошёл —
    «прошло» одной строкой."""
    watch = Watch()
    boom = RuntimeError("database is down")

    await watch.see(RESTING, NOW)
    await watch.see(RESTING, NOW + MINUTE * 20)
    for minutes in range(21, 36):
        await watch.failed(boom, NOW + MINUTE * minutes)
    quiet = list(told)
    await watch.failed(boom, NOW + MINUTE * 36)
    await watch.failed(boom, NOW + MINUTE * 50)
    await watch.see(Pass(due=True, sent={7: "box@made-up.example.test"}), NOW + MINUTE * 51)

    assert quiet == []
    assert told == [marked(f"{ALARM}. {FAILED}", STALLED), f"{RESUMED} (стояла: {FAILED})"]


async def test_another_sending_caught_in_flight_on_every_pass_is_no_stop_and_no_alarm(
    session: AsyncSession,
    world: w.World,
    monkeypatch: pytest.MonkeyPatch,
    switched_on: None,
    told: list[str],
) -> None:
    """Каждый проход застаёт чужую передачу письма продаж — добивки, ответа, кнопки — в пути:
    проход на ней не стоит, шлёт своё по паузам ящика, тревоги нет."""
    letters = await _letters(session, world, JANE, OLGA, IVAN, *MORE)
    letters[0].status = MessageStatus.SENDING
    source = _transports()
    stops: list[Stop | None] = []

    async def with_another_in_flight(now: datetime) -> Pass:
        letters[0].updated_at = now - MINUTE
        await session.flush()
        found = await autosend.run(session, source, now=now)
        stops.append(found.stop)
        return found

    _passes(monkeypatch, with_another_in_flight)
    watch = Watch()

    for minutes in range(0, 40, 2):
        await autosend_jobs.tick(now=NOW + MINUTE * minutes, watch=watch)

    assert Stop.UNKNOWN not in stops
    # Писем в окне хватало на все сорок минут: проход ждал паузы ящика, а не стоял.
    assert Stop.RESTING in stops
    assert told == []


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
    watch = Watch(stalled_since=NOW, due=True)
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
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    monkeypatch.setattr(sales_cfg, "AUTOSEND", True)
    monkeypatch.setattr(sales_cfg, "AUTOBUILD", True)

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


def test_switched_off_sales_passes_load_no_sales_module_into_the_reaper() -> None:
    """Тумблеры выключены — циклы автоотправки и автосборки не грузят в процесс разбора ни модуль
    продаж, ни отправку с почтой, ни сборку с моделью: процесс живёт в тесном пределе памяти, а
    сломанный импорт выключенного прохода ронял бы каждый его цикл. Тумблеры — до импорта, у
    каждого прохода свой: включённый соседний его не включает."""
    code = (
        "import asyncio, sys\n"
        "from backend.config import sales\n"
        "from backend.workers import reaper\n"
        "before = set(sys.modules)\n"
        "switches = ((False, False, False), (False, True, True), (True, False, True))\n"
        "for enabled, own, other in switches:\n"
        "    sales.ENABLED, sales.AUTOSEND, sales.AUTOBUILD = enabled, own, other\n"
        "    asyncio.run(reaper.autosend())\n"
        "    sales.ENABLED, sales.AUTOSEND, sales.AUTOBUILD = enabled, other, own\n"
        "    asyncio.run(reaper.autobuild())\n"
        "print(sorted(name for name in set(sys.modules) - before if name.startswith('backend.')))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert done.stdout.strip() == "[]", done.stderr
