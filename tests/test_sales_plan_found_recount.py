"""«Найдено» гипотезы плана работ — сразу после решения человека, а не на следующем проходе.

Стенд 19.11: человек принял сайт прогона плана и отменил решение, а экран и API плана показывали
прежнее число до следующего прохода конвейера, у остановленной гипотезы — насовсем: проход её не
считал. Проверяется: «Принять» и «Отменить решение» маршрутами — число в API плана той же
транзакцией, а время взгляда прохода (`counted_at`) не тронуто; решение по прогону экрана «Сбор
базы» той же гипотезы число плана не трогает; пересуд прогона плана — на своём чекпоинте; проход
считает у гипотез в конце найдено и расход, а остановленную, набравшую цель, в письма не
переводит; два решения по одной гипотезе плана сразу — второе считает после первого; строку
гипотезы плана держат дольше короткого предела — пересчёт пропущен строкой журнала, а отмена
проходит, и её собственный предел ожидания замков — прежний. Сайты выдуманы (`*.example.test`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.config.judge import JudgeMode
from backend.features.core.models.access import UserModel
from backend.features.sales import plan_found, plan_tick, segment_report
from backend.features.sales import segment_rejudge as sr
from backend.features.sales import segment_undo as su
from backend.features.sales.plan_models import (
    PlanStage,
    PlanStatus,
    SalesPlanHypothesisModel,
    SalesPlanModel,
)
from backend.features.sales.plan_moves import Did, Turn
from backend.features.sales.plan_waves import Batch, Wave
from backend.features.sales.segment_models import Lane, SalesSegmentCandidateModel
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_hypothesis_edit import _waits_on_a_lock
from tests.test_sales_plan_waves import KEYS, bought, judged, make_plan, make_run, sheet, take
from tests.test_sales_segment_rejudge import NARROWER, NO_PARTS, PARTS, asked, deps, describe
from tests.test_sales_segment_rejudge import site as judged_before
from tests.test_sales_segment_run import Model, hypothesis
from tests.test_sales_segment_run import make_run as screen_run
from tests.test_send_race import MEETING, committed_sessions

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

Row = SalesPlanHypothesisModel
Candidate = SalesSegmentCandidateModel
SELLER = "seller@ours.example.test"
CANDIDATES = "/api/sales/segment/candidates"
#: Последний взгляд прохода на гипотезу — до решения человека: решение его не трогает.
LOOKED = datetime(2026, 11, 19, 15, 0, tzinfo=UTC)
LATER = LOOKED + timedelta(hours=1)


@pytest.fixture
async def headers(make_user: MakeUser, sign_in: SignIn) -> dict[str, str]:
    await make_user(SELLER)
    return bearer(await sign_in(SELLER))


async def _found_in_api(
    client: AsyncClient, headers: dict[str, str], plan: SalesPlanModel, row: Row
) -> int:
    """«Найдено» гипотезы плана — как его отдаёт API плана экрану."""
    response = await client.get(f"/api/sales/plans/{plan.id}", headers=headers)
    assert response.status_code == 200, response.text
    [found] = [one["found"] for one in response.json()["hypotheses"] if one["id"] == row.id]
    return int(found)


# --- решение и отмена маршрутами ----------------------------------------------------------------


async def test_accept_and_undo_on_a_stopped_sheet_move_found_in_the_plan_api_at_once(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Случай стенда: гипотезу остановили после прогона — два сайта принял судья, один ждёт
    человека. «Принять» — в API плана 3, «Отменить решение» — снова 2, без прохода конвейера; этап
    и время взгляда прохода — прежние."""
    plan = await make_plan(session)
    row = await sheet(session, plan, target=3)
    run = await bought(session, row, await take(session, row))
    await judged(session, run, Lane.ACCEPT)
    await judged(session, run, Lane.ACCEPT)
    review = await judged(session, run, Lane.REVIEW)
    row.stage, row.found, row.counted_at = PlanStage.STOPPED.value, 2, LOOKED
    await session.commit()
    url = f"{CANDIDATES}/{review.id}/decision"

    accepted = await client.post(url, json={"accept": True}, headers=headers)
    after_accept = await _found_in_api(client, headers, plan, row)
    undone = await client.delete(url, headers=headers)
    after_undo = await _found_in_api(client, headers, plan, row)

    assert accepted.status_code == 200, accepted.text
    assert undone.status_code == 200, undone.text
    assert (accepted.json()["lane"], undone.json()["lane"]) == ("accept", "review")
    assert (after_accept, after_undo) == (3, 2)
    await session.refresh(row)
    assert (row.stage, row.counted_at) == ("stopped", LOOKED)


async def test_a_decision_on_a_screen_run_of_the_same_hypothesis_leaves_the_plan_number(
    session: AsyncSession, client: AsyncClient, headers: dict[str, str]
) -> None:
    """Прогон экрана «Сбор базы» той же гипотезы в «найдено» плана не входит — и решение по его
    сайту число плана не меняет."""
    plan = await make_plan(session)
    row = await sheet(session, plan)
    own = await bought(session, row, await take(session, row))
    await judged(session, own, Lane.ACCEPT)
    manual = await make_run(session, row, Batch(Wave(1, 1), "de", KEYS[:1]), own=False)
    review = await judged(session, manual, Lane.REVIEW)
    row.stage, row.found = PlanStage.COLLECTING.value, 1
    await session.commit()

    accepted = await client.post(
        f"{CANDIDATES}/{review.id}/decision", json={"accept": True}, headers=headers
    )

    assert accepted.status_code == 200, accepted.text
    assert await _found_in_api(client, headers, plan, row) == 1


# --- пересуд прогона плана ------------------------------------------------------------------------


async def test_a_rejudge_of_a_plan_run_recounts_found_at_its_checkpoint(
    session: AsyncSession,
) -> None:
    """Пересуд после правки описания отсёк принятый магазин запчастей — у остановленной гипотезы
    плана «найдено» 0 тем же чекпоинтом, а время взгляда прохода — прежнее."""
    owner = await hypothesis(session)
    run = await screen_run(session, owner, mode=JudgeMode.ENFORCE)
    plan = await make_plan(session)
    row = Row(
        plan_id=plan.id,
        hypothesis_id=owner.id,
        position=1,
        sheet="Лист 1",
        markets=["us"],
        language="en",
        target=5,
        max_pages=1,
        cap_usd=Decimal("5.00"),
        stage=PlanStage.STOPPED.value,
        found=1,
        counted_at=LOOKED,
    )
    session.add(row)
    await session.flush()
    run.plan_hypothesis_id = row.id
    await session.commit()
    parts = await judged_before(session, run, PARTS, lane=Lane.ACCEPT)
    await describe(session, owner, NARROWER)
    await asked(session, run)

    await sr.rejudge(session, run.id, deps(Model({PARTS: NO_PARTS})))

    await session.refresh(row)
    assert (parts.lane, row.found, row.counted_at) == (Lane.REJECT, 0, LOOKED)


# --- проход: гипотезы в конце ---------------------------------------------------------------------


async def test_the_pass_counts_sheets_at_the_end_and_a_stopped_one_at_target_stays_stopped(
    session: AsyncSession,
) -> None:
    """Прогон дошёл после «Остановить»: проход последний раз считал гипотезу, пока судья ещё не
    кончил и выдача шла в резерве. Теперь проход считает и остановленную, и готовую — найдено и
    расход, без времени счёта; остановленная, набравшая цель, остаётся остановленной: «цель
    набрана → письма» — у гипотезы, чей ход."""
    plan = await make_plan(session)
    stopped = await sheet(session, plan, target=2)
    done = await sheet(session, plan)
    late = await bought(session, stopped, await take(session, stopped))
    late.report = {**late.report, "serp": {**late.report["serp"], "cost_usd": 0.25}}
    await judged(session, late, Lane.ACCEPT)
    await judged(session, late, Lane.ACCEPT)
    sent = await bought(session, done, await take(session, done))
    await judged(session, sent, Lane.ACCEPT)
    stopped.stage, stopped.found, stopped.counted_at = PlanStage.STOPPED.value, 1, LOOKED
    stopped.spent_usd, stopped.reserved_usd = Decimal("0.1000"), Decimal("0.4000")
    done.stage, done.found = PlanStage.DONE.value, 0
    await session.commit()

    turn = await plan_tick.step(session, _Jobs(), now=LATER)

    assert (stopped.found, stopped.stage, stopped.counted_at) == (2, "stopped", LOOKED)
    assert (stopped.spent_usd, stopped.reserved_usd) == (Decimal("0.2500"), Decimal(0))
    assert (done.found, done.stage) == (1, "done")
    assert (turn, plan.status) == (Turn(Did.MOVED, plan_tick.PLAN_DONE), PlanStatus.DONE)


async def test_the_pass_counts_a_stopped_sheet_also_when_the_collecting_one_moves(
    session: AsyncSession,
) -> None:
    """Собирающая набрала цель — проход переводит её в письма тем же правилом, что и прежде, и в
    том же проходе считает остановленную: её строка — под замком только перед коммитом."""
    plan = await make_plan(session)
    collecting = await sheet(session, plan, target=1)
    stopped = await sheet(session, plan)
    run = await bought(session, collecting, await take(session, collecting))
    await judged(session, run, Lane.ACCEPT)
    late = await bought(session, stopped, await take(session, stopped))
    await judged(session, late, Lane.ACCEPT)
    collecting.stage = PlanStage.COLLECTING.value
    stopped.stage, stopped.found, stopped.counted_at = PlanStage.STOPPED.value, 0, LOOKED
    await session.commit()

    turn = await plan_tick.step(session, _Jobs(), now=LATER)

    assert turn == Turn(Did.MOVED, plan_tick.TARGET_MET)
    assert (collecting.stage, collecting.found) == ("sending", 1)
    assert (stopped.stage, stopped.found, stopped.counted_at) == ("stopped", 1, LOOKED)


# --- гонки: на настоящих фиксациях ---------------------------------------------------------------


async def _committed_reviews(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int, int]:
    """Остановленная гипотеза плана, её прогон и два сайта «на проверке» — зафиксированы."""
    async with factory() as session:
        row = await sheet(session)
        run = await bought(session, row, await take(session, row))
        one = await judged(session, run, Lane.REVIEW)
        two = await judged(session, run, Lane.REVIEW)
        row.stage = PlanStage.STOPPED.value
        await session.commit()
        return row.id, one.id, two.id


async def _accept(factory: async_sessionmaker[AsyncSession], site_id: int) -> None:
    async with factory() as session:
        await segment_report.decide(session, site_id, accept=True, author=SELLER)
        await session.commit()


async def _state(
    factory: async_sessionmaker[AsyncSession], row_id: int, site_id: int
) -> tuple[int | None, str | None]:
    async with factory() as session:
        found = await session.scalar(select(Row.found).where(Row.id == row_id))
        lane = await session.scalar(select(Candidate.lane).where(Candidate.id == site_id))
        return found, lane


async def test_two_decisions_on_one_sheet_at_once_count_one_after_the_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Два «Принять» по сайтам одной гипотезы плана сразу: короткий предел их выстраивает —
    второе ждёт замка строки гипотезы и считает после первого, в числе оба. Счёт внутри одного
    `UPDATE` смотрел бы на базу до ожидания и затёр бы первое решение."""
    # Проверяется порядок, а не предел: раннер CI медленнее машины в разы, и коммит первого
    # решения не должен опоздать за полторы секунды. Пропуск по пределу — тест ниже.
    monkeypatch.setattr(plan_found, "LOCK_WAIT", "SET LOCAL lock_timeout = '5s'")
    async with committed_sessions() as factory:
        row_id, one, two = await _committed_reviews(factory)
        async with factory() as first:
            await segment_report.decide(first, one, accept=True, author=SELLER)
            second = asyncio.create_task(_accept(factory, two))
            assert await _waits_on_a_lock(factory)
            await first.commit()
        await asyncio.wait_for(second, MEETING)

        assert (await _state(factory, row_id, two)) == (2, Lane.ACCEPT)


async def test_a_held_sheet_does_not_hold_the_undo_and_the_pass_counts_it_later(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Строку гипотезы плана держит проход конвейера дольше короткого предела: отмена не ждёт её и
    не отказывает — решение снято, «найдено» прежнее, пропуск — строкой журнала с номером строки;
    предел замков отмены после пересчёта — её собственный. Следующий проход досчитывает число."""
    monkeypatch.setattr(plan_found, "LOCK_WAIT", "SET LOCAL lock_timeout = '200ms'")
    caplog.set_level(logging.INFO, logger=plan_found.__name__)
    async with committed_sessions() as factory:
        row_id, one, _ = await _committed_reviews(factory)
        await _accept(factory, one)
        async with factory() as holding:
            await holding.execute(select(Row.id).where(Row.id == row_id).with_for_update())
            async with factory() as session:
                undone = await asyncio.wait_for(su.undo(session, one), MEETING)
                limit = await session.scalar(text("SELECT current_setting('lock_timeout')"))
                await session.commit()
            await holding.rollback()

        assert undone is not None
        assert (undone.was, limit) == (Lane.ACCEPT, "5s")
        assert (await _state(factory, row_id, one)) == (1, Lane.REVIEW)
        [skipped] = [
            record
            for record in caplog.records
            if record.name == plan_found.__name__ and "не пересчитано" in record.getMessage()
        ]
        assert skipped.__dict__["plan_hypothesis_id"] == row_id

        async with factory() as session:
            await plan_tick.step(session, _Jobs(), now=LATER)
            await session.commit()
        assert (await _state(factory, row_id, one)) == (0, Lane.REVIEW)


async def test_a_recount_hands_the_caller_back_its_own_lock_limit(session: AsyncSession) -> None:
    """Короткий предел пересчёта — `SET LOCAL`: он дожил бы до конца транзакции вызывающего. После
    пересчёта у отмены — снова её предел, у решения — прежний, какой был до него."""
    plan = await make_plan(session)
    row = await sheet(session, plan)
    run = await bought(session, row, await take(session, row))
    review = await judged(session, run, Lane.REVIEW)
    await session.commit()
    limit = text("SELECT current_setting('lock_timeout')")
    before = await session.scalar(limit)

    await segment_report.decide(session, review.id, accept=True, author=SELLER)
    after_decision = await session.scalar(limit)
    await session.commit()
    await su.undo(session, review.id)
    after_undo = await session.scalar(limit)

    assert (after_decision, after_undo) == (before, "5s")
    await session.refresh(row)
    assert row.found == 0
