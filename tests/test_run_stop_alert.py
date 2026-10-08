"""Тревога об остановленном прогоне — телом задачи, на настоящей базе.

Правило (`runs/stopped.py`): одна тревога на прогон, когда он закрыт
насовсем, и ни одной на сбой, который будет продолжен. Проверяется тем
путём, каким прогон закрывается на деле: задача очереди (`jobs._search`)
с настоящим сбором поверх подменённого провайдера и разбор мёртвых
(`recover`) своей сессией на проход, как у процесса разбора. Платное
подменено, сеть Telegram — тоже: транспорт записывает, что ушло.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.config import filters
from backend.config.judge import JudgeMode
from backend.config.startup_checks import ConfigError
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.core.domain import RunStatus, Stage
from backend.features.core.models.run import RunModel
from backend.features.runs.lifecycle import (
    MAX_RESUMES,
    RESUME_AFTER_SEC,
    STALE_AFTER_SEC,
    Recovery,
    recover,
)
from backend.features.runs.pipeline import RunRequest, execute_run
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from backend.shared import alerts
from backend.workers import jobs
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_execute_run import GOOD, FakeSerp, _ahrefs, _deps, _flaky_ahrefs

TOKEN = "123456:run-alert-test-token"

#: Отказ Ahrefs по коду — словами, как его видит человек. До 08.10.2026 сырой
#: ответ («batch_metrics: 403 forbidden») уходил в чат и на экран общими
#: словами: «техническая ошибка (AhrefsError)».
REFUSED = "batch_metrics: Ahrefs ответил 403: forbidden"


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Бот настроен, сеть подменена: список — тексты, которые ушли в чат."""
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/bot{TOKEN}/sendMessage"
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True, "result": {}})

    monkeypatch.setattr("backend.config.alerts.TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr("backend.config.alerts.TELEGRAM_CHAT_ID", "-100200300")
    monkeypatch.setattr(
        alerts, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    return sent


class _Closable:
    async def aclose(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


async def _no_heartbeat(*args: object, **kwargs: object) -> None:
    return None


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> dict[str, Any]:
    """Задача прогона в транзакции теста: сбор настоящий, провайдеры — нет.

    Каждая попытка берёт нового клиента Ahrefs, как и боевая задача; каким
    он будет, решает тест (`provider["ahrefs"]`). Судья и главные сайтов
    выключены: это сеть, и к тревоге они отношения не имеют.
    """
    wiring: dict[str, Callable[[], AhrefsClient]] = {"ahrefs": lambda: _ahrefs({"good.com": GOOD})}
    monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.OFF)
    monkeypatch.setattr("backend.config.judge.HOME_CHECK", False)
    # Паузы между повторами запроса — ноль: проверяется число попыток, а не часы.
    monkeypatch.setattr("backend.features.ahrefs.client._retry_delay", lambda *_a: 0.0)
    monkeypatch.setattr(jobs, "check_collect", lambda: None)
    monkeypatch.setattr(jobs, "heartbeat", _no_heartbeat)

    def ahrefs() -> AhrefsClient:
        # Клиент берётся в момент попытки: тест меняет его между попытками.
        return wiring["ahrefs"]()

    monkeypatch.setattr(jobs, "AhrefsClient", ahrefs)
    monkeypatch.setattr(jobs, "build_provider", lambda _client: FakeSerp(["https://good.com"]))
    monkeypatch.setattr(jobs, "create_async_engine", lambda _dsn: _Closable())
    monkeypatch.setattr(
        jobs,
        "async_sessionmaker",
        lambda _engine, **_kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    return wiring


async def _queued_run(session: AsyncSession) -> RunModel:
    runs = RunRepository(session)
    settings = await runs.create_settings(
        defaults(),
        geo_top_n=filters.GEO_TOP_N,
        geo_min_share=filters.GEO_MIN_SHARE,
        metrics_ttl_days=filters.METRICS_TTL_DAYS,
        price_ttl_days=filters.PRICE_TTL_DAYS,
        units_cap=100_000,
    )
    run = await runs.create_run(
        stage=Stage.DONORS, settings_id=settings.id, keywords=["crm"], country="us"
    )
    await session.commit()
    return run


async def _silent_for(session: AsyncSession, run_id: int, seconds: float) -> None:
    """Прогон молчит столько-то: время последней записи — отдельным запросом,
    иначе запись сама сделала бы его только что живым."""
    await session.execute(
        update(RunModel)
        .where(RunModel.id == run_id)
        .values(updated_at=datetime.now(UTC) - timedelta(seconds=seconds))
    )
    await session.commit()


#: Что сохранила очередь про упавшую задачу — строка исключения как есть.
FELL = "backend.features.ahrefs.client.AhrefsError: batch_metrics: не удалось за 4 попыток — 503"


async def _sweep(session: AsyncSession) -> Recovery:
    """Проход разбора своей сессией, как у процесса разбора: сессия теста
    держит прогон таким, каким видела его до задачи."""
    factory = async_sessionmaker(bind=session.bind, expire_on_commit=False)
    async with factory() as own:
        return await recover(
            RunRepository(own),
            alive=lambda _job: False,
            enqueue=lambda run_id: f"job-{run_id}-{datetime.now(UTC).timestamp()}",
            failure=lambda _job: FELL,
        )


async def test_refusal_mid_run_is_told_once(
    session: AsyncSession, provider: dict[str, Any], telegram: list[str]
) -> None:
    """Отказ посреди сбора закрывают двое: сам прогон и задача очереди
    после него. Тревога — одна: от того, кто закрыл первым."""
    provider["ahrefs"] = lambda: _ahrefs({"good.com": GOOD}, fail_after_screen=True)
    run = await _queued_run(session)

    result = await jobs._search(run.id)
    await session.refresh(run)

    assert run.status is RunStatus.STOPPED
    assert result["refused"]
    assert telegram == [f"outreach-donors: прогон №{run.id} остановлен: {REFUSED}"]


async def test_refusal_before_the_run_is_told_once(
    session: AsyncSession,
    provider: dict[str, Any],
    telegram: list[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Неверная настройка: сбор не начинается, прогон закрывает задача —
    и она же говорит человеку, что делать, его же словами."""

    def broken() -> None:
        raise ConfigError("SERP_LOGIN пуст — выдачу покупать не на что")

    monkeypatch.setattr(jobs, "check_collect", broken)
    run = await _queued_run(session)

    await jobs._search(run.id)
    await session.refresh(run)

    assert run.status is RunStatus.STOPPED
    assert telegram == [
        f"outreach-donors: прогон №{run.id} остановлен: SERP_LOGIN пуст — выдачу покупать не на что"
    ]


async def test_retries_are_silent_and_the_burial_is_told_once(
    session: AsyncSession, provider: dict[str, Any], telegram: list[str]
) -> None:
    """Сеть лежит всё время: первая попытка и два продолжения падают, разбор
    каждый раз ставит новую задачу. Ни одна попытка не событие — событие,
    когда продолжать больше нечем. И следующий проход разбора молчит."""
    provider["ahrefs"] = lambda: _flaky_ahrefs({"good.com": GOOD}, broken={"on": True})
    run = await _queued_run(session)

    for attempt in range(1, MAX_RESUMES + 2):
        with pytest.raises(AhrefsError):
            await jobs._search(run.id)
        assert telegram == [], f"попытка {attempt}: сбой будет продолжен — тревоги нет"
        if attempt <= MAX_RESUMES:
            await _silent_for(session, run.id, RESUME_AFTER_SEC + 10)
            outcome = await _sweep(session)
            assert outcome.resumed == [run.id], f"продолжение №{attempt}"
            assert telegram == [], "продолжение — тоже не событие"

    await _silent_for(session, run.id, STALE_AFTER_SEC + 10)
    buried = await _sweep(session)
    again = await _sweep(session)
    await session.refresh(run)

    assert buried.stopped == [run.id]
    assert again.stopped == [], "закрытый прогон разбор больше не видит"
    assert run.status is RunStatus.STOPPED
    assert len(telegram) == 1, telegram
    assert telegram[0].startswith(
        f"outreach-donors: прогон №{run.id} остановлен разбором: "
        "задача упала: batch_metrics: не удалось за 4 попыток — 503, "
        f"продолжений {MAX_RESUMES} из {MAX_RESUMES}, молчание "
    )
    assert "AhrefsError" not in telegram[0], "имя класса — журналу, не человеку"


async def test_console_run_is_told_too(session: AsyncSession, telegram: list[str]) -> None:
    """Консольный прогон идёт мимо очереди — закрывает его сам сбор,
    и тревога уходит оттуда же."""
    deps = await _deps(
        session,
        FakeSerp(["https://good.com"]),
        _ahrefs({"good.com": GOOD}, fail_after_screen=True),
    )
    settings = await RunRepository(session).create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )

    with pytest.raises(AhrefsError):
        await execute_run(deps, RunRequest(["crm"], "us", defaults(), settings.id))

    assert len(telegram) == 1
    assert telegram[0].endswith(f"остановлен: {REFUSED}")


async def test_a_failing_alert_does_not_hide_the_stop(
    session: AsyncSession,
    provider: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Telegram лежит — прогон всё равно закрыт с причиной, а задача
    отвечает отказом, а не падением тревоги."""
    provider["ahrefs"] = lambda: _ahrefs({"good.com": GOOD}, fail_after_screen=True)
    monkeypatch.setattr("backend.config.alerts.TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setattr("backend.config.alerts.TELEGRAM_CHAT_ID", "-100200300")

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("нет сети", request=request)

    monkeypatch.setattr(
        alerts, "_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    run = await _queued_run(session)

    result = await jobs._search(run.id)
    await session.refresh(run)

    assert result["refused"]
    assert run.status is RunStatus.STOPPED
    assert run.stats["причина"] == f"остановлен: {REFUSED}"
