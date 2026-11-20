"""Потолки расхода плана работ (`sales/plan_budget.py`).

Проверяется то, ради чего модуль: израсходовано — факт «деньгами» отчёта прогона (выдача и модель,
экономный тариф — его долей, Hunter в доллары не идёт) по прогонам гипотезы плана и всего плана, без
чужих планов и «Сбора базы»; резерв идущего прогона — смета сверх факта, у законченного резерва нет;
до прогона впритык — помещается, через край у гипотезы и у плана — исход значением и слова экрана,
резерв в словах назван; слова «нужны ключи»; проблема — словарём `{code, words, at}`, новая заменяет
прежнюю того же вида и ложится в строку гипотезы плана. Деньги и числа — выдуманные.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from backend.config.judge import JudgeMode
from backend.features.sales import plan_budget
from backend.features.sales.plan_budget import Outcome, Problem, Room, Spent, room, usd
from backend.features.sales.plan_models import SalesPlanHypothesisModel
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from backend.features.sales.segment_spend import spend_rows
from backend.features.sales.segment_tier import TIER_KEY, JudgeTier
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_plan_waves import make_plan, sheet

Run = SalesSegmentRunModel

NOW = datetime(2026, 11, 17, 9, 0, tzinfo=UTC)
#: Цены модели в смете прогона: токены ввода — доллар за миллион, так факт модели виден в токенах.
PRICES = {"usd_per_1m_input": 1.0, "usd_per_1m_output": 4.0}
FLEX = {TIER_KEY: JudgeTier.FLEX.value}
CAP = Decimal("50.00")
PLAN_CAP = Decimal("100.00")


def spent_run(
    row: SalesPlanHypothesisModel | None = None,
    *,
    serp: float | None,
    model: float = 0.0,
    promised: float = 0.0,
    going: bool = False,
    estimate: dict[str, Any] | None = None,
    own: bool = True,
) -> Run:
    """Прогон с расходом: выдача и модель — долларами факта (`serp=None` — выдачу не покупали),
    смета — деньгами вместе; идущий — проход не кончился. `own=False` — прогон «Сбора базы»."""
    report: dict[str, Any] = {} if serp is None else {"serp": {"hosts": 1, "cost_usd": serp}}
    if model:
        tokens = round(model * 1_000_000)
        report["judge"] = {"tokens": tokens, "prompt_tokens": tokens, "completion_tokens": 0}
    return Run(
        hypothesis_id=None if row is None else row.hypothesis_id,
        plan_hypothesis_id=row.id if row is not None and own else None,
        keywords=["ремонт кофемашин"],
        country="de",
        language="en",
        depth_pages=1,
        mode=JudgeMode.ENFORCE.value,
        estimate={"serp_usd": 0.0, "tokens_usd": promised, **PRICES, **(estimate or {})},
        report=report,
        stage=Stage.JUDGE if going else Stage.DONE,
        finished_at=None if going else NOW,
    )


def money(text: str) -> Decimal:
    return Decimal(text)


# --- деньги ---------------------------------------------------------------------------------


def test_usd_is_exact_to_a_hundredth_of_a_cent() -> None:
    assert usd(None) == 0
    assert usd(0.1 + 0.2) == money("0.3000")
    assert usd(Decimal("50.00")) == money("50")
    assert usd(48.1) + usd(0.9) + usd(1.0) == money("50")


def test_a_finished_run_spends_its_money_fact_and_holds_no_reserve() -> None:
    run = spent_run(serp=0.50, model=0.70, promised=5.00)
    # Hunter платится запросами своего тарифа — в доллары он не идёт.
    run.report["contacts"] = {"counters": {"provider_entered": 40}}

    assert plan_budget.run_spent(run) == Spent(money("1.20"))
    assert plan_budget.run_spent(run).spent == usd(spend_rows(run)[-1].fact_usd)
    assert plan_budget.run_spent(spent_run(serp=None)) == Spent()


def test_a_going_run_reserves_its_estimate_above_the_fact() -> None:
    assert plan_budget.run_spent(spent_run(serp=0.10, promised=0.90, going=True)) == Spent(
        money("0.10"), money("0.80"), 1
    )
    # Факт перерос смету — резерва нет: идущий прогон весит свой факт.
    assert plan_budget.run_spent(
        spent_run(serp=0.10, model=1.00, promised=0.90, going=True)
    ) == Spent(money("1.10"), money("0"), 1)
    # Остановленный и поставленный продолжением — идёт: конец прохода пуст.
    resumed = spent_run(serp=0.10, promised=0.90)
    resumed.stage, resumed.finished_at = Stage.STOPPED, None
    assert plan_budget.run_spent(resumed).held == money("0.90")
    stopped = spent_run(serp=0.10, promised=0.90)
    stopped.stage = Stage.STOPPED
    assert plan_budget.run_spent(stopped) == Spent(money("0.10"))


async def test_spending_sums_each_plan_hypothesis_and_nothing_else(session: AsyncSession) -> None:
    plan = await make_plan(session)
    first = await sheet(session, plan, keys=())
    second = await sheet(session, plan, keys=())
    stranger = await sheet(session, keys=())
    session.add_all(
        [
            spent_run(first, serp=0.50, model=0.70),
            # Экономный тариф: модель — половиной прайса, как в отчёте прогона.
            spent_run(first, serp=0.30, model=1.00, estimate=FLEX),
            spent_run(first, serp=0.10, promised=0.90, going=True),
            spent_run(second, serp=2.00),
            spent_run(second, serp=None),
            spent_run(stranger, serp=5.00),
            spent_run(first, serp=9.00, own=False),
        ]
    )
    await session.flush()

    found = await plan_budget.spending(session, plan.id)

    assert found == {
        first.id: Spent(money("2.10"), money("0.80"), 1),
        second.id: Spent(money("2.00")),
    }
    assert sum(found.values(), Spent()) == Spent(money("4.10"), money("0.80"), 1)


# --- до прогона -----------------------------------------------------------------------------


def test_right_at_the_cap_the_run_fits() -> None:
    assert room(
        hypothesis=Spent(money("49.10")),
        hypothesis_cap=CAP,
        plan=Spent(money("99.10")),
        plan_cap=PLAN_CAP,
        next_usd=0.90,
    ) == Room(Outcome.GO)
    # Двоичная дробь сказала бы здесь «через край»: 0,1 + 0,2 + 0,3 > 0,6.
    tight = Spent(usd(0.1), usd(0.2), 1)
    assert room(
        hypothesis=tight,
        hypothesis_cap=money("0.60"),
        plan=tight,
        plan_cap=money("0.60"),
        next_usd=0.3,
    ) == Room(Outcome.GO)


def test_over_the_hypothesis_cap_the_hypothesis_stops_in_words() -> None:
    decided = room(
        hypothesis=Spent(money("49.30")),
        hypothesis_cap=CAP,
        plan=Spent(money("60.00")),
        plan_cap=PLAN_CAP,
        next_usd=0.90,
    )

    assert decided == Room(
        Outcome.HYPOTHESIS_CAP,
        "потолок гипотезы 50,00\u00a0$: израсходовано 49,30\u00a0$, следующий прогон ≈ 0,90\u00a0$ — "
        "поднимите потолок в «Качестве сайтов»",
    )


def test_over_the_plan_cap_the_plan_pauses_in_words() -> None:
    decided = room(
        hypothesis=Spent(money("10.00")),
        hypothesis_cap=CAP,
        plan=Spent(money("99.50")),
        plan_cap=PLAN_CAP,
        next_usd=money("0.90"),
    )

    assert decided == Room(
        Outcome.PLAN_CAP,
        "потолок плана 100,00\u00a0$: израсходовано 99,50\u00a0$, следующий прогон ≈ 0,90\u00a0$ — "
        "поднимите потолок кнопкой «Потолок» или остановите план",
    )


def test_both_caps_over_the_hypothesis_stops_first() -> None:
    decided = room(
        hypothesis=Spent(money("49.50")),
        hypothesis_cap=CAP,
        plan=Spent(money("99.50")),
        plan_cap=PLAN_CAP,
        next_usd=0.90,
    )

    assert decided.outcome is Outcome.HYPOTHESIS_CAP


def test_the_reserve_of_going_runs_counts_and_is_named() -> None:
    one = room(
        hypothesis=Spent(money("48.10"), money("1.20"), 1),
        hypothesis_cap=CAP,
        plan=Spent(),
        plan_cap=PLAN_CAP,
        next_usd=0.90,
    )
    assert one.outcome is Outcome.HYPOTHESIS_CAP
    assert one.words == (
        "потолок гипотезы 50,00\u00a0$: израсходовано 48,10\u00a0$ и 1,20\u00a0$ в резерве идущего прогона, "
        "следующий прогон ≈ 0,90\u00a0$ — поднимите потолок в «Качестве сайтов»"
    )
    two = room(
        hypothesis=Spent(),
        hypothesis_cap=CAP,
        plan=Spent(money("98.00"), money("1.50"), 2),
        plan_cap=PLAN_CAP,
        next_usd=0.90,
    )
    assert two.words is not None
    assert "1,50\u00a0$ в резерве идущих прогонов" in two.words


# --- слова и проблемы -----------------------------------------------------------------------


def test_money_in_words_follows_the_service_rule() -> None:
    """Деньги слов — как на экранах сервиса («48,10 $», разряды и знак — неразрывным пробелом); доли
    цента — «меньше 0,01 $», а не «0,00 $», будто даром; ноль — нулём."""
    assert plan_budget.said_usd(Decimal("48.1")) == "48,10\u00a0$"
    assert plan_budget.said_usd(Decimal("1234.5")) == "1\u00a0234,50\u00a0$"
    assert plan_budget.said_usd(Decimal("0.004")) == "меньше 0,01\u00a0$"
    assert plan_budget.said_usd(Decimal("0.005")) == "0,01\u00a0$"
    assert plan_budget.said_usd(Decimal(0)) == "0,00\u00a0$"


def test_need_keys_words_name_the_depth_found_and_target() -> None:
    assert plan_budget.keys_words(3, 3120, 5000) == (
        "ключи и глубина до топ-30 кончились: найдено 3 120 из 5 000 — добавьте ключи "
        "или поднимите глубину"
    )
    # Предельная ступень книги: глубже не поднять.
    assert plan_budget.keys_words(5, 12, 300) == (
        "ключи и глубина до топ-50 кончились: найдено 12 из 300 — добавьте ключи"
    )


def test_a_new_problem_replaces_the_old_one_of_its_kind() -> None:
    keys = plan_budget.problem(Problem.KEYS, "нужны ключи", NOW)
    old = plan_budget.problem(Problem.BUDGET, "потолок гипотезы — прежние слова", NOW)
    new = plan_budget.problem(Problem.BUDGET, "потолок гипотезы — новые слова", NOW)
    problems = [keys, old]

    assert plan_budget.put(problems, new) == [keys, new]
    assert problems == [keys, old]
    assert new == {
        "code": "budget",
        "words": "потолок гипотезы — новые слова",
        "at": "2026-11-17T09:00:00+00:00",
    }


def test_reworded_keeps_the_place_and_time_of_the_problem() -> None:
    """Пересказ числа — та же проблема: место в списке (признак потолка, `stopped_on_cap`) и время
    прежние, другие виды не тронуты; новый список, а не правка на месте."""
    keys = plan_budget.problem(Problem.KEYS, plan_budget.keys_words(5, 24, 100), NOW)
    budget = plan_budget.problem(Problem.BUDGET, "потолок гипотезы — слова", NOW)
    problems = [keys, budget]

    found = plan_budget.reworded(problems, Problem.KEYS, plan_budget.keys_words(5, 25, 100))

    assert found == [{**keys, "words": plan_budget.keys_words(5, 25, 100)}, budget]
    assert problems == [keys, budget]
    assert keys["words"] == plan_budget.keys_words(5, 24, 100)


async def test_a_hypothesis_over_its_cap_gets_the_budget_problem_in_its_row(
    session: AsyncSession,
) -> None:
    plan = await make_plan(session, cap=PLAN_CAP)
    row = await sheet(session, plan, keys=(), cap=money("2.00"))
    session.add(spent_run(row, serp=0.50, model=0.70))
    await session.flush()
    spent = await plan_budget.spending(session, plan.id)

    decided = room(
        hypothesis=spent[row.id],
        hypothesis_cap=row.cap_usd,
        plan=sum(spent.values(), Spent()),
        plan_cap=plan.cap_usd,
        next_usd=0.90,
    )
    assert decided.outcome is Outcome.HYPOTHESIS_CAP
    assert decided.words is not None
    row.problems = plan_budget.put(
        row.problems, plan_budget.problem(Problem.BUDGET, decided.words, NOW)
    )
    await session.flush()
    await session.refresh(row)

    assert row.problems == [
        {
            "code": "budget",
            "words": "потолок гипотезы 2,00\u00a0$: израсходовано 1,20\u00a0$, следующий прогон ≈ "
            "0,90\u00a0$ — поднимите потолок в «Качестве сайтов»",
            "at": "2026-11-17T09:00:00+00:00",
        }
    ]
