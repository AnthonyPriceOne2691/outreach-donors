"""Конвейер «Плана работ» целиком и его проводка: проход заводит прогон, задача прогона проводит его
своим телом, следующий проход видит итог; цикл процесса разбора, тумблеры и настройки конвейера.

**Целиком** — без подмены пути: проход конвейера (`plan_tick.step`) считает настоящую смету и заводит
прогон (`segment_launch.launch`) в очередь-подделку с правилами rq; задача прогона из очереди —
`segment_jobs.run_once` с её клиентами и проходом (`segment_run.execute`: выдача, судья, адреса,
лиды); её сессии — на соединении теста, сеть — подделки прогона сбора (`test_sales_segment_run`:
выдача, модель, сайты, DNS). Следующий проход видит кончившийся прогон: купленное — в реестре,
«найдено» — принятые судьёй сайты, цель набрана — гипотеза в письмах.

**Проводка** — по образцу автосборки (`test_sales_autosend_watch.py`): цикл «Конвейер плана работ»
со своим интервалом, проход — `plan_tick.tick` за обоими тумблерами, выключенный не грузит модуль
продаж в процесс разбора; настройки читаются, не падая: опечатка выключает конвейер, а не старт.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import httpx
import pytest
from backend.config import judge as judge_cfg
from backend.config import sales as sales_cfg
from backend.config import sales_plan as plan_cfg
from backend.features.sales import plan_tick, segment_jobs
from backend.features.sales.models import LeadSource, SalesHypothesisModel, SalesLeadModel
from backend.features.sales.plan_models import SalesPlanHypothesisModel
from backend.features.sales.plan_moves import Did, Turn
from backend.features.sales.segment_jobs import SEGMENT_JOB
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.workers import reaper
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_plan_tick import conveyor, jobs, pages
from tests.test_sales_plan_waves import add_keys, make_plan
from tests.test_sales_segment_estimate import _settings
from tests.test_sales_segment_run import (
    ANSWERS,
    KEYWORD,
    NEWS,
    PARTS,
    SEGMENT,
    SHOP,
    Model,
    Serp,
    _network,
    serp,
    web,
)

__all__ = ["_network", "_settings", "conveyor", "jobs", "web"]  # оснастка соседних тестов

ROOT = Path(__file__).resolve().parents[1]
LOOP = "Конвейер плана работ"


class _Engine:
    """Движок задачи — без своего пула: её сессии идут соединением теста."""

    async def dispose(self) -> None:
        return None


def job_here(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession, found: Serp, model: Model
) -> None:
    """Задача прогона — на соединении теста (база теста откатывается целиком), выдача и модель —
    подделки; пульс выключен: второй сессией на том же соединении он бился бы с проходом."""
    factory = async_sessionmaker(bind=session.bind, expire_on_commit=False)

    async def no_pulse(*_args: object, **_kwargs: object) -> None:
        return None

    def made(*_args: object, **_kwargs: object) -> Callable[..., AsyncSession]:
        return factory

    monkeypatch.setattr(segment_jobs, "create_async_engine", lambda _dsn: _Engine())
    monkeypatch.setattr(segment_jobs, "async_sessionmaker", made)
    monkeypatch.setattr(segment_jobs, "pulse", no_pulse)
    monkeypatch.setattr(segment_jobs, "_serp", lambda _ahrefs, _mode: found)
    monkeypatch.setattr(
        segment_jobs,
        "judge_http",
        lambda _tier: httpx.AsyncClient(transport=httpx.MockTransport(model)),
    )
    monkeypatch.setattr(judge_cfg, "HOME_CHECK", False)


async def test_the_conveyor_launches_the_job_runs_and_the_next_pass_sees_its_sites(
    session: AsyncSession, jobs: _Jobs, web: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Лист «Велосипеды»: один ключ, рынок `us`, цель — два сайта, топ-10. Проход заводит прогон
    экономного режима; задача прогона покупает выдачу, судья на Flex принимает магазин и запчасти и
    отсекает новости, у магазина находится адрес — лид. Следующий проход: купленное — в реестре,
    найдено два из двух — гипотеза в письмах, расход прогона — в её числах."""
    plan = await make_plan(session, cap=Decimal("5.00"))
    owner = SalesHypothesisModel(name="Велосипеды плана", description=SEGMENT)
    session.add(owner)
    await session.flush()
    row = SalesPlanHypothesisModel(
        plan_id=plan.id,
        hypothesis_id=owner.id,
        position=1,
        sheet="Велосипеды",
        markets=["us"],
        language="en",
        target=2,
        max_pages=1,
        cap_usd=Decimal("2.50"),
    )
    session.add(row)
    await session.flush()
    await add_keys(session, row, [KEYWORD])
    model = Model(ANSWERS)
    job_here(monkeypatch, session, serp(SHOP, NEWS, PARTS), model)

    launched = await plan_tick.step(session, jobs, now=datetime.now(UTC))

    [((path, run_id), _)] = jobs.enqueued
    assert (launched, path) == (Turn(Did.LAUNCHED, run_id=run_id), SEGMENT_JOB)

    report = await segment_jobs.run_once(run_id)

    run = await session.get(SalesSegmentRunModel, run_id, populate_existing=True)
    assert run is not None
    assert (run.stage, run.error, run.from_page, run.depth_pages) == ("done", None, 1, 1)
    assert run.estimate["judge_tier"] == "flex"
    assert sorted(model.asked) == sorted([SHOP, NEWS, PARTS])
    assert report["leads"]["created"] == 1
    [lead] = (await session.scalars(select(SalesLeadModel))).all()
    assert (lead.email, lead.source, lead.hypothesis_id) == (
        f"hello@{SHOP}",
        LeadSource.COLLECTED,
        owner.id,
    )

    after = await plan_tick.step(session, jobs, now=datetime.now(UTC) + timedelta(hours=1))

    assert after == Turn(Did.MOVED, plan_tick.TARGET_MET)
    assert (row.stage, row.found, row.reserved_usd) == ("sending", 2, Decimal(0))
    assert row.spent_usd > 0
    assert await pages(session, row) == {KEYWORD: 1}
    assert len(jobs.enqueued) == 1


# --- проводка процесса разбора ---------------------------------------------------------------


def test_the_reaper_runs_the_conveyor_loop_with_its_interval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[tuple[float, str, Any]] = []

    async def every(interval: float, work: Any, *, name: str) -> None:
        started.append((interval, name, work))

    monkeypatch.setattr(reaper, "every", every)
    monkeypatch.setattr(reaper, "check_storage", lambda: None)
    monkeypatch.setattr(plan_cfg, "PASS_SEC", 137)

    reaper.main()

    assert [(interval, name) for interval, name, _ in started if name == LOOP] == [(137, LOOP)]


@pytest.mark.parametrize(
    ("sales_on", "plan_on", "ticks"), [(True, True, 1), (True, False, 0), (False, True, 0)]
)
async def test_the_reaper_pass_is_the_conveyor_tick_behind_both_switches(
    monkeypatch: pytest.MonkeyPatch, sales_on: bool, plan_on: bool, ticks: int
) -> None:
    called: list[str] = []

    async def tick() -> None:
        called.append("конвейер")

    monkeypatch.setattr(plan_tick, "tick", tick)
    monkeypatch.setattr(sales_cfg, "ENABLED", sales_on)
    monkeypatch.setattr(plan_cfg, "ENABLED", plan_on)

    await reaper.plan_conveyor()

    assert len(called) == ticks


def test_the_reaper_loads_the_conveyor_only_when_it_is_switched_on() -> None:
    """Запуск процесса разбора и выключенный проход не грузят модуль продаж: процесс живёт в тесном
    пределе памяти, а сломанный импорт выключенного прохода ронял бы каждый его цикл."""
    code = (
        "import asyncio, sys\n"
        "from backend.config import sales, sales_plan\n"
        "from backend.workers import reaper\n"
        "before = set(sys.modules)\n"
        "for enabled, own in ((False, False), (False, True), (True, False)):\n"
        "    sales.ENABLED, sales_plan.ENABLED = enabled, own\n"
        "    asyncio.run(reaper.plan_conveyor())\n"
        "print(sorted(name for name in set(sys.modules) - before if name.startswith('backend.')))\n"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True, cwd=ROOT
    )
    assert done.stdout.strip() == "[]", done.stderr


# --- настройки -------------------------------------------------------------------------------

#: Настройки конвейера: имя переменной → постоянная модуля, умолчание и опечатка.
SETTINGS: dict[str, tuple[str, object, str]] = {
    "SALES_PLAN": ("ENABLED", False, "да"),
    "SALES_PLAN_PASS_SEC": ("PASS_SEC", 120, "5"),
    "SALES_PLAN_CAP_SHARE": ("CAP_SHARE", 0.5, "50%"),
    "SALES_PLAN_KEYS_PER_RUN": ("KEYS_PER_RUN", 100, "0"),
    "SALES_PLAN_PAUSE_MIN": ("PAUSE_MIN", 10, "-1"),
    "SALES_PLAN_RETRY_MIN": ("RETRY_MIN", 30, "полчаса"),
    "SALES_PLAN_LOST_TRIES": ("LOST_TRIES", 2, "11"),
}

RELOAD = """
import importlib, json, os, sys

import backend.config.sales_plan as plan

names, consts = json.loads(sys.argv[2])
found = {}
for case, env in json.loads(sys.argv[1]).items():
    for name in names:
        os.environ.pop(name, None)
    os.environ.update(env)
    module = importlib.reload(plan)
    found[case] = {"unread": sorted(module._unread), "values": {c: getattr(module, c) for c in consts}}
print(json.dumps(found))
"""


def _sample() -> dict[str, str]:
    """Настройки конвейера из образца `.env.example` — из него собирают `.env` развёртывания."""
    found = {}
    for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines():
        name, sep, value = line.partition("=")
        if sep and name in SETTINGS:
            found[name] = value
    return found


@pytest.fixture(scope="module")
def seen(tmp_path_factory: pytest.TempPathFactory) -> dict[str, dict[str, Any]]:
    """Модуль настроек, импортированный при каждом окружении — одним подпроцессом; каталог без
    `.env` — читается только окружение случая."""
    cases = {
        "образец": _sample(),
        "включён": {"SALES_PLAN": "true", "SALES_PLAN_PASS_SEC": "300"},
        **{name: {"SALES_PLAN": "true", name: bad} for name, (_, _, bad) in SETTINGS.items()},
    }
    consts = [const for const, _, _ in SETTINGS.values()]
    env = {name: value for name, value in os.environ.items() if name not in SETTINGS}
    done = subprocess.run(
        [sys.executable, "-c", RELOAD, json.dumps(cases), json.dumps([list(SETTINGS), consts])],
        cwd=tmp_path_factory.mktemp("no-env"),
        env={**env, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr
    found: dict[str, dict[str, Any]] = json.loads(done.stdout)
    return found


def test_the_sample_is_read_whole_and_the_conveyor_is_off(seen: dict[str, dict[str, Any]]) -> None:
    assert _sample().keys() == SETTINGS.keys()
    assert seen["образец"] == {
        "unread": [],
        "values": {const: default for const, default, _ in SETTINGS.values()},
    }


def test_switched_on_the_conveyor_runs_with_its_settings(seen: dict[str, dict[str, Any]]) -> None:
    assert seen["включён"]["unread"] == []
    assert (seen["включён"]["values"]["ENABLED"], seen["включён"]["values"]["PASS_SEC"]) == (
        True,
        300,
    )


@pytest.mark.parametrize("name", list(SETTINGS))
def test_a_typo_switches_the_conveyor_off_and_takes_the_default(
    seen: dict[str, dict[str, Any]], name: str
) -> None:
    const, default, _ = SETTINGS[name]

    found = seen[name]

    assert found["unread"] == [name]
    assert (found["values"]["ENABLED"], found["values"][const]) == (False, default)
