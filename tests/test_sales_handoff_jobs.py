"""Задача передачи лида и проход повторов — срез 5.3, T4: проводка, а не правила.

Правила передачи — в `tests/test_sales_handoff.py`; здесь — что задача собирает
из настроек (Kommo подключён или нет, негодная настройка), что делает с отказами
(постоянный — итог, временный — повтор очереди) и что проход ставит в очередь.
Задача работает в транзакции теста; сеть — подставной транспорт; очереди нет.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.config import sales as cfg
from backend.features.sales import handoff, handoff_jobs, telegram
from backend.features.sales.handoff import HandoffBusyError
from backend.features.sales.kommo import KommoLive, KommoRefusedError, NewLead
from backend.features.sales.models import HandoffKommo, SalesHandoffModel
from backend.workers import health, reaper, ticker
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_sales_handoff_rows import sales_dialog
from tests.test_sales_switch import sales_switched_on
from tests.test_sales_telegram import PERSONAL, TOKEN, Recorder, ok

__all__ = ["sales_switched_on"]  # продажи включены: путь вида ответа и передачи лида

ROOT = Path(__file__).resolve().parents[1]

APP = "https://app.example.test"


class _Closable:
    async def dispose(self) -> None:
        return None


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> Recorder:
    """Задача и проход работают в транзакции теста; Telegram — подставной."""
    api = Recorder(ok())
    monkeypatch.setattr(handoff_jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        handoff_jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    monkeypatch.setattr(handoff_jobs, "check_storage", lambda: None)
    monkeypatch.setattr(handoff_jobs, "_http", api.client)
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr(cfg, "TELEGRAM_CHAT_ID", PERSONAL)
    monkeypatch.setattr(cfg, "TELEGRAM_GROUP_COPY", False)
    monkeypatch.setattr(cfg, "APP_URL", APP)
    return api


def _live(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    settings = {
        "KOMMO_PROVIDER": "live",
        "KOMMO_SUBDOMAIN": "acme-test",
        "KOMMO_TOKEN": "k" * 40,
        "KOMMO_PIPELINE_ID": "6170293",
        "KOMMO_STATUS_ID": "58301947",
        "KOMMO_RESPONSIBLE_USER_ID": "9124803",
    }
    for name, value in (settings | values).items():
        monkeypatch.setattr(cfg, name, value)


# --- какой Kommo у задачи ---------------------------------------------------------------


async def test_fixture_kommo_means_not_connected() -> None:
    async with httpx.AsyncClient() as http:
        assert handoff_jobs.connected_kommo(http) is None


async def test_live_kommo_is_the_live_client(monkeypatch: pytest.MonkeyPatch) -> None:
    _live(monkeypatch)
    async with httpx.AsyncClient() as http:
        assert isinstance(handoff_jobs.connected_kommo(http), KommoLive)


async def test_broken_live_settings_refuse_every_write_with_their_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _live(monkeypatch, KOMMO_TOKEN="")
    async with httpx.AsyncClient() as http:
        kommo = handoff_jobs.connected_kommo(http)
    assert kommo is not None
    with pytest.raises(KommoRefusedError, match="SALES_KOMMO_TOKEN"):
        await kommo.find_contact("ivan@acme.example.test")
    with pytest.raises(KommoRefusedError, match="SALES_KOMMO_TOKEN"):
        await kommo.add_note(9301, "текст")
    with pytest.raises(KommoRefusedError, match="SALES_KOMMO_TOKEN"):
        await kommo.create_complex_lead(NewLead("ivan@acme.example.test", "acme.example.test", "h"))
    assert kommo.lead_url(9301) == ""


# --- задача -------------------------------------------------------------------------------


async def test_job_hands_off_with_dialog_link_before_kommo_is_connected(
    session: AsyncSession, wired: Recorder
) -> None:
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)

    outcome = await handoff_jobs.run_hand_off(row.id)

    assert outcome == {
        "handoff": row.id,
        "kommo": "off",
        "telegram": "sent",
        "kommo_lead_id": None,
    }
    [request] = wired.seen
    assert json.loads(request.content)["text"].endswith(f"{APP}/threads/{dialog.thread.id}")


async def test_job_with_broken_live_settings_fails_loudly_and_still_tells(
    session: AsyncSession, wired: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    _live(monkeypatch, KOMMO_PIPELINE_ID="")
    told: list[str] = []

    async def alert(text: str) -> bool:
        told.append(text)
        return True

    monkeypatch.setattr(handoff_jobs, "send_alert", alert)
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)

    outcome = await handoff_jobs.run_hand_off(row.id)

    assert outcome["kommo"] == HandoffKommo.FAILED.value
    assert outcome["telegram"] == "sent"
    [alert_text] = told
    assert "SALES_KOMMO_PIPELINE_ID" in alert_text


def test_job_for_missing_handoff_is_a_settled_refusal(
    monkeypatch: pytest.MonkeyPatch, wired: Recorder
) -> None:
    monkeypatch.setattr(handoff_jobs, "setup_logging", lambda: None)

    async def refuse(_handoff_id: int) -> dict[str, object]:
        raise handoff.HandoffError("передачи №987653 нет — делать нечего")

    monkeypatch.setattr(handoff_jobs, "run_hand_off", refuse)

    outcome = handoff_jobs.hand_off_lead(987653)

    assert outcome == {
        "error": "HandoffError: передачи №987653 нет — делать нечего",
        "permanent": True,
    }


def test_busy_handoff_goes_back_to_the_queue_with_its_reason(
    monkeypatch: pytest.MonkeyPatch, wired: Recorder
) -> None:
    monkeypatch.setattr(handoff_jobs, "setup_logging", lambda: None)
    remembered: list[tuple[str, str]] = []

    class Job:
        id = "sales-handoff-test"

    async def busy(_handoff_id: int) -> dict[str, object]:
        raise HandoffBusyError("передачу №5 держит другая задача — повторим позже")

    monkeypatch.setattr(handoff_jobs, "run_hand_off", busy)
    monkeypatch.setattr(handoff_jobs, "get_current_job", Job)
    monkeypatch.setattr(
        handoff_jobs, "remember_job_error", lambda job_id, text: remembered.append((job_id, text))
    )

    with pytest.raises(HandoffBusyError):
        handoff_jobs.hand_off_lead(5)

    assert remembered == [
        (
            "sales-handoff-test",
            "HandoffBusyError: передачу №5 держит другая задача — повторим позже",
        )
    ]


def test_job_outside_the_queue_does_not_need_a_job_to_fail(
    monkeypatch: pytest.MonkeyPatch, wired: Recorder
) -> None:
    monkeypatch.setattr(handoff_jobs, "setup_logging", lambda: None)

    async def broken(_handoff_id: int) -> dict[str, object]:
        raise RuntimeError("база отвалилась")

    monkeypatch.setattr(handoff_jobs, "run_hand_off", broken)
    monkeypatch.setattr(handoff_jobs, "get_current_job", lambda: None)

    with pytest.raises(RuntimeError, match="база отвалилась"):
        handoff_jobs.hand_off_lead(5)


def test_job_runs_the_core_in_its_own_loop(
    monkeypatch: pytest.MonkeyPatch, wired: Recorder
) -> None:
    monkeypatch.setattr(handoff_jobs, "setup_logging", lambda: None)

    async def done(handoff_id: int) -> dict[str, object]:
        return {"handoff": handoff_id}

    monkeypatch.setattr(handoff_jobs, "run_hand_off", done)

    assert handoff_jobs.hand_off_lead(31) == {"handoff": 31}


# --- проход повторов ----------------------------------------------------------------------


async def test_pass_queues_handoffs_whose_time_has_come(
    session: AsyncSession, wired: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    row.kommo, row.due_at = HandoffKommo.RETRY, datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()
    queued: list[int] = []
    monkeypatch.setattr(handoff_jobs, "enqueue_handoff", queued.append)

    await handoff_jobs.retry_pass()

    assert queued == [row.id]
    pushed = await session.get(SalesHandoffModel, row.id, populate_existing=True)
    assert pushed is not None
    assert pushed.due_at is not None
    assert pushed.due_at > datetime.now(UTC) + timedelta(seconds=cfg.HANDOFF_RETRY_SEC - 60)


async def test_pass_survives_a_queue_that_does_not_answer(
    session: AsyncSession,
    wired: Recorder,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    dialog = await sales_dialog(session)
    row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
    row.due_at = datetime.now(UTC) - timedelta(minutes=1)
    await session.commit()

    def refuse(_handoff_id: int) -> None:
        raise RedisConnectionError("очередь не отвечает")

    monkeypatch.setattr(handoff_jobs, "enqueue_handoff", refuse)

    await handoff_jobs.retry_pass()

    assert "не поставлен" in caplog.text


async def test_quiet_pass_queues_nothing(
    session: AsyncSession, wired: Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued: list[int] = []
    monkeypatch.setattr(handoff_jobs, "enqueue_handoff", queued.append)
    await handoff_jobs.retry_pass()
    assert queued == []


def test_reaper_runs_the_handoff_pass_as_its_third_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[float, str]] = []

    async def every(interval: float, _work: Any, *, name: str) -> None:
        started.append((interval, name))

    monkeypatch.setattr(reaper, "every", every)
    monkeypatch.setattr(reaper, "check_storage", lambda: None)

    reaper.main()

    assert (cfg.HANDOFF_PASS_SEC, "Повтор передачи лидов продаж") in started


async def test_job_client_is_a_plain_httpx_client() -> None:
    async with handoff_jobs._http() as http:
        assert isinstance(http, httpx.AsyncClient)


# --- гарантии процессу разбора: проход продаж не роняет соседей и не краснит здоровье ------

HANDOFF_LOOP = "Повтор передачи лидов продаж"


async def _until(condition: Callable[[], bool], *, seconds: float = 5.0) -> None:
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


async def test_falling_handoff_pass_does_not_stop_the_other_reaper_loops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Проход повторов передачи падает на каждом круге — разбор прогонов и сторож тишины
    идут своим чередом, исключение из `_loops` не выходит, а падение видно только
    в отметке своего цикла."""
    calls = {"sweep": 0, "watch": 0, "handoffs": 0}

    def counting(name: str) -> Callable[[], Awaitable[None]]:
        async def work() -> None:
            calls[name] += 1

        return work

    async def falling() -> None:
        calls["handoffs"] += 1
        raise RuntimeError("проход повторов передачи упал")

    monkeypatch.setattr(ticker, "BEATS_DIR", tmp_path)
    monkeypatch.setattr(reaper, "sweep", counting("sweep"))
    monkeypatch.setattr(reaper, "watch", counting("watch"))
    monkeypatch.setattr(reaper, "retry_handoffs", falling)
    monkeypatch.setattr(reaper, "POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(reaper, "WATCHDOG_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(cfg, "HANDOFF_PASS_SEC", 0.01)

    loops = asyncio.create_task(reaper._loops())
    await _until(lambda: min(calls.values()) >= 2 or loops.done())

    assert not loops.done(), "исключение прохода передачи вышло из циклов разбора"
    loops.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loops
    assert calls["sweep"] >= 2
    assert calls["watch"] >= 2
    assert calls["handoffs"] >= 2
    beats = _beats(tmp_path)
    assert beats["Разбор мёртвых прогонов"]["failures"] == 0
    assert beats["Сторож тишины"]["failures"] == 0
    assert beats[HANDOFF_LOOP]["failures"] >= 2


async def _due_handoffs(session: AsyncSession, count: int) -> list[int]:
    """Передачи, которым пора повторить: Kommo не ответил минуту назад."""
    ids = []
    for number in range(count):
        dialog = await sales_dialog(
            session, host=f"due{number}.example.test", email=f"a@due{number}.example.test"
        )
        row = await handoff.start(session, dialog.thread.id, enqueue=lambda _id: None)
        row.kommo, row.due_at = HandoffKommo.RETRY, datetime.now(UTC) - timedelta(minutes=1)
        ids.append(row.id)
    await session.commit()
    return ids


def _forbid_providers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Kommo и бот продаж: позвали из прохода — громко. Их зовёт задача в очереди."""
    called: list[str] = []

    def forbidden(name: str) -> Callable[..., Any]:
        def call(*_args: object, **_kwargs: object) -> Any:
            called.append(name)
            raise AssertionError(f"проход повторов позвал {name}")

        return call

    for name in ("connected_kommo", "build_kommo", "SalesBot", "_http"):
        monkeypatch.setattr(handoff_jobs, name, forbidden(name))
    monkeypatch.setattr(telegram.SalesBot, "send", forbidden("SalesBot.send"))
    return called


def _redis_down(tried: list[int]) -> Callable[[int], None]:
    def refuse(handoff_id: int) -> None:
        tried.append(handoff_id)
        raise RedisConnectionError("очередь не отвечает")

    return refuse


async def test_pass_builds_no_provider_and_survives_redis_down_on_every_handoff(
    session: AsyncSession,
    wired: Recorder,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    ids = await _due_handoffs(session, 2)
    called = _forbid_providers(monkeypatch)
    tried: list[int] = []
    monkeypatch.setattr(handoff_jobs, "enqueue_handoff", _redis_down(tried))

    await handoff_jobs.retry_pass()

    assert sorted(tried) == sorted(ids), "отказ очереди на первой передаче остановил вторую"
    assert called == []
    assert caplog.text.count("повтор передачи лида не поставлен") == 2


async def test_reaper_handoff_loop_is_the_sales_retry_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    """Цикл передачи в процессе разбора — это проход повторов модуля продаж, и только он."""
    called: list[str] = []

    async def retry_pass() -> None:
        called.append("pass")

    monkeypatch.setattr(handoff_jobs, "retry_pass", retry_pass)

    await reaper.retry_handoffs()

    assert called == ["pass"]


def test_reaper_does_not_load_the_sales_handoff_module_at_start() -> None:
    """Модуль передачи (и за ним клиенты Kommo и бота) грузится первым проходом цикла, а не
    импортом процесса разбора: сломанный импорт продаж не должен мешать процессу подняться."""
    code = (
        "import sys, backend.workers.reaper; "
        "print('backend.features.sales.handoff_jobs' in sys.modules)"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert done.stdout.strip() == "False", done.stderr


async def test_broken_sales_module_import_does_not_stop_the_other_reaper_loops(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Импорт модуля продаж падает на каждом круге — разбор прогонов и сторож тишины идут
    своим чередом: сбой импорта — сбой одного цикла, его ловит `every`."""
    calls = {"sweep": 0, "watch": 0}

    def counting(name: str) -> Callable[[], Awaitable[None]]:
        async def work() -> None:
            calls[name] += 1

        return work

    monkeypatch.setitem(sys.modules, "backend.features.sales.handoff_jobs", None)
    with pytest.raises(ImportError):
        await reaper.retry_handoffs()
    monkeypatch.setattr(ticker, "BEATS_DIR", tmp_path)
    monkeypatch.setattr(reaper, "sweep", counting("sweep"))
    monkeypatch.setattr(reaper, "watch", counting("watch"))
    monkeypatch.setattr(reaper, "POLL_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(reaper, "WATCHDOG_INTERVAL_SEC", 0.01)
    monkeypatch.setattr(cfg, "HANDOFF_PASS_SEC", 0.01)

    loops = asyncio.create_task(reaper._loops())
    await _until(lambda: min(calls.values()) >= 2 or loops.done())

    assert not loops.done(), "сбой импорта модуля продаж вышел из циклов разбора"
    loops.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loops


async def test_reaper_stays_healthy_after_such_a_pass(
    session: AsyncSession,
    wired: Recorder,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Kommo и Telegram недоступны, Redis не отвечает — отметка цикла передачи без
    неудач, у процесса разбора проблем нет: недоступный провайдер — повтор позже."""
    await _due_handoffs(session, 2)
    called = _forbid_providers(monkeypatch)
    tried: list[int] = []
    monkeypatch.setattr(handoff_jobs, "enqueue_handoff", _redis_down(tried))
    passed = asyncio.Event()

    async def handoff_pass() -> None:
        try:
            await handoff_jobs.retry_pass()
        finally:
            passed.set()

    async def nothing() -> None:
        return None

    monkeypatch.setattr(ticker, "BEATS_DIR", tmp_path)
    monkeypatch.setattr(reaper, "sweep", nothing)
    monkeypatch.setattr(reaper, "watch", nothing)
    monkeypatch.setattr(reaper, "retry_handoffs", handoff_pass)

    loops = asyncio.create_task(reaper._loops())
    await asyncio.wait_for(passed.wait(), timeout=5)
    loops.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loops

    assert len(tried) == 2
    assert called == []
    assert _beats(tmp_path)[HANDOFF_LOOP]["failures"] == 0
    assert health.beat_problems(tmp_path) == []
