"""Потолки «Плана работ» после сохранения (`sales/plan_actions.py`, слова — `sales/plan_budget.py`).

Проверяется то, ради чего правка (потолок — по смете с запасом, его можно поменять и после
сохранения плана): гипотеза, вставшая на потолке проходом конвейера, после поднятого потолка снова
собирает, и следующий проход ставит её прогон; план на паузе по своему потолку — снова идёт.
Остановленные кнопкой так и стоят; слова «прогон не поставлен», сказанные до потолка, гипотезу не
держат. Ниже израсходованного — отказ словами, ничего не тронуто; ровно израсходованное — можно; тот же
потолок — не правка и не журнал. Деньги и сайты выдуманы, ключи — разных письменностей.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from backend.features.core.models.access import UserModel
from backend.features.sales import plan_actions
from backend.features.sales.plan_actions import (
    CAP_ABOVE_ZERO,
    PLAN_CAPPED,
    PLAN_STOPPED,
    ROW_CAPPED,
    STOPPED_BY_HAND,
    PlanAskError,
    PlanStateError,
    Quality,
)
from backend.features.sales.plan_budget import (
    Problem,
    below_spent,
    paused_on_cap,
    problem,
    stopped_on_cap,
)
from backend.features.sales.plan_models import PlanStage, PlanStatus
from backend.features.sales.plan_moves import Did
from backend.features.sales.plan_runs import NOT_PUT
from backend.features.serp import funds
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_clean_api import _Jobs
from tests.test_sales_plan_actions import journal, name_of
from tests.test_sales_plan_budget import spent_run
from tests.test_sales_plan_tick import LATER, codes, collecting, conveyor, jobs, runs_of, step
from tests.test_sales_plan_waves import NOW, make_plan
from tests.test_sales_segment_estimate import _settings

__all__ = ["_settings", "conveyor", "jobs"]  # смета и конвейер — как у тестов прохода

AUTHOR = "seller@ours.example.test"
CAP = Decimal("50.00")


@pytest.fixture
async def author(make_user: Any) -> UserModel:
    user: UserModel = await make_user(AUTHOR)
    return user


def raise_to(cap: str) -> Quality:
    return Quality(cap_usd=Decimal(cap))


# --- потолок гипотезы плана ------------------------------------------------------------------


async def test_a_raised_cap_wakes_the_sheet_stopped_on_it_and_the_next_pass_launches_its_run(
    session: AsyncSession, jobs: _Jobs, author: UserModel
) -> None:
    row = await collecting(session, cap=Decimal("0.01"))
    await step(session, jobs)
    assert (row.stage, codes(row)) == ("stopped", ["budget"])

    await plan_actions.change_quality(
        session, row.id, raise_to("50.00"), author_id=author.id, now=LATER
    )

    # Гипотеза в плане одна — очередь её, и она сразу собирает.
    assert (row.stage, row.cap_usd, row.problems) == ("collecting", CAP, [])
    turn = await step(session, jobs, LATER + timedelta(minutes=1))
    [launched] = await runs_of(session, row)
    assert (turn.did, turn.run_id) == (Did.LAUNCHED, launched.id)
    assert (await journal(session))[-1] == {
        "who": author.id,
        "план": "План недели",
        "действие": ROW_CAPPED,
        "гипотеза": await name_of(session, row),
        "было": funds.dollars(0.01),
        "стало": funds.dollars(50),
    }


async def test_a_sheet_stopped_by_the_button_after_the_budget_stays_stopped(
    session: AsyncSession, author: UserModel
) -> None:
    row = await collecting(session)
    row.problems = [problem(Problem.BUDGET, "потолок гипотезы 50,00 $: …", NOW)]
    await plan_actions.stop_hypothesis(session, row.id, author_id=author.id, now=NOW)

    await plan_actions.change_quality(
        session, row.id, raise_to("80.00"), author_id=author.id, now=LATER
    )

    # «Кончился бюджет» снят: поднятый потолок его решил; стоит гипотеза кнопкой — и стоит.
    assert (row.stage, codes(row), row.problems[0]["words"]) == (
        "stopped",
        ["stopped"],
        STOPPED_BY_HAND,
    )


async def test_words_not_put_said_before_the_cap_do_not_hold_the_sheet(
    session: AsyncSession, author: UserModel
) -> None:
    row = await collecting(session)
    row.stage = PlanStage.STOPPED.value
    row.problems = [
        problem(Problem.RUN_FAILED, NOT_PUT + "доля потолка модели на сегодня выбрана", NOW),
        problem(Problem.BUDGET, "потолок гипотезы 50,00 $: …", LATER),
    ]

    await plan_actions.change_quality(
        session, row.id, raise_to("60.00"), author_id=author.id, now=LATER
    )

    assert (row.stage, row.problems) == ("collecting", [])


async def test_a_finished_plan_goes_again_when_its_sheet_wakes_on_a_raised_cap(
    session: AsyncSession, author: UserModel
) -> None:
    plan = await make_plan(session)
    row = await collecting(session, plan)
    row.stage = PlanStage.STOPPED.value
    row.problems = [problem(Problem.BUDGET, "потолок гипотезы 50,00 $: …", NOW)]
    plan.status, plan.finished_at = PlanStatus.DONE.value, NOW

    await plan_actions.change_quality(
        session, row.id, raise_to("60.00"), author_id=author.id, now=LATER
    )

    assert (plan.status, plan.finished_at, row.stage) == ("running", None, "collecting")


async def test_a_lowered_cap_lifts_nothing_and_the_sheet_stays_on_the_budget(
    session: AsyncSession, author: UserModel
) -> None:
    row = await collecting(session)
    row.stage = PlanStage.STOPPED.value
    row.problems = [problem(Problem.BUDGET, "потолок гипотезы 50,00 $: …", NOW)]

    await plan_actions.change_quality(
        session, row.id, raise_to("40.00"), author_id=author.id, now=LATER
    )

    assert (row.stage, codes(row), row.cap_usd) == ("stopped", ["budget"], Decimal("40.00"))
    assert [entry["действие"] for entry in await journal(session)] == [ROW_CAPPED]


async def test_a_cap_below_the_spent_is_refused_in_words_and_nothing_moves(
    session: AsyncSession, author: UserModel
) -> None:
    row = await collecting(session)
    session.add_all([spent_run(row, serp=2.00), spent_run(row, serp=0.0012)])
    await session.flush()

    with pytest.raises(PlanStateError) as refused:
        await plan_actions.change_quality(
            session,
            row.id,
            Quality(target=500, cap_usd=Decimal("2.00")),
            author_id=author.id,
            now=LATER,
        )

    assert str(refused.value).endswith(
        f"израсходовано больше {funds.dollars(2.00)} — задайте от {funds.dollars(2.01)}"
    )
    assert (row.cap_usd, row.target, await journal(session)) == (CAP, 100, [])
    # Ровно израсходованное, поднятое до цента, — можно.
    await plan_actions.change_quality(
        session, row.id, raise_to("2.01"), author_id=author.id, now=LATER
    )
    assert row.cap_usd == Decimal("2.01")
    # Расход строки — тот, по которому решён потолок, а не число прошлого прохода.
    assert row.spent_usd == Decimal("2.0012")


async def test_the_reserve_of_a_going_run_is_not_the_spent(
    session: AsyncSession, author: UserModel
) -> None:
    row = await collecting(session)
    session.add(spent_run(row, serp=1.00, promised=9.00, going=True))
    await session.flush()

    await plan_actions.change_quality(
        session, row.id, raise_to("1.50"), author_id=author.id, now=LATER
    )

    assert row.cap_usd == Decimal("1.50")


async def test_the_same_cap_is_no_change_and_no_journal(
    session: AsyncSession, author: UserModel
) -> None:
    row = await collecting(session)

    await plan_actions.change_quality(
        session, row.id, raise_to("50"), author_id=author.id, now=LATER
    )

    assert (row.cap_usd, await journal(session)) == (CAP, [])


def test_a_cap_of_zero_or_less_is_a_bad_ask() -> None:
    assert Quality(cap_usd=Decimal(0)).refusal() == CAP_ABOVE_ZERO
    assert Quality(cap_usd=Decimal("-1")).refusal() == CAP_ABOVE_ZERO
    assert Quality(cap_usd=Decimal("0.01")).refusal() is None


# --- потолок плана ---------------------------------------------------------------------------


async def test_a_raised_plan_cap_resumes_the_plan_paused_on_it_and_the_next_pass_launches(
    session: AsyncSession, jobs: _Jobs, author: UserModel
) -> None:
    plan = await make_plan(session, cap=Decimal("0.02"))
    row = await collecting(session, plan)
    await step(session, jobs)
    stood = plan.problem
    assert plan.status == "paused"

    await plan_actions.change_plan_cap(session, plan.id, Decimal("100.00"), author_id=author.id)

    assert (plan.status, plan.problem, plan.cap_usd) == ("running", None, Decimal("100.00"))
    turn = await step(session, jobs, LATER + timedelta(minutes=1))
    [launched] = await runs_of(session, row)
    assert (turn.did, turn.run_id) == (Did.LAUNCHED, launched.id)
    assert (await journal(session))[-1] == {
        "who": author.id,
        "план": "План недели",
        "действие": PLAN_CAPPED,
        "было": funds.dollars(0.02),
        "стало": funds.dollars(100),
        "стоял": stood,
    }


@pytest.mark.parametrize(
    "words",
    [PLAN_STOPPED, "на счёте выдачи 0,40 $ — пополните счёт и продолжите план"],
    ids=["button", "service"],
)
async def test_a_plan_paused_not_on_its_cap_stays_paused(
    session: AsyncSession, author: UserModel, words: str
) -> None:
    plan = await make_plan(session)
    await collecting(session, plan)
    plan.status, plan.problem = PlanStatus.PAUSED.value, words

    await plan_actions.change_plan_cap(session, plan.id, Decimal("500.00"), author_id=author.id)

    assert (plan.status, plan.problem, plan.cap_usd) == ("paused", words, Decimal("500.00"))
    assert "стоял" not in (await journal(session))[-1]


async def test_a_plan_cap_below_the_plan_spent_is_refused_in_words(
    session: AsyncSession, author: UserModel
) -> None:
    plan = await make_plan(session)
    first = await collecting(session, plan)
    second = await collecting(session, plan)
    session.add_all([spent_run(first, serp=1.25), spent_run(second, serp=1.75)])
    await session.flush()

    with pytest.raises(PlanStateError) as refused:
        await plan_actions.change_plan_cap(session, plan.id, Decimal("2.99"), author_id=author.id)

    assert "потолок плана «План недели» не может быть ниже израсходованного" in str(refused.value)
    assert (plan.cap_usd, await journal(session)) == (Decimal("100.00"), [])
    await plan_actions.change_plan_cap(session, plan.id, Decimal("3.00"), author_id=author.id)
    assert plan.cap_usd == Decimal("3.00")


async def test_a_plan_cap_of_zero_is_a_bad_ask_and_the_same_is_no_change(
    session: AsyncSession, author: UserModel
) -> None:
    plan = await make_plan(session)

    with pytest.raises(PlanAskError, match=CAP_ABOVE_ZERO):
        await plan_actions.change_plan_cap(session, plan.id, Decimal(0), author_id=author.id)
    await plan_actions.change_plan_cap(session, plan.id, Decimal("100"), author_id=author.id)

    assert await journal(session) == []


# --- слова и признаки ------------------------------------------------------------------------


def test_below_the_spent_words_round_the_least_cap_up_to_a_cent() -> None:
    """Расход с долей цента — «больше 2,00 $»: округлённое «израсходовано 2,00 $» рядом с отказом
    потолку 2,00 читалось противоречием (стенд 20.11). Целые центы — как есть, меньше цента —
    «меньше 0,01 $» правила денег."""
    assert below_spent(Decimal("2.00"), Decimal("2.0001")) == (
        f"не может быть ниже израсходованного: израсходовано больше {funds.dollars(2.00)} — "
        f"задайте от {funds.dollars(2.01)}"
    )
    assert below_spent(Decimal("12.39"), Decimal("12.40")) == (
        f"не может быть ниже израсходованного: израсходовано {funds.dollars(12.40)} — "
        f"задайте от {funds.dollars(12.40)}"
    )
    assert below_spent(Decimal("0.00"), Decimal("0.004")) == (
        f"не может быть ниже израсходованного: израсходовано меньше {funds.dollars(0.01)} — "
        f"задайте от {funds.dollars(0.01)}"
    )
    assert below_spent(Decimal("2.00"), Decimal("2.00")) is None


async def test_on_the_cap_means_the_last_stop_reason_is_the_budget(session: AsyncSession) -> None:
    row = await collecting(session)
    budget = problem(Problem.BUDGET, "потолок гипотезы …", NOW)
    by_hand = problem(Problem.STOPPED, STOPPED_BY_HAND, LATER)
    chain = problem(Problem.CHAIN, "нет цепочки на языке писем", LATER)

    row.problems = [budget]
    assert not stopped_on_cap(row)  # собирает — не стоит
    row.stage = PlanStage.STOPPED.value
    assert stopped_on_cap(row)
    row.problems = [budget, chain]  # «нет цепочки» гипотезу не останавливает
    assert stopped_on_cap(row)
    row.problems = [budget, by_hand]
    assert not stopped_on_cap(row)


async def test_paused_on_the_cap_means_the_plan_cap_words(session: AsyncSession) -> None:
    plan = await make_plan(session)
    plan.status, plan.problem = PlanStatus.PAUSED.value, "потолок плана 100,00 $: израсходовано …"
    assert paused_on_cap(plan)
    plan.problem = PLAN_STOPPED
    assert not paused_on_cap(plan)
    plan.status, plan.problem = PlanStatus.RUNNING.value, None
    assert not paused_on_cap(plan)
