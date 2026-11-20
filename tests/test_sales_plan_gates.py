"""Тормоза конвейера «Плана работ» (`sales/plan_gates.py`) — через проход конвейера.

Прогон плана не ставится, пока в очереди долгих задач продаж стоит или идёт сборка очереди или пачка
писем продаж, пока включённой автосборке нужна сборка и есть кого собрать, пока судья сегодня и смета
прогона выше доли конвейера в дневном потолке модели продаж и пока не кончилась пауза после прошлого
прогона плана. Кончилась причина — прогон заводится тем же проходом. Очередь — подделка с правилами
rq (`test_sales_clean_api._Jobs`), смета — настоящая; сайты и ключи выдуманы.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.api.letters import once
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.features.core import usage
from backend.features.core.domain import Stage
from backend.features.sales import autobuild, plan_gates, plan_runs
from backend.features.sales.autobuild import Plan
from backend.features.sales.plan_gates import BUILD_GOING, LETTERS_GOING, QUEUE_SILENT, SEND_JOB_ID
from backend.features.sales.plan_moves import Did
from backend.features.sales.queue_jobs import QUEUE_JOB
from backend.features.sales.queue_start import build_job_id
from backend.features.sales.segment_judge import OPERATION as JUDGE
from backend.features.sales.segment_models import Stage as RunStage
from backend.features.sales.segment_spend import number
from backend.shared.queue import SEND_QUEUE_JOB
from redis.exceptions import ConnectionError as RedisConnectionError
from rq.job import JobStatus
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_clean_api import _Job, _Jobs
from tests.test_sales_plan_tick import (
    LATER,
    WORDS,
    collecting,
    conveyor,
    first_wave,
    jobs,
    runs_of,
    step,
)
from tests.test_sales_plan_waves import make_run
from tests.test_sales_segment_estimate import _settings

__all__ = ["_settings", "conveyor", "jobs"]  # оснастка — та же, что у тестов прохода


class _Silent(_Jobs):
    """Очередь задач не отвечает."""

    def fetch_job(self, job_id: str) -> _Job | None:
        raise RedisConnectionError("очередь не отвечает")


def test_the_send_batch_number_is_the_one_the_mailing_screen_puts() -> None:
    assert once.send_job_id(Stage.SALES, "links") == SEND_JOB_ID


# --- письма первыми --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("status", "held"),
    [
        (JobStatus.QUEUED, BUILD_GOING),
        (JobStatus.STARTED, BUILD_GOING),
        (JobStatus.SCHEDULED, BUILD_GOING),
        (JobStatus.FINISHED, None),
        (JobStatus.FAILED, None),
    ],
)
async def test_a_queued_or_running_build_holds_the_run_and_a_finished_one_does_not(
    session: AsyncSession, jobs: _Jobs, status: JobStatus, held: str | None
) -> None:
    """Сборка очереди любой гипотезы продаж стоит, идёт или ждёт повтора — прогон плана после неё;
    кончилась — прогон заводится."""
    row = await collecting(session)
    built = jobs.enqueue(QUEUE_JOB, row.hypothesis_id, 20, job_id=build_job_id(row.hypothesis_id))
    built.status = status

    turn = await step(session, jobs)

    if held is None:
        assert turn.did is Did.LAUNCHED
    else:
        assert (turn.did, turn.why, await runs_of(session, row)) == (Did.WAITED, held, [])


async def test_a_send_batch_holds_the_run(session: AsyncSession, jobs: _Jobs) -> None:
    row = await collecting(session)
    batch = jobs.enqueue(SEND_QUEUE_JOB, Stage.SALES.value, job_id=SEND_JOB_ID)
    batch.status = JobStatus.STARTED

    turn = await step(session, jobs)

    assert (turn.did, turn.why, await runs_of(session, row)) == (Did.WAITED, LETTERS_GOING, [])


async def test_a_silent_queue_holds_the_run(session: AsyncSession) -> None:
    """Очередь не ответила — идут ли письма, не узнать: неизвестность — не «свободно»."""
    row = await collecting(session)

    turn = await step(session, _Silent())

    assert (turn.did, turn.why, await runs_of(session, row)) == (Did.WAITED, QUEUE_SILENT, [])


@pytest.mark.parametrize("wanted", [True, False], ids=["needs-a-build", "nothing-to-build"])
async def test_the_run_waits_while_the_autobuild_has_someone_to_build(
    session: AsyncSession, jobs: _Jobs, monkeypatch: pytest.MonkeyPatch, wanted: bool
) -> None:
    """Автосборке не хватает писем на ближайший день отправки и есть кого собрать — прогон плана
    после её сборки. Собрать некого (лиды кончились) — недостачу и закрывает сбор: прогон идёт."""
    seen: list[datetime] = []

    async def plan(_session: AsyncSession, _jobs: object, *, now: datetime) -> Plan:
        seen.append(now)
        if wanted:
            return Plan(need=20, have=3, hypothesis_id=11, name="Другая гипотеза", day=now.date())
        return Plan(need=20, have=3, why=autobuild.NO_CANDIDATES, day=now.date())

    monkeypatch.setattr(sales_cfg, "AUTOBUILD", True)
    monkeypatch.setattr(plan_gates.autobuild, "plan", plan)
    row = await collecting(session)

    turn = await step(session, jobs)

    assert seen == [LATER]
    if wanted:
        assert turn.did is Did.WAITED
        assert turn.why == (
            "автосборке нужна сборка гипотезы «Другая гипотеза» на 17.11: писем в очереди 3 из "
            "20 — прогон плана после неё"
        )
        assert await runs_of(session, row) == []
    else:
        assert turn.did is Did.LAUNCHED


async def test_a_switched_off_autobuild_is_not_asked(
    session: AsyncSession, jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def forbidden(*_args: object, **_kwargs: object) -> Plan:
        raise AssertionError("план автосборки спрошен при выключенной автосборке")

    monkeypatch.setattr(plan_gates.autobuild, "plan", forbidden)
    await collecting(session)

    assert (await step(session, jobs)).did is Did.LAUNCHED


# --- доля потолка модели ---------------------------------------------------------------------


async def test_the_judge_and_the_run_above_the_share_wait_for_the_retry_pause(
    session: AsyncSession, jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Потолок модели продаж 1 000 000 токенов в сутки, доля конвейера — половина. Судья сегодня
    потратил 450 000, смета прогона — 93 600: остатка потолка хватает, но вместе больше доли — прогон
    не ставится, гипотеза ждёт паузы повтора с проблемой словами доли."""
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 1_000_000)
    usage.record(session, operation=JUDGE, units=450_000)
    row = await collecting(session)
    now = datetime.now(UTC)

    turn = await step(session, jobs, now)

    assert turn.did is Did.WAITED
    assert (turn.why or "").startswith(
        f"доля конвейера в дневном потолке модели продаж — {number(500_000)} токенов: судья "
        f"сегодня потратил {number(450_000)}, смета прогона ≈ {number(93_600)} — сбор продолжится "
        "после сброса потолка"
    )
    [found] = row.problems
    assert (found["code"], found["words"]) == ("run_failed", plan_runs.NOT_PUT + turn.why)
    assert await runs_of(session, row) == []


async def test_a_spent_share_holds_without_an_estimate(
    session: AsyncSession, jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Доля выбрана целиком — держит и без сметы: смета спросила бы модель проверкой."""

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("смета посчитана при выбранной доле")

    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 1_000_000)
    monkeypatch.setattr(plan_runs, "estimate", forbidden)
    usage.record(session, operation=JUDGE, units=500_000)
    row = await collecting(session)

    turn = await step(session, jobs, datetime.now(UTC))

    assert turn.did is Did.WAITED
    assert (turn.why or "").startswith("доля конвейера в дневном потолке модели продаж")
    assert (row.problems, await runs_of(session, row)) == ([], [])


async def test_the_share_counts_the_judge_and_not_the_letters(
    session: AsyncSession, jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Письма продаж сегодня потратили 600 000 из потолка продаж в 1 000 000: остатка прогону
    хватает, а доля конвейера — про судью, и судья сегодня не тратил."""
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 1_000_000)
    usage.record(session, operation="sales_letter_rewrite", units=600_000)
    await collecting(session)

    assert (await step(session, jobs, datetime.now(UTC))).did is Did.LAUNCHED


# --- пауза между прогонами -------------------------------------------------------------------


async def test_the_next_run_waits_for_the_pause_after_the_last_plan_run(
    session: AsyncSession, jobs: _Jobs
) -> None:
    """Прогон плана — любого — кончился пять минут назад: следующий — после паузы в десять."""
    other = await collecting(session)
    done = await make_run(session, other, first_wave())
    done.stage, done.finished_at = RunStage.DONE.value, LATER - timedelta(minutes=5)
    row = await collecting(session)
    await session.flush()

    held = await step(session, jobs)

    assert held.did is Did.WAITED
    assert (held.why or "").startswith("пауза между прогонами плана — до 17.11 13:05 по Москве")
    assert jobs.enqueued == []

    started = await step(session, jobs, LATER + timedelta(minutes=6))

    # Первым — план, у которого прогонов ещё не было: прогон другого кончился недавно.
    [run] = await runs_of(session, row)
    assert (started.did, started.run_id) == (Did.LAUNCHED, run.id)
    assert list(run.keywords) == list(WORDS)
