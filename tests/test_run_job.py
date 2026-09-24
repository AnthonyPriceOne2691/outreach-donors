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
from backend.features.core.domain import Stage
from backend.features.runs.pipeline import RunDeps, RunRequest
from backend.features.runs.repository import RunRepository
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
