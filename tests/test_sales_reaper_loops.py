"""Циклы продаж в процессе разбора: их неудачи — тревога продаж, а не здоровье процесса.

Три прохода продаж в процессе разбора (повтор передачи лидов, автоотправка, автосборка) падали в
общий цикл (`workers/ticker.every`), и три неудачи подряд любого из них делали «нездоровым» весь
процесс — тот, где живёт сторож тишины. Теперь проход продаж — под своей памятью
(`features/sales/loops.own`): неудача — строкой журнала, третья подряд — тревога продаж словами,
отметка цикла без неудач. Цикл доноров судит здоровье процесса, как раньше.
"""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import sys
from collections import Counter
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import pytest
from backend.config import alerts as alerts_cfg
from backend.config import sales as sales_cfg
from backend.features.sales import loops
from backend.workers import health, reaper, ticker

#: Циклы продаж в процессе разбора — их имена в отметках и тревогах.
SALES_LOOPS = {
    "retry_handoffs": "Повтор передачи лидов продаж",
    "autosend": "Автоотправка писем продаж",
    "autobuild": "Автосборка очереди продаж",
}
SWEEP = "Разбор мёртвых прогонов"
ROOT = Path(__file__).resolve().parents[1]
NAME = "Автосборка очереди продаж"


@pytest.fixture
def told(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Тревоги в общий канал — списком; бот тревог задан, Telegram тревогу принимает."""
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(loops, "send_alert", send)
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "123:token")
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_CHAT_ID", "-100")
    return said


def _falling(calls: Counter[str], name: str, why: str = "Kommo не ответил") -> loops.Pass:
    async def work() -> None:
        calls[name] += 1
        raise RuntimeError(why)

    return work


async def _passes(loop: loops.Own, count: int) -> None:
    for _ in range(count):
        await loop()


# --- память цикла -------------------------------------------------------------------------------


async def test_third_failure_in_a_row_is_one_sales_alarm_in_words(
    told: list[str], caplog: pytest.LogCaptureFixture
) -> None:
    calls: Counter[str] = Counter()
    loop = loops.own(_falling(calls, "pass"), NAME)

    await _passes(loop, 2)
    assert told == []
    await loop()
    await _passes(loop, 3)

    assert calls["pass"] == 6
    assert told == [
        f"тревога: цикл продаж «{NAME}» падает 3-й проход подряд — Kommo не ответил; трассировка "
        "— в журнале процесса разбора, разбор прогонов и сторож тишины идут"
    ]
    # Каждая неудача — строкой журнала с трассировкой: тревога одна, журнал — о каждой.
    failed = [r for r in caplog.records if r.name == loops.logger.name]
    assert len(failed) == 6
    assert all(r.levelno == logging.ERROR and r.exc_info for r in failed)


async def test_first_pass_after_the_alarm_says_it_is_over_once(told: list[str]) -> None:
    calls: Counter[str] = Counter()
    falls = True

    async def work() -> None:
        calls["pass"] += 1
        if falls:
            raise RuntimeError("Kommo не ответил")

    loop = loops.own(work, NAME)
    await _passes(loop, 3)
    falls = False
    await _passes(loop, 2)

    assert told[1:] == [f"прошло: цикл продаж «{NAME}» снова идёт"]
    assert (loop.failures, loop.told) == (0, False)


async def test_a_good_pass_resets_the_count_and_says_nothing(told: list[str]) -> None:
    """Две неудачи, удачный проход, две неудачи — не «четыре подряд»: тревоги нет."""
    falls = [True, True, False, True, True]

    async def work() -> None:
        if falls.pop(0):
            raise RuntimeError("сбой")

    loop = loops.own(work, NAME)
    await _passes(loop, 5)

    assert told == []
    assert loop.failures == 2


async def test_alarm_telegram_did_not_take_is_told_by_the_next_failing_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Бот тревог задан, а Telegram тревогу не принял — её говорит следующий проход; принял —
    больше не повторяет. С «прошло» — так же."""
    answers = [False, True, False, True]
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return answers.pop(0)

    monkeypatch.setattr(loops, "send_alert", send)
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "123:token")
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_CHAT_ID", "-100")
    falls = True

    async def work() -> None:
        if falls:
            raise RuntimeError("сбой")

    loop = loops.own(work, NAME)
    await _passes(loop, 5)
    falls = False
    await _passes(loop, 3)

    assert [text.split(":")[0] for text in said] == ["тревога", "тревога", "прошло", "прошло"]
    assert "4-й проход подряд" in said[1]


async def test_without_the_alarm_bot_the_alarm_is_a_loud_line_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Бот тревог не задан (так в наборе тестов): тревога — громкой строкой журнала
    (`send_alert`), один раз на серию, а не каждым проходом."""
    loop = loops.own(_falling(Counter(), "pass"), NAME)

    await _passes(loop, 6)

    assert caplog.text.count("ТРЕВОГА НЕ ОТПРАВЛЕНА") == 1
    assert loop.told is True


async def test_a_broken_sales_import_is_a_failure_of_its_loop(
    told: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ленивый импорт модуля продаж в проходе упал — неудача этого цикла, а не процесса."""
    monkeypatch.setitem(sys.modules, "backend.features.sales.handoff_jobs", None)

    loop = loops.own(reaper.retry_handoffs, SALES_LOOPS["retry_handoffs"])
    await _passes(loop, 3)

    assert loop.failures == 3
    assert len(told) == 1
    assert "техническая ошибка (ModuleNotFoundError)" in told[0]


async def test_cancelling_the_process_is_not_swallowed(told: list[str]) -> None:
    """Остановка процесса разбора отменяет проход — отмена выходит из обёртки, а не считается
    неудачей."""
    started = asyncio.Event()

    async def hangs() -> None:
        started.set()
        await asyncio.sleep(60)

    loop = loops.own(hangs, NAME)
    running = asyncio.create_task(loop())
    await started.wait()
    running.cancel()

    with pytest.raises(asyncio.CancelledError):
        await running
    assert (loop.failures, told) == (0, [])


# --- процесс разбора ----------------------------------------------------------------------------


async def _until(condition: Callable[[], bool], *, seconds: float = 10.0) -> None:
    """Ждать условия, пока циклы крутятся; не дождались — громко, а не вечно."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + seconds
    while not condition():
        assert loop.time() < deadline, "циклы разбора не дошли до условия"
        await asyncio.sleep(0.005)


def _beats(directory: Path) -> dict[str, dict[str, Any]]:
    return {
        path.stem: json.loads(path.read_text(encoding="utf-8")) for path in directory.glob("*.json")
    }


async def test_three_sales_failures_leave_the_reaper_healthy_and_donors_still_count(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, told: list[str]
) -> None:
    """Все три прохода продаж и разбор прогонов падают каждым кругом: отметки циклов продаж —
    без неудач, по тревоге продаж на каждый цикл; здоровье процесса красное только от разбора
    прогонов — как раньше. Сторож тишины идёт."""
    calls: Counter[str] = Counter()

    async def watching() -> None:
        calls["watch"] += 1

    monkeypatch.setattr(ticker, "BEATS_DIR", tmp_path)
    monkeypatch.setattr(reaper, "sweep", _falling(calls, "sweep", "база не ответила"))
    monkeypatch.setattr(reaper, "watch", watching)
    for name in SALES_LOOPS:
        monkeypatch.setattr(reaper, name, _falling(calls, name))
    monkeypatch.setattr(reaper, "POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(reaper, "WATCHDOG_INTERVAL_SEC", 0.01)
    for setting in ("HANDOFF_PASS_SEC", "AUTOSEND_PASS_SEC", "AUTOBUILD_PASS_SEC"):
        monkeypatch.setattr(sales_cfg, setting, 0.01)

    running = asyncio.create_task(reaper._loops())
    counted = ("sweep", "watch", *SALES_LOOPS)
    await _until(lambda: min(calls[name] for name in counted) >= 4 or running.done())

    assert not running.done(), "исключение прохода вышло из циклов разбора"
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    beats = _beats(tmp_path)
    assert {name: beats[name]["failures"] for name in SALES_LOOPS.values()} == dict.fromkeys(
        SALES_LOOPS.values(), 0
    )
    assert beats[SWEEP]["failures"] >= 3
    [problem] = health.beat_problems(tmp_path)
    assert problem.startswith(f"{SWEEP}: ")
    assert sorted(text.split("«")[1].split("»")[0] for text in told) == sorted(SALES_LOOPS.values())


async def test_reaper_wraps_exactly_the_three_sales_loops_with_one_memory_each(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Под своей памятью — три прохода продаж; память — одна на цикл, первым проходом (иначе счёт
    неудач начинался бы заново на каждом); разбор прогонов и сторож тишины — как были."""
    started: dict[str, Callable[[], Awaitable[None]]] = {}
    made: list[tuple[object, str]] = []
    ran: Counter[str] = Counter()

    async def every(_interval: float, work: Callable[[], Awaitable[None]], *, name: str) -> None:
        started[name] = work

    def own(work: loops.Pass, name: str) -> loops.Pass:
        made.append((work, name))

        async def owned() -> None:
            ran[name] += 1

        return owned

    monkeypatch.setattr(reaper, "every", every)
    monkeypatch.setattr(loops, "own", own)

    await reaper._loops()
    for name in SALES_LOOPS.values():
        await started[name]()
        await started[name]()

    assert made == [(getattr(reaper, attr), name) for attr, name in SALES_LOOPS.items()]
    assert ran == Counter(dict.fromkeys(SALES_LOOPS.values(), 2))
    assert (started[SWEEP], started["Сторож тишины"]) == (reaper.sweep, reaper.watch)


# --- память циклов продаж — лениво: сломан её модуль — падают циклы продаж, а не процесс --------


def test_reaper_start_does_not_load_the_sales_loop_memory() -> None:
    """Память циклов продаж (`sales/loops.py`) грузит первый проход цикла продаж, а не импорт
    процесса разбора — как модули самих проходов (шапка `workers/reaper.py`)."""
    code = (
        "import sys, backend.workers.reaper; print('backend.features.sales.loops' in sys.modules)"
    )

    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )

    assert done.stdout.strip() == "False", done.stderr


async def test_a_broken_sales_loop_memory_fails_the_sales_loops_and_the_reaper_lives(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Импорт памяти циклов продаж сломан: каждый проход цикла продаж — неудача своего цикла
    (её ловит `every`, отметка цикла считает), разбор прогонов и сторож тишины идут, процесс
    разбора жив. Импорт до `every` вылетел бы из `gather` и уронил бы reaper целиком."""
    calls: Counter[str] = Counter()

    def counting(name: str) -> loops.Pass:
        async def work() -> None:
            calls[name] += 1

        return work

    def failed(name: str) -> int:
        """Неудачи цикла — строками журнала цикла (`ticker.every`): файлы отметок пишутся на
        ходу, читать их до остановки циклов нельзя."""
        return sum(
            1
            for record in caplog.records
            if record.name == ticker.logger.name and record.args and record.args[0] == name
        )

    monkeypatch.setitem(sys.modules, "backend.features.sales.loops", None)
    monkeypatch.setattr(ticker, "BEATS_DIR", tmp_path)
    monkeypatch.setattr(reaper, "sweep", counting("sweep"))
    monkeypatch.setattr(reaper, "watch", counting("watch"))
    for attr in SALES_LOOPS:
        monkeypatch.setattr(reaper, attr, counting(attr))
    monkeypatch.setattr(reaper, "POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(reaper, "WATCHDOG_INTERVAL_SEC", 0.01)
    for setting in ("HANDOFF_PASS_SEC", "AUTOSEND_PASS_SEC", "AUTOBUILD_PASS_SEC"):
        monkeypatch.setattr(sales_cfg, setting, 0.01)

    running = asyncio.create_task(reaper._loops())
    await _until(
        lambda: (
            (
                min(calls["sweep"], calls["watch"]) >= 3
                and min(failed(name) for name in SALES_LOOPS.values()) >= 3
            )
            or running.done()
        )
    )

    assert not running.done(), "сбой импорта памяти циклов продаж вышел из циклов разбора"
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    beats = _beats(tmp_path)
    assert all(beats[name]["failures"] >= 3 for name in SALES_LOOPS.values())
    assert (beats[SWEEP]["failures"], beats["Сторож тишины"]["failures"]) == (0, 0)
    # Без памяти цикла проход продаж не идёт: его неудача — сбой импорта, а не работы.
    assert [calls[attr] for attr in SALES_LOOPS] == [0, 0, 0]
