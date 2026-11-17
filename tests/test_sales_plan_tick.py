"""Конвейер «Плана работ» (`sales/plan_tick.py`) на настоящей базе: проход — переходы гипотез плана и
не больше одной постановки.

Проверяется то, ради чего проход: цель набрана — гипотеза шлёт письма, а следующая начинает
собирать; ключи кончились и потолки — словами; остановка прогона и отказ сметы — по виду (повтор
не чаще паузы повтора, остановка гипотезы, пауза плана); «одна вперёд»; один прогон плана за раз на
весь сервис и брошенный прогон; ключ, которого источник не отдаёт, не покупается по кругу; письма
уходят — гипотеза в конце, все в конце — план тоже; выключенный конвейер — ничего.

Смета — настоящая (`segment_estimate.estimate`, настройки — как у её тестов); очередь — подделка с
правилами rq (`test_sales_clean_api._Jobs`), Redis не трогается; прогон проходит только заведение —
его задачу исполняет `test_sales_plan_tick_run.py`. Тормоза письмам и потолку модели —
`test_sales_plan_gates.py`. Ключи — трёх ниш и письменностей, сайты выдуманы (`*.example.test`).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config import sales_plan as plan_cfg
from backend.features.core.domain import AuditAction, MessageStatus
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.outreach import MessageModel
from backend.features.sales import plan_runs, plan_tick, queue, segment_jobs
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.plan_budget import keys_words
from backend.features.sales.plan_models import (
    PlanStage,
    PlanStatus,
    SalesPlanHypothesisModel,
    SalesPlanKeywordModel,
    SalesPlanModel,
)
from backend.features.sales.plan_moves import Did, Turn
from backend.features.sales.plan_waves import Batch, Wave
from backend.features.sales.segment_estimate import NO_SEGMENT
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from backend.features.sales.segment_run import JUDGE_STOPPED
from backend.features.sales.segment_watch import LOST, Watch
from backend.features.sales.segment_window import NO_CREDITS
from backend.features.serp import funds
from backend.features.serp.funds import FundsTooLowError
from backend.shared.llm import NO_CREDITS_WORDS, Refusal, RefusalKind
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_plan_waves import NOW, make_plan, make_run, sheet, site, tally
from tests.test_sales_segment_estimate import _settings

__all__ = ["_settings"]  # смета — с настройками её тестов: продажи, выдача, модель

Run = SalesSegmentRunModel
Row = SalesPlanHypothesisModel
Key = SalesPlanKeywordModel

#: Ключи листа — ремонт кофемашин кириллицей, стоматология латиницей, мебель китайским письмом.
WORDS = ("ремонт кофемашин", "dental implants price", "家具 批发")
#: Час после прогонов-заготовок (`make_run` кончает их в `NOW`): пауза между прогонами прошла.
LATER = NOW + timedelta(hours=1)


@pytest.fixture(autouse=True)
def conveyor(monkeypatch: pytest.MonkeyPatch) -> None:
    """Конвейер включён, тормоза — по умолчанию; память ожиданий — своя у теста."""
    monkeypatch.setattr(sales_cfg, "ENABLED", True)
    for name, value in {
        "ENABLED": True,
        "KEYS_PER_RUN": 100,
        "PAUSE_MIN": 10,
        "RETRY_MIN": 30,
        "LOST_TRIES": 2,
        "CAP_SHARE": 0.5,
    }.items():
        monkeypatch.setattr(plan_cfg, name, value)
    monkeypatch.setattr(plan_tick, "SAID", plan_tick.Said())


@pytest.fixture
def jobs() -> _Jobs:
    return _Jobs()


async def collecting(
    session: AsyncSession, plan: SalesPlanModel | None = None, **changes: Any
) -> SalesPlanHypothesisModel:
    """Лист плана, который уже собирает: ключи `WORDS` на рынке `de`."""
    row = await sheet(session, plan, keys=WORDS, **changes)
    row.stage = PlanStage.COLLECTING.value
    await session.flush()
    return row


def first_wave(market: str = "de", keys: tuple[str, ...] = WORDS) -> Batch:
    return Batch(Wave(1, 1), market, keys)


async def stopped_run(
    session: AsyncSession, row: SalesPlanHypothesisModel, words: str, at: datetime = LATER
) -> SalesSegmentRunModel:
    """Прогон пачки с купленной выдачей, вставший словами `words` в `at`."""
    run = await make_run(session, row, first_wave(), report=tally())
    run.stage, run.error, run.finished_at = Stage.STOPPED.value, words, at
    await session.flush()
    return run


async def runs_of(session: AsyncSession, row: SalesPlanHypothesisModel) -> list[Run]:
    found = await session.scalars(
        select(Run).where(Run.plan_hypothesis_id == row.id).order_by(Run.id)
    )
    return list(found.all())


async def pages(session: AsyncSession, row: SalesPlanHypothesisModel) -> dict[str, int]:
    found = await session.execute(
        select(Key.keyword, Key.pages).where(Key.plan_hypothesis_id == row.id)
    )
    return dict(found.tuples().all())


async def journal(session: AsyncSession) -> list[AuditLogModel]:
    found = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.RUN_STARTED)
        .order_by(AuditLogModel.id)
    )
    return list(found.all())


async def step(session: AsyncSession, jobs: _Jobs, now: datetime = LATER) -> Turn:
    return await plan_tick.step(session, jobs, now=now)


def codes(row: SalesPlanHypothesisModel) -> list[str]:
    return [str(item["code"]) for item in row.problems]


# --- цель набрана ----------------------------------------------------------------------------


async def test_the_target_met_the_sheet_sends_and_the_next_one_starts(
    session: AsyncSession, jobs: _Jobs
) -> None:
    """Прогон дал два принятых сайта при цели 2: гипотеза — в письма, купленное — в реестр; лидов
    без письма у неё нет — следующим проходом она `done`, а следующая гипотеза начинает собирать:
    прогон её первой пачки заведён — страница 1, рынок листа, все ключи, журнал «автоматически»."""
    plan = await make_plan(session)
    first = await collecting(session, plan, target=2, max_pages=1)
    second = await sheet(session, plan, keys=WORDS)
    run = await make_run(session, first, first_wave(), report=tally())
    await site(session, run, WORDS[0])
    await site(session, run, WORDS[1])

    moved = await step(session, jobs)

    assert moved == Turn(Did.MOVED, plan_tick.TARGET_MET)
    assert (first.stage, first.found, first.counted_at) == ("sending", 2, LATER)
    assert set((await pages(session, first)).values()) == {1}
    assert (second.stage, jobs.enqueued) == ("waiting", [])

    started = await step(session, jobs)

    [new] = await runs_of(session, second)
    assert started == Turn(Did.LAUNCHED, run_id=new.id)
    assert (first.stage, first.finished_at) == ("done", LATER)
    assert (second.stage, second.started_at, second.wave_page) == ("collecting", LATER, 1)
    assert (new.from_page, new.depth_pages, new.country, new.language) == (1, 1, "de", "en")
    assert list(new.keywords) == list(WORDS)
    assert (new.estimate["serp_mode"], new.estimate["judge_tier"]) == ("standard", "flex")
    [(args, options)] = jobs.enqueued
    assert (args, options["job_id"]) == ((SEGMENT_JOB, new.id), segment_job_id(new.id))
    [entry] = await journal(session)
    assert entry.user_id is None
    assert entry.details is not None
    assert entry.details["гипотеза плана"] == second.id


async def test_a_running_plan_run_holds_every_plan_and_only_its_numbers_move(
    session: AsyncSession, jobs: _Jobs
) -> None:
    """Один прогон плана за раз на весь сервис: идёт прогон одного плана — у другого плана гипотеза
    ждёт, а проход только пересчитывает «найдено» идущего (очередь не отвечает — «идёт»)."""
    busy = await collecting(session)
    going = await make_run(session, busy, first_wave())
    going.stage, going.finished_at = Stage.JUDGE.value, None
    await site(session, going, WORDS[0])
    other = await sheet(session, keys=WORDS)

    turn = await step(session, jobs)

    assert turn == Turn(Did.GOING, run_id=going.id)
    assert (busy.found, busy.counted_at) == (1, LATER)
    assert (other.stage, jobs.enqueued) == ("waiting", [])


# --- нужны ключи, потолки ------------------------------------------------------------------------


async def test_keys_and_depth_ran_out_the_sheet_stops_with_words(
    session: AsyncSession, jobs: _Jobs
) -> None:
    row = await collecting(session, max_pages=1, target=40)
    await session.execute(update(Key).where(Key.plan_hypothesis_id == row.id).values(pages=1))

    turn = await step(session, jobs)

    assert turn == Turn(Did.MOVED, plan_runs.NO_KEYS)
    assert row.stage == "stopped"
    [found] = row.problems
    assert (found["code"], found["words"]) == ("keys", keys_words(1, 0, 40))
    assert jobs.enqueued == []


async def test_the_hypothesis_cap_stops_the_sheet_before_the_run(
    session: AsyncSession, jobs: _Jobs
) -> None:
    row = await collecting(session, cap=Decimal("0.01"))

    await step(session, jobs)

    assert (row.stage, codes(row)) == ("stopped", ["budget"])
    assert str(row.problems[0]["words"]).startswith(
        f"потолок гипотезы {funds.dollars(0.01)}: израсходовано"
    )
    assert (await runs_of(session, row), jobs.enqueued) == ([], [])


async def test_the_plan_cap_pauses_the_plan_in_words(session: AsyncSession, jobs: _Jobs) -> None:
    plan = await make_plan(session, cap=Decimal("0.02"))
    row = await collecting(session, plan)

    await step(session, jobs)

    assert plan.status == "paused"
    assert (plan.problem or "").startswith(f"потолок плана {funds.dollars(0.02)}: израсходовано")
    assert (row.stage, row.problems, await runs_of(session, row)) == ("collecting", [], [])


# --- остановка прогона -----------------------------------------------------------------------


async def test_a_temporary_stop_is_resumed_not_sooner_than_the_retry_pause(
    session: AsyncSession, jobs: _Jobs
) -> None:
    """Судья встал: у модели кончились кредиты. Гипотеза собирает дальше с проблемой словами
    прогона; тот же прогон продолжается не раньше паузы повтора — без новой выдачи, журналом
    «автоматически», и проблема снята."""
    row = await collecting(session, max_pages=1)
    words = f"{JUDGE_STOPPED}{NO_CREDITS}"
    run = await stopped_run(session, row, words)

    early = await step(session, jobs, LATER + timedelta(minutes=29))

    assert early.did is Did.WAITED
    assert (early.why or "").startswith("повтор после сбоя — не раньше")
    assert row.stage == "collecting"
    assert row.problems == [{"code": "run_failed", "words": words, "at": LATER.isoformat()}]
    assert jobs.enqueued == []

    resumed = await step(session, jobs, LATER + timedelta(minutes=31))

    assert resumed == Turn(Did.RESUMED, run_id=run.id)
    [(args, options)] = jobs.enqueued
    assert (args, options["job_id"]) == ((SEGMENT_JOB, run.id), segment_job_id(run.id))
    assert (run.finished_at, run.job_id, row.problems) == (None, segment_job_id(run.id), [])
    assert len(await runs_of(session, row)) == 1
    [entry] = await journal(session)
    assert entry.details is not None
    assert entry.details["действие"] == plan_runs.RESUMED


async def test_a_sheet_stop_stops_the_sheet_and_the_conveyor_goes_on(
    session: AsyncSession, jobs: _Jobs
) -> None:
    """Описание сегмента стёрли посреди прогона — гипотеза `stopped` словами прогона; следующая
    гипотеза плана начинает собирать: остановленная — тоже конец."""
    plan = await make_plan(session)
    row = await collecting(session, plan)
    words = f"прогон остановлен: {NO_SEGMENT}"
    await stopped_run(session, row, words, NOW)
    second = await sheet(session, plan, keys=WORDS)

    turn = await step(session, jobs)

    assert turn == Turn(Did.MOVED, words)
    assert (row.stage, codes(row)) == ("stopped", ["run_failed"])
    assert (await step(session, jobs)).did is Did.LAUNCHED
    assert second.stage == "collecting"


async def test_a_service_stop_pauses_the_plan_and_resuming_the_plan_resumes_the_run(
    session: AsyncSession, jobs: _Jobs
) -> None:
    """Выдача упёрлась в счёт — встало бы у каждой гипотезы: план на паузу словами прогона, гипотеза
    не тронута. Человек снял паузу — тот же прогон продолжается, а не новый."""
    plan = await make_plan(session)
    row = await collecting(session, plan)
    words = segment_jobs.stop_words(FundsTooLowError(funds.refusal(1.0, 0.1) or ""))
    run = await stopped_run(session, row, words, NOW)

    paused = await step(session, jobs)

    assert paused == Turn(Did.MOVED, words)
    assert (plan.status, plan.problem) == ("paused", words)
    assert (row.stage, codes(row)) == ("collecting", ["run_failed"])
    assert (await step(session, jobs)).did is Did.NOTHING

    plan.status, plan.problem = PlanStatus.RUNNING.value, None
    await session.flush()

    assert await step(session, jobs) == Turn(Did.RESUMED, run_id=run.id)
    assert len(await runs_of(session, row)) == 1


async def test_a_run_without_its_job_is_resumed_and_of_a_paused_plan_holds_nobody(
    session: AsyncSession, jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Воркер умер посреди прогона: строка «идёт», а задачи нет. Идёт план — проход продолжает
    прогон; план на паузе — брошенный прогон ждёт человека и не держит другие планы."""

    def lost(run_id: int, stage: str, *, workers: int | None) -> Watch:
        return Watch(going=False, stalled=LOST, workers=workers)

    monkeypatch.setattr(plan_tick, "watch", lost)
    row = await collecting(session)
    run = await make_run(session, row, first_wave())
    run.stage, run.finished_at = Stage.JUDGE.value, None
    await session.flush()

    assert await step(session, jobs) == Turn(Did.RESUMED, run_id=run.id)
    assert [args for args, _ in jobs.enqueued] == [(SEGMENT_JOB, run.id)]

    plan = await session.get(SalesPlanModel, row.plan_id)
    assert plan is not None
    plan.status = PlanStatus.PAUSED.value
    other = await sheet(session, keys=WORDS)
    await session.flush()

    turn = await step(session, jobs)

    [launched] = await runs_of(session, other)
    assert turn == Turn(Did.LAUNCHED, run_id=launched.id)


# --- отказ сметы -----------------------------------------------------------------------------


async def test_a_model_cap_refusal_waits_for_the_retry_pause_without_a_run(
    session: AsyncSession, jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Смета выше остатка дневного потолка модели — временное: гипотеза собирает дальше с проблемой
    словами сметы, прогона нет; до паузы повтора смета не считается заново."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 1000)
    row = await collecting(session)

    turn = await step(session, jobs)

    assert turn.did is Did.WAITED
    assert (turn.why or "").startswith("смета модели выше остатка дневного потолка")
    [found] = row.problems
    assert found["code"] == "run_failed"
    assert str(found["words"]).startswith(f"{plan_runs.NOT_PUT}смета модели выше остатка")
    assert (row.stage, await runs_of(session, row)) == ("collecting", [])

    async def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("смета посчитана до паузы повтора")

    monkeypatch.setattr(plan_runs, "estimate", forbidden)
    again = await step(session, jobs, LATER + timedelta(minutes=10))
    assert (again.why or "").startswith("повтор после сбоя")


async def test_an_erased_segment_refusal_stops_the_sheet(
    session: AsyncSession, jobs: _Jobs
) -> None:
    row = await collecting(session)
    hypothesis = await session.get(SalesHypothesisModel, row.hypothesis_id)
    assert hypothesis is not None
    hypothesis.description = None
    await session.flush()

    await step(session, jobs)

    assert (row.stage, codes(row)) == ("stopped", ["run_failed"])
    assert str(row.problems[0]["words"]) == f"{plan_runs.NOT_PUT}{NO_SEGMENT}"


@pytest.mark.parametrize("why", ["funds", "model"])
async def test_a_service_refusal_pauses_the_plan_in_the_estimate_words(
    session: AsyncSession, jobs: _Jobs, monkeypatch: pytest.MonkeyPatch, why: str
) -> None:
    """Счёт выдачи меньше порога и модель без кредитов — у сервиса: план на паузу словами сметы."""

    async def low(_ahrefs: object) -> float:
        return 1.0

    async def broke(*_args: object, **_kwargs: object) -> Refusal:
        return Refusal(RefusalKind.NO_CREDITS, "insufficient_quota", permanent=True, status=429)

    if why == "funds":
        monkeypatch.setattr("backend.features.serp.funds.provider_balance", low)
    else:
        monkeypatch.setattr("backend.shared.llm.probe", broke)
    plan = await make_plan(session)
    row = await collecting(session, plan)

    await step(session, jobs)

    assert plan.status == "paused"
    if why == "funds":
        assert plan.problem == funds.refusal(1.0, 0.0)
    else:
        assert plan.problem == NO_CREDITS_WORDS
    assert (row.stage, row.problems, await runs_of(session, row)) == ("collecting", [], [])


# --- одна вперёд -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("stages", "turn"),
    [
        (("sending", "waiting"), 1),
        (("sending", "sending", "waiting"), None),
        (("done", "sending", "waiting"), 2),
        (("stopped", "sending", "waiting"), 2),
        (("collecting", "waiting"), 0),
        (("waiting", "waiting"), 0),
    ],
    ids=["one-ahead", "k+2-waits-for-k", "k-done", "k-stopped", "collecting-first", "in-order"],
)
async def test_one_sheet_collects_and_one_more_may_run_ahead(
    session: AsyncSession,
    jobs: _Jobs,
    monkeypatch: pytest.MonkeyPatch,
    stages: tuple[str, ...],
    turn: int | None,
) -> None:
    """Гипотезы в письмах ещё шлют их: следующая начинает, пока впереди не больше одной в
    `sending`; собирающая — первая; ждущие — по порядку листов."""

    async def letters(*_args: object, **_kwargs: object) -> bool:
        return True

    monkeypatch.setattr(plan_tick, "letters_left", letters)
    plan = await make_plan(session)
    rows = [await sheet(session, plan, keys=WORDS) for _ in stages]
    for row, stage in zip(rows, stages, strict=True):
        row.stage = stage
    await session.flush()

    await step(session, jobs)

    launched = [index for index, row in enumerate(rows) if len(await runs_of(session, row)) == 1]
    assert launched == ([] if turn is None else [turn])
    assert [row.stage for index, row in enumerate(rows) if index != turn] == [
        stage for index, stage in enumerate(stages) if index != turn
    ]


# --- письма и конец плана --------------------------------------------------------------------


async def test_a_sending_sheet_is_done_when_its_first_letters_are_gone(
    session: AsyncSession, jobs: _Jobs, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Гипотеза ждёт, пока лиду «готов» сборка напишет первое письмо и пока оно в очереди; ушло —
    первые письма ушли, гипотеза `done`."""
    world = await w.world(session, monkeypatch)
    plan = await make_plan(session)
    row = Row(
        plan_id=plan.id,
        hypothesis_id=world.hypothesis_id,
        position=1,
        sheet="Лист писем",
        markets=["de"],
        language="en",
        target=1,
        max_pages=1,
        cap_usd=Decimal("5.00"),
        stage=PlanStage.SENDING.value,
    )
    session.add(row)
    await w.lead(session, world.hypothesis_id, "jane@acme.example.test")

    await step(session, jobs)
    assert row.stage == "sending"

    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=1)
    await step(session, jobs)
    assert row.stage == "sending"

    letter = await session.scalar(select(MessageModel))
    assert letter is not None
    letter.status = MessageStatus.SENT
    await session.flush()

    assert await step(session, jobs) == Turn(Did.MOVED, plan_tick.PLAN_DONE)
    assert (row.stage, row.finished_at, plan.status, plan.finished_at) == (
        "done",
        LATER,
        "done",
        LATER,
    )


# --- потерянный ключ -------------------------------------------------------------------------


async def test_a_key_the_source_keeps_losing_is_given_up_after_the_limit(
    session: AsyncSession, jobs: _Jobs
) -> None:
    """Источник не отдал ключ: заплачено, а не пришло. Один раз — ключ идёт в следующую пачку той
    же волны; второй — отработан, и волна кончилась: «нужны ключи», а не покупка по кругу."""
    row = await collecting(session, max_pages=1)
    await make_run(session, row, first_wave(), report=tally(lost=[WORDS[2]]))

    first = await step(session, jobs)

    [_, again] = await runs_of(session, row)
    assert first == Turn(Did.LAUNCHED, run_id=again.id)
    assert list(again.keywords) == [WORDS[2]]
    assert await pages(session, row) == {WORDS[0]: 1, WORDS[1]: 1, WORDS[2]: 0}

    again.stage, again.report = Stage.DONE.value, tally(lost=[WORDS[2]])
    again.finished_at = LATER + timedelta(minutes=5)
    await session.flush()

    second = await step(session, jobs, LATER + timedelta(minutes=30))

    assert second == Turn(Did.MOVED, plan_runs.NO_KEYS)
    assert await pages(session, row) == {WORDS[0]: 1, WORDS[1]: 1, WORDS[2]: 1}
    assert (row.stage, codes(row), len(jobs.enqueued)) == ("stopped", ["keys"], 1)


# --- выключатели -----------------------------------------------------------------------------


@pytest.mark.parametrize(("sales_on", "plan_on"), [(True, False), (False, True)])
async def test_switched_off_the_conveyor_does_nothing(
    monkeypatch: pytest.MonkeyPatch, sales_on: bool, plan_on: bool
) -> None:
    called: list[datetime] = []

    async def forbidden(*_args: object, now: datetime) -> Turn:
        called.append(now)
        raise AssertionError("проход конвейера при выключенном тумблере")

    monkeypatch.setattr(plan_tick, "step", forbidden)
    monkeypatch.setattr(sales_cfg, "ENABLED", sales_on)
    monkeypatch.setattr(plan_cfg, "ENABLED", plan_on)

    await plan_tick.tick()

    assert called == []


def test_the_waiting_line_is_said_once_per_reason(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger=plan_tick.logger.name)
    said = plan_tick.Said()

    for why in ("пауза", "пауза", "письма", "письма"):
        said.tell(Turn(Did.WAITED, why))
    said.tell(Turn(Did.LAUNCHED, run_id=7))
    said.tell(Turn(Did.WAITED, "пауза"))

    lines = [record for record in caplog.records if record.name == plan_tick.logger.name]
    assert [getattr(record, "why", None) for record in lines] == ["пауза", "письма", "пауза"]


async def test_a_moment_from_the_past_is_now_for_the_numbers(session: AsyncSession) -> None:
    """Время прохода — до чтения: прогон, кончившийся посреди прохода, — новый для следующего."""
    row = await collecting(session)
    run = await make_run(session, row, first_wave())

    assert plan_tick._fresh(row, run)
    row.counted_at = datetime(2026, 11, 17, 9, 0, 1, tzinfo=UTC)
    assert not plan_tick._fresh(row, run)
