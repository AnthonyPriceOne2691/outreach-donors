"""Задача прогона из очереди — на настоящей базе.

До 24.09.2026 её не исполнял ни один тест: проверялось только, что задача
существует и что API ставит её в очередь. А задача падала на первой же
строке — ленивое чтение `run.settings` в асинхронной сессии (MissingGreenlet),
и ни один прогон с кнопки экрана не доходил до выдачи. Консольный прогон
шёл мимо задачи, поэтому все прогоны до этого дня проходили. Нашёл первый
тестовый прогон на проде.

Платные части подменены: вопрос теста — доходит ли задача до сбора и с тем
ли потолком, а не что вернёт Ahrefs.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from backend.config import filters
from backend.config.startup_checks import ConfigError
from backend.features.core.domain import RunStatus, Stage
from backend.features.runs.budget import CapExceededError
from backend.features.runs.pipeline import RunDeps, RunRequest
from backend.features.runs.repository import RunRepository, unique_share_from
from backend.features.runs.thresholds import defaults
from backend.workers import jobs
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class _Closable:
    async def aclose(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


async def _no_heartbeat(*args: object, **kwargs: object) -> None:
    return None


@pytest.fixture
def pipeline(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> dict[str, Any]:
    """Задача работает в той же транзакции, что и тест, а сбор — подменён."""
    seen: dict[str, Any] = {}

    async def execute(deps: RunDeps, request: RunRequest) -> SimpleNamespace:
        seen["request"] = request
        return SimpleNamespace(
            plan=SimpleNamespace(new=[], estimate=SimpleNamespace(total=0)),
            by_status={},
            spent_units=0,
        )

    monkeypatch.setattr(jobs, "execute_run", execute)
    monkeypatch.setattr(jobs, "heartbeat", _no_heartbeat)
    monkeypatch.setattr(jobs, "AhrefsClient", _Closable)
    monkeypatch.setattr(jobs, "build_provider", lambda client: object())
    monkeypatch.setattr(jobs, "create_async_engine", lambda dsn: _Closable())
    monkeypatch.setattr(
        jobs,
        "async_sessionmaker",
        lambda engine, **kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    return seen


async def test_queued_run_reaches_the_pipeline_with_its_own_cap(
    session: AsyncSession, pipeline: dict[str, Any]
) -> None:
    runs = RunRepository(session)
    settings = await runs.create_settings(
        defaults(),
        geo_top_n=filters.GEO_TOP_N,
        geo_min_share=filters.GEO_MIN_SHARE,
        metrics_ttl_days=filters.METRICS_TTL_DAYS,
        price_ttl_days=filters.PRICE_TTL_DAYS,
        units_cap=1234,
    )
    run = await runs.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=["home improvement write for us", "best cordless drill"],
        country="us",
        depth_pages=1,
    )
    await session.commit()

    result = await jobs._run(run.id)

    request = pipeline["request"]
    assert request.cap == 1234, "потолок обещан человеку при запуске и обязан доехать до сбора"
    assert request.settings_id == settings.id
    assert request.keywords == ["home improvement write for us", "best cordless drill"]
    assert request.country == "us"
    assert result["run"] == run.id


# --- исход прогона — в самом прогоне ---------------------------------------


async def _queued_run(session: AsyncSession, *, cap: int = 3000) -> Any:
    runs = RunRepository(session)
    settings = await runs.create_settings(
        defaults(),
        geo_top_n=filters.GEO_TOP_N,
        geo_min_share=filters.GEO_MIN_SHARE,
        metrics_ttl_days=filters.METRICS_TTL_DAYS,
        price_ttl_days=filters.PRICE_TTL_DAYS,
        units_cap=cap,
    )
    run = await runs.create_run(
        stage=Stage.DONORS, settings_id=settings.id, keywords=["a"], country="de", depth_pages=1
    )
    await session.commit()
    return run


@pytest.fixture
def failing(monkeypatch: pytest.MonkeyPatch, pipeline: dict[str, Any]) -> dict[str, Any]:
    """Сбор падает тем, что задаст тест; настройки считаются в порядке."""
    monkeypatch.setattr(jobs, "check_collect", lambda: None)

    def fail_with(error: Exception) -> None:
        async def execute(deps: RunDeps, request: RunRequest) -> None:
            raise error

        monkeypatch.setattr(jobs, "execute_run", execute)

    return {"fail_with": fail_with}


async def test_cap_refusal_closes_the_run_with_its_reason(
    session: AsyncSession, failing: dict[str, Any]
) -> None:
    """Потолок — решение человека, а не сбой: прогон закрывается сразу, без
    исключения и без перезапусков. 24.09.2026 №22 вместо этого падал,
    перезапускался дважды, а в записи стояло «воркер умер»."""
    run = await _queued_run(session)
    failing["fail_with"](
        CapExceededError("Прогон обойдётся в 3480 юнитов, доступно 3000. Новых доменов 61 из 62")
    )

    result = await jobs._search(run.id)
    await session.refresh(run)

    assert run.status is RunStatus.STOPPED
    assert "3480" in run.stats["причина"]
    assert "воркер умер" not in run.stats["причина"]
    assert result["refused"]


async def test_unexpected_failure_is_written_and_still_raised(
    session: AsyncSession, failing: dict[str, Any]
) -> None:
    """Неожиданное падение пробрасывается — очередь и разбор должны его
    видеть, — но причина лежит в прогоне уже сейчас, а не через три минуты."""
    run = await _queued_run(session)
    failing["fail_with"](RuntimeError("провайдер лёг"))

    with pytest.raises(RuntimeError):
        await jobs._search(run.id)
    await session.refresh(run)

    assert run.stats["причина"] == "сбой, будет продолжен: RuntimeError: провайдер лёг"
    assert run.status is not RunStatus.STOPPED, "решение о повторе — за разбором"


async def test_config_refusal_does_not_reach_the_pipeline(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, pipeline: dict[str, Any]
) -> None:
    run = await _queued_run(session)

    def broken() -> None:
        raise ConfigError("SERP_LOGIN пуст — выдачу покупать не на что")

    monkeypatch.setattr(jobs, "check_collect", broken)

    await jobs._search(run.id)
    await session.refresh(run)

    assert "request" not in pipeline, "с неверной настройкой сбор не начинается"
    assert run.status is RunStatus.STOPPED
    assert "SERP_LOGIN" in run.stats["причина"]


# --- доля уникальных доменов для сметы до запуска ---------------------------


def test_unique_share_takes_the_worst_recent_run() -> None:
    history = [
        {"serp_results": 86, "unique_hosts": 70},  # 0,81
        {"serp_results": 853, "unique_hosts": 535},  # 0,63
        {"serp_results": 500, "unique_hosts": 85},  # 0,17 — старый замер
    ]
    assert unique_share_from(history, 0.5) == pytest.approx(70 / 86)


def test_tiny_runs_do_not_count_and_no_history_gives_default() -> None:
    assert unique_share_from([{"serp_results": 3, "unique_hosts": 3}], 0.85) == 0.85
    assert unique_share_from([], 0.85) == 0.85
    assert unique_share_from([{"serp_results": 40}], 0.85) == 0.85
