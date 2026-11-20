"""«Найдено» гипотезы плана работ — сразу после решения человека, а не на следующем проходе.

Стенд 19.11: человек принял сайт прогона плана и отменил решение, а экран и API плана показывали
прежнее число до следующего прохода конвейера, у остановленной гипотезы — насовсем: проход её не
считал. Проверяется: «Принять» и «Отменить решение» маршрутами — число в API плана той же
транзакцией, а время взгляда прохода (`counted_at`) не тронуто; решение по прогону экрана «Сбор
базы» той же гипотезы число плана не трогает; пересуд прогона плана — на своём чекпоинте; проход
считает у гипотез в конце найдено и расход, а остановленную, набравшую цель, в письма не
переводит; два решения по одной гипотезе плана сразу — второе считает после первого; строку
гипотезы плана держат дольше короткого предела — пересчёт пропущен строкой журнала, а отмена
проходит, и её собственный предел ожидания замков — прежний; у плана на паузе пропущенное прохода
не дожидается — его считает первая кнопка строки. Слова «нужны ключи» следуют за числом (стенд
20.11): решение и проход пишут их на месте, с прежним порядком проблем и временем; цель набрана —
проблема остаётся словами «Продолжить» и этап прежний, подъём потолка такую не будит, а поднятая
цель снова просит ключи (ревью 20.11). Сайты выдуманы (`*.example.test`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.config.judge import JudgeMode
from backend.features.core.models.access import UserModel
from backend.features.sales import plan_actions, plan_found, plan_tick, segment_report
from backend.features.sales import segment_rejudge as sr
from backend.features.sales import segment_undo as su
from backend.features.sales.plan_actions import PLAN_STOPPED
from backend.features.sales.plan_budget import (
    Outcome,
    Problem,
    Spent,
    cap_words,
    keys_words,
    problem,
    stopped_on_cap,
)
from backend.features.sales.plan_models import (
    PlanStage,
    PlanStatus,
    SalesPlanHypothesisModel,
    SalesPlanModel,
)
from backend.features.sales.plan_moves import Did, Turn
from backend.features.sales.plan_runs import NOT_PUT
from backend.features.sales.plan_waves import Batch, Wave
from backend.features.sales.segment_models import Lane, SalesSegmentCandidateModel
from httpx import AsyncClient
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_hypothesis_edit import _waits_on_a_lock
from tests.test_sales_plan_actions import bought_out
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


# --- слова «нужны ключи» за числом ------------------------------------------------------------


async def _stopped_for_keys(
    session: AsyncSession, target: int, before: list[dict[str, Any]]
) -> tuple[Row, Candidate, dict[str, Any]]:
    """Гипотеза плана встала «нужны ключи» на «найдено 1 из `target`»: судья принял один сайт её
    прогона, второй ждёт человека. Проблемы — `before`, потом «нужны ключи»."""
    row = await sheet(session, await make_plan(session), target=target)
    run = await bought(session, row, await take(session, row))
    await judged(session, run, Lane.ACCEPT)
    review = await judged(session, run, Lane.REVIEW)
    keys = problem(Problem.KEYS, keys_words(row.max_pages, 1, target), LOOKED)
    row.stage, row.found, row.problems = PlanStage.STOPPED.value, 1, [*before, keys]
    await session.commit()
    return row, review, keys


async def test_a_decision_restates_need_keys_in_place_and_the_cap_sign_stays(
    session: AsyncSession,
) -> None:
    """Стенд 20.11: «…найдено 1 из 3 — добавьте ключи» — снимок на момент остановки, а после
    «Принять» рядом уже 2. Слова — на новое число, на месте: гипотеза встала на потолке, потом
    «Собрать ещё» без ключей поставило «нужны ключи» — порядок проблем говорит, на чём она стоит
    (`stopped_on_cap`), время — когда кончились ключи; пересчёт не меняет ни того ни другого."""
    budget = problem(
        Problem.BUDGET,
        cap_words(Outcome.HYPOTHESIS_CAP, Decimal("5.00"), Spent(Decimal("4.90")), Decimal("0.30")),
        LOOKED - timedelta(hours=1),
    )
    row, review, keys = await _stopped_for_keys(session, 3, [budget])

    await segment_report.decide(session, review.id, accept=True, author=SELLER)

    assert row.found == 2
    assert row.problems == [budget, {**keys, "words": keys_words(row.max_pages, 2, 3)}]
    assert not stopped_on_cap(row)


async def test_a_decision_that_meets_the_target_keeps_need_keys_in_words_of_resume(
    session: AsyncSession,
) -> None:
    """Цель набрана — ключей этой цели не просят, но «нужны ключи» остаются: слова — «цель набрана:
    «Продолжить» отправит гипотезу в письма», на месте и с прежним временем; другие проблемы и
    этап — прежние (проход остановленную в письма не переводит). «Продолжить» не отказывает —
    гипотеза снова в работе, и её ход переводит её в письма."""
    failed = problem(Problem.RUN_FAILED, NOT_PUT + "доля потолка модели выбрана", LOOKED)
    row, review, keys = await _stopped_for_keys(session, 2, [failed])

    await segment_report.decide(session, review.id, accept=True, author=SELLER)

    met = keys_words(row.max_pages, 2, 2)
    assert met.endswith("найдено 2 из 2 — цель набрана: «Продолжить» отправит гипотезу в письма")
    assert (row.found, row.stage, row.problems) == (2, "stopped", [failed, {**keys, "words": met}])
    await plan_actions.resume_hypothesis(session, row.id, author_id=None, now=LATER)
    assert (row.stage, row.problems) == ("collecting", [])
    await session.commit()
    turn = await plan_tick.step(session, _Jobs(), now=LATER)
    assert (turn, row.stage) == (Turn(Did.MOVED, plan_tick.TARGET_MET), "sending")


async def test_a_cap_raised_after_the_target_was_met_does_not_wake_a_sheet_that_stood_on_keys(
    session: AsyncSession,
) -> None:
    """Ревью 20.11: гипотеза встала на потолке, потом — «нужны ключи». «Принять» набрало цель, а
    «нужны ключи» остались последними из проблем остановки: она стоит на ключах, а не на потолке.
    Подъём потолка в «Качестве сайтов» снимает «кончился бюджет», но её не будит — и проход не
    уводит её в письма мимо «Продолжить»."""
    budget = problem(
        Problem.BUDGET,
        cap_words(Outcome.HYPOTHESIS_CAP, Decimal("5.00"), Spent(Decimal("4.90")), Decimal("0.30")),
        LOOKED - timedelta(hours=1),
    )
    row, review, keys = await _stopped_for_keys(session, 2, [budget])

    await segment_report.decide(session, review.id, accept=True, author=SELLER)
    assert (row.found, [item["code"] for item in row.problems]) == (2, ["budget", "keys"])
    assert not stopped_on_cap(row)
    raised = plan_actions.Quality(cap_usd=row.cap_usd + Decimal("10"))
    await plan_actions.change_quality(session, row.id, raised, author_id=None, now=LATER)
    await session.commit()
    await plan_tick.step(session, _Jobs(), now=LATER + timedelta(minutes=1))

    assert row.stage == "stopped"
    assert row.problems == [{**keys, "words": keys_words(row.max_pages, 2, 2)}]


async def test_a_target_raised_after_it_was_met_asks_for_keys_again_with_the_keys_button(
    session: AsyncSession,
) -> None:
    """Ревью 20.11: цель набрана, ключей и глубины нет. Цель подняли в «Качестве сайтов» — «нужны
    ключи» снова просят ключи (кнопка «Добавить ключи» экрана — по виду проблемы), и «Продолжить»
    отказывает теми же словами: гипотеза не стоит «Остановленной» без проблемы и без кнопки."""
    row, review, _ = await _stopped_for_keys(session, 2, [])
    await bought_out(session, row)
    await session.commit()
    await segment_report.decide(session, review.id, accept=True, author=SELLER)
    assert [item["code"] for item in row.problems] == ["keys"]

    raised = plan_actions.Quality(target=5)
    await plan_actions.change_quality(session, row.id, raised, author_id=None, now=LATER)

    asked = keys_words(row.max_pages, 2, 5)
    assert asked.endswith("найдено 2 из 5 — добавьте ключи")
    assert (row.stage, [(one["code"], one["words"]) for one in row.problems]) == (
        "stopped",
        [("keys", asked)],
    )
    with pytest.raises(plan_actions.PlanStateError, match="добавьте ключи"):
        await plan_actions.resume_hypothesis(session, row.id, author_id=None, now=LATER)


async def test_the_pass_restates_need_keys_of_a_stopped_sheet_it_counts(
    session: AsyncSession,
) -> None:
    """Проход считает остановленную гипотезу в конце идущего плана — и слова «нужны ключи» идут за
    числом: прогон дошёл после остановки, принятых стало больше."""
    row, review, keys = await _stopped_for_keys(session, 5, [])
    review.lane = Lane.ACCEPT.value
    row.counted_at = LOOKED
    await session.commit()

    await plan_tick.step(session, _Jobs(), now=LATER)

    assert (row.found, row.stage, row.counted_at) == (2, "stopped", LOOKED)
    assert row.problems == [{**keys, "words": keys_words(row.max_pages, 2, 5)}]


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


async def _committed_paused_sheet(factory: async_sessionmaker[AsyncSession]) -> tuple[int, int]:
    """План на паузе; его гипотеза встала «нужны ключи» на «найдено 1 из 2»: ключи куплены до
    предела, судья принял один сайт, второй ждёт человека, — зафиксированы."""
    async with factory() as session:
        plan = await make_plan(session)
        row = await sheet(session, plan, max_pages=1, target=2)
        run = await bought(session, row, await take(session, row))
        await judged(session, run, Lane.ACCEPT)
        review = await judged(session, run, Lane.REVIEW)
        await bought_out(session, row)
        row.stage, row.found = PlanStage.STOPPED.value, 1
        row.problems = [problem(Problem.KEYS, keys_words(1, 1, 2), LOOKED)]
        plan.status, plan.problem = PlanStatus.PAUSED.value, PLAN_STOPPED
        await session.commit()
        return row.id, review.id


async def test_a_recount_skipped_on_a_paused_plan_is_counted_by_the_first_row_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Стенд 20.11: план на паузе — прохода нет. «Принять» пришло, пока строку держали дольше
    короткого предела: пересчёт пропущен, в строке «найдено 1 из 2» и «нужны ключи», и проход его
    не досчитает. Первая кнопка строки — «Продолжить» — считает под своим замком: отказа «нужны
    ключи» нет, хотя ключей и глубины нет, число в строке живое, проблема снята."""
    monkeypatch.setattr(plan_found, "LOCK_WAIT", "SET LOCAL lock_timeout = '200ms'")
    async with committed_sessions() as factory:
        row_id, review = await _committed_paused_sheet(factory)
        async with factory() as holding:
            await holding.execute(select(Row.id).where(Row.id == row_id).with_for_update())
            await asyncio.wait_for(_accept(factory, review), MEETING)
            await holding.rollback()
        assert (await _state(factory, row_id, review)) == (1, Lane.ACCEPT)

        async with factory() as session:
            assert await plan_tick.step(session, _Jobs(), now=LATER) == Turn(Did.NOTHING)
            await session.commit()
        assert (await _state(factory, row_id, review)) == (1, Lane.ACCEPT)

        async with factory() as session:
            row = await plan_actions.resume_hypothesis(session, row_id, author_id=None, now=LATER)
            seen = (row.found, row.problems, row.stage)
            await session.commit()

        assert seen == (2, [], "collecting")
        assert (await _state(factory, row_id, review)) == (2, Lane.ACCEPT)


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
