"""Задача прогона гипотезы (`segment_jobs.run_segment`): свои клиенты, постоянный отказ —
итогом и словами в строке прогона, временный — повтором очереди.

Сеть не трогается: проход прогона подменён, клиенты только собираются.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.config import judge as judge_cfg
from backend.config import llm as llm_cfg
from backend.config import storage
from backend.config.startup_checks import ConfigError
from backend.features.sales import segment_jobs
from backend.features.sales.segment_estimate import SALES_OFF, SERP_AHREFS
from backend.features.sales.segment_judge import SegmentClient
from backend.features.sales.segment_models import Stage
from backend.features.sales.segment_run import RunDeps
from backend.features.sales.verifier import FixtureVerifier
from backend.features.serp.factory import UnknownProviderError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import TEST_DSN
from tests.test_sales_segment_run import hypothesis, make_run


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(segment_jobs, "setup_logging", lambda: None)
    monkeypatch.setattr(segment_jobs, "check_storage", lambda: None)
    monkeypatch.setattr("backend.config.sales.ENABLED", True)
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr("backend.config.sales.VERIFIER_PROVIDER", "fixture")
    monkeypatch.setattr("backend.config.serp.PROVIDER", "dataforseo")
    monkeypatch.setattr("backend.config.serp.LOGIN", "login-test")
    monkeypatch.setattr("backend.config.serp.PASSWORD", "password-test")  # pragma: allowlist secret


def test_a_permanent_refusal_is_the_outcome_not_three_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def refused(_run_id: int) -> dict[str, Any]:
        raise ConfigError("SALES_VERIFIER_PROVIDER=live, а ключа нет")

    monkeypatch.setattr(segment_jobs, "run_once", refused)

    assert segment_jobs.run_segment(5) == {
        "error": "ConfigError: SALES_VERIFIER_PROVIDER=live, а ключа нет",
        "permanent": True,
    }


def test_a_passing_failure_goes_to_the_queue_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    async def broken(_run_id: int) -> dict[str, Any]:
        raise ConnectionError("база не ответила")

    monkeypatch.setattr(segment_jobs, "run_once", broken)

    with pytest.raises(ConnectionError, match="база не ответила"):
        segment_jobs.run_segment(5)


async def test_the_job_runs_with_its_own_session_and_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[RunDeps] = []

    async def execute(_session: AsyncSession, run_id: int, deps: RunDeps) -> dict[str, Any]:
        seen.append(deps)
        return {"run": run_id}

    monkeypatch.setattr(segment_jobs, "execute", execute)
    monkeypatch.setattr(judge_cfg, "HOME_CHECK", True)

    assert await segment_jobs.run_once(7) == {"run": 7}

    [deps] = seen
    assert deps.serp.name == "dataforseo"
    assert isinstance(deps.judge, SegmentClient)
    assert deps.home is not None
    assert isinstance(deps.verifier, FixtureVerifier)


async def test_the_judge_waits_for_the_model_as_long_as_the_donor_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Модель с рассуждением отвечает дольше умолчания httpx (5 с): судья ждёт её свой срок
    (`LLM_TIMEOUT_S`), а проверка адресов — на своём клиенте с умолчанием, как у очистки."""
    seen: list[RunDeps] = []

    async def execute(_session: AsyncSession, _run_id: int, deps: RunDeps) -> dict[str, Any]:
        seen.append(deps)
        return {}

    monkeypatch.setattr(segment_jobs, "execute", execute)
    monkeypatch.setattr(llm_cfg, "TIMEOUT_S", 181.0)

    await segment_jobs.run_once(7)

    judge = seen[0].judge
    assert isinstance(judge, SegmentClient)
    assert judge._http.timeout.read == 181.0


async def test_home_check_off_judges_by_the_serp_only(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[RunDeps] = []

    async def execute(_session: AsyncSession, _run_id: int, deps: RunDeps) -> dict[str, Any]:
        seen.append(deps)
        return {}

    monkeypatch.setattr(segment_jobs, "execute", execute)
    monkeypatch.setattr(judge_cfg, "HOME_CHECK", False)

    await segment_jobs.run_once(7)

    assert seen[0].home is None


async def test_an_unbuildable_serp_stops_the_run_in_words(monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[tuple[int, str]] = []

    def unbuildable(_client: object) -> object:
        raise UnknownProviderError("SERP_PROVIDER=«нет такого» — такого источника нет")

    async def stop(_factory: object, run_id: int, words: str) -> None:
        stopped.append((run_id, words))

    monkeypatch.setattr(segment_jobs, "build_provider", unbuildable)
    monkeypatch.setattr(segment_jobs, "_stop", stop)

    with pytest.raises(segment_jobs.SerpNotSetError):
        await segment_jobs.run_once(7)

    assert stopped == [
        (7, "SerpNotSetError: SERP_PROVIDER=«нет такого» — такого источника нет"),
    ]


async def test_serp_switched_to_ahrefs_after_the_estimate_stops_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Настройку выдачи сменили на Ahrefs после сметы: прогон не тратит юниты мимо журнала."""
    stopped: list[tuple[int, str]] = []
    ran: list[int] = []

    async def stop(_factory: object, run_id: int, words: str) -> None:
        stopped.append((run_id, words))

    async def execute(_session: AsyncSession, run_id: int, _deps: RunDeps) -> dict[str, Any]:
        ran.append(run_id)
        return {}

    monkeypatch.setattr("backend.config.serp.PROVIDER", "ahrefs")
    monkeypatch.setattr(segment_jobs, "_stop", stop)
    monkeypatch.setattr(segment_jobs, "execute", execute)

    with pytest.raises(segment_jobs.SerpNotSetError):
        await segment_jobs.run_once(7)

    assert ran == []
    assert stopped == [(7, f"SerpNotSetError: {SERP_AHREFS}")]


async def test_sales_switched_off_after_the_start_stop_the_run_in_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Модуль выключили, пока прогон ждал очереди: прогон не тратит и говорит почему."""
    stopped: list[tuple[int, str]] = []
    ran: list[int] = []

    async def stop(_factory: object, run_id: int, words: str) -> None:
        stopped.append((run_id, words))

    async def execute(_session: AsyncSession, run_id: int, _deps: RunDeps) -> dict[str, Any]:
        ran.append(run_id)
        return {}

    monkeypatch.setattr("backend.config.sales.ENABLED", False)
    monkeypatch.setattr(segment_jobs, "_stop", stop)
    monkeypatch.setattr(segment_jobs, "execute", execute)

    with pytest.raises(segment_jobs.SalesOffError):
        await segment_jobs.run_once(7)

    assert ran == []
    assert stopped == [(7, f"SalesOffError: {SALES_OFF}")]


async def test_the_stop_words_land_on_the_run_row(session: AsyncSession) -> None:
    """Экран читает причину остановки из строки прогона, а не из журнала воркера."""
    run = await make_run(session, await hypothesis(session))
    factory = async_sessionmaker(bind=await session.connection(), expire_on_commit=False)

    await segment_jobs._stop(factory, run.id, "SerpNotSetError: источника нет")
    await segment_jobs._stop(factory, 987654, "прогона нет — и молча")

    await session.refresh(run)
    assert (run.stage, run.error) == (Stage.STOPPED, "SerpNotSetError: источника нет")
