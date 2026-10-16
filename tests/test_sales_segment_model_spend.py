"""Деньги модели прогона сбора: ввод и вывод — своими ценами, пересуд — в расходе, доля вывода в
смете — по истории.

У вывода модели своя цена, и рассуждение идёт в счёт вывода: деньги судьи по одной смешанной
цене врали бы на долю вывода. Панель расхода (`segment_spend`) считает ввод по цене ввода, вывод
— по цене вывода, токены без разбивки — смешанной ценой с запасной долей; пересуды прогона —
своей строкой и в деньгах вместе. Смета (`segment_estimate`) берёт долю вывода из прогонов с
раздельным счётом, без них — запасную и говорит это словами. Числа выдуманы.
"""

from __future__ import annotations

from typing import Any

import pytest
from backend.config import sales as sales_cfg
from backend.features.sales import segment_history
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_estimate import estimate
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_rejudge_report import TOTALS
from backend.features.sales.segment_spend import (
    OUTPUT_SHARE,
    REJUDGE_PROMISED,
    SpendRow,
    spend_rows,
    usd_per_million,
)
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_estimate import _settings, ask
from tests.test_sales_segment_run import hypothesis, make_run
from tests.test_sales_segment_spend import ESTIMATE, REPORT, run_of

__all__ = ["_settings"]  # фикстура сметы — отсюда её видит pytest

#: Цены сметы на запуске — раздельно, $ за миллион ввода и вывода: не те, что в настройках
#: (`_settings`: 0,25 и 2,00) — панель обязана брать сметные.
IN, OUT = 0.3, 2.4
PRICED: dict[str, Any] = {**ESTIMATE, "usd_per_1m_input": IN, "usd_per_1m_output": OUT}
#: Смешанная цена токенов без разбивки — цены сметы с запасной долей вывода.
BLENDED = (1 - OUTPUT_SHARE) * IN + OUTPUT_SHARE * OUT
#: Судья с разбивкой: ввод, вывод (с рассуждением) и токены без разбивки.
JUDGE: dict[str, Any] = {
    "judged": 38,
    "tokens": 41_000,
    "prompt_tokens": 32_000,
    "completion_tokens": 7_000,
    "unsplit_tokens": 2_000,
    "reasoning_tokens": 5_000,
}


def rows_of(run: SalesSegmentRunModel) -> dict[str, SpendRow]:
    return {row.item: row for row in spend_rows(run)}


# --- панель расхода ----------------------------------------------------------------------------


def test_the_judge_money_counts_input_and_output_at_their_own_prices() -> None:
    """Ввод — по цене ввода, вывод — по цене вывода, токены без разбивки — смешанной ценой с
    запасной долей вывода; цены — сметы на запуске, а не нынешних настроек."""
    judge = rows_of(run_of(estimate=PRICED, report={**REPORT, "judge": JUDGE}))["judge"]

    usd = round((32_000 * IN + 7_000 * OUT + 2_000 * BLENDED) / 1_000_000, 4)
    assert (judge.fact_units, judge.fact_usd) == (41_000, usd)
    assert (
        judge.fact
        == f"токенов 41\u00a0000, из них вывод 7\u00a0000 (без разбивки 2\u00a0000), ≈ ${usd:.4f}".replace(
            ".", ","
        )
    )


def test_a_run_judged_before_the_split_keeps_its_early_tokens_at_the_blended_price() -> None:
    """Старый прогон продолжили после раздельного счёта: ранние токены — только в `tokens`;
    без разбивки — всё, что сверх ввода и вывода, а не поле `unsplit_tokens`."""
    old = {"tokens": 5_000 + 1_100, "prompt_tokens": 1_000, "completion_tokens": 100}
    judge = rows_of(run_of(estimate=PRICED, report={**REPORT, "judge": old}))["judge"]

    assert judge.fact_usd == round((1_000 * IN + 100 * OUT + 5_000 * BLENDED) / 1_000_000, 4)


def test_an_estimate_before_the_split_prices_takes_the_settings_and_its_own_blended_price() -> None:
    """Смета до раздельных цен: ввод и вывод — по настройкам, без разбивки — смешанной ценой,
    которую записала смета."""
    judge = rows_of(run_of(estimate=ESTIMATE, report={**REPORT, "judge": JUDGE}))["judge"]

    entered, produced = (
        sales_cfg.SEGMENT_INPUT_USD_PER_1M_TOKENS,
        sales_cfg.SEGMENT_OUTPUT_USD_PER_1M_TOKENS,
    )
    usd = round((32_000 * entered + 7_000 * produced + 2_000 * 0.6) / 1_000_000, 4)
    assert (entered, produced) == (0.25, 2.0)
    assert judge.fact_usd == usd


def test_every_rejudge_of_the_run_is_a_spend_row_and_part_of_the_money() -> None:
    """Пересуды прогона — своей строкой (их смета — перед каждым пересудом, не на запуске) и в
    деньгах вместе: модель — это и судья, и пересуд."""
    totals = {"count": 2, "tokens": 4_850, "prompt_tokens": 3_000}
    totals |= {"completion_tokens": 650, "unsplit_tokens": 1_200, "reasoning_tokens": 292}
    found = rows_of(run_of(estimate=PRICED, report={**REPORT, "judge": JUDGE, TOTALS: totals}))

    rejudge = found["rejudge"]
    usd = round((3_000 * IN + 650 * OUT + 1_200 * BLENDED) / 1_000_000, 4)
    assert (rejudge.title, rejudge.promised) == ("Пересуд", REJUDGE_PROMISED)
    assert (rejudge.fact_units, rejudge.fact_usd) == (4_850, usd)
    assert rejudge.fact.startswith(
        "пересудов 2: токенов 4\u00a0850, из них вывод 650 (без разбивки 1\u00a0200)"
    )
    judge_usd = found["judge"].fact_usd or 0.0
    assert found["money"].fact_usd == round(0.0132 + judge_usd + usd, 4)
    assert list(found) == ["serp", "judge", "rejudge", "search", "check", "money"]


def test_without_a_rejudge_there_is_no_rejudge_row() -> None:
    found = rows_of(run_of(estimate=PRICED, report={**REPORT, "judge": JUDGE}))

    assert "rejudge" not in found
    assert found["money"].fact_usd == round(0.0132 + (found["judge"].fact_usd or 0.0), 4)


# --- смета: доля вывода по истории --------------------------------------------------------------


async def judged(session: AsyncSession, owner: SalesHypothesisModel, judge: dict[str, Any]) -> None:
    """Прошлый прогон сбора с отчётом судьи."""
    run = await make_run(session, owner)
    run.report = {"judge": judge}
    await session.commit()


async def test_the_estimate_takes_the_output_share_from_runs_with_the_split(
    session: AsyncSession,
) -> None:
    """Доля вывода — суммой по прогонам с раздельным счётом (прогон в сотню сайтов весит больше
    десятка); прогоны до разбивки в долю не идут."""
    owner = await hypothesis(session)
    await judged(session, owner, {"tokens": 50_000})  # до раздельного счёта: делить не на что
    split_runs = ((27_000, 6_000), (9_000, 3_000))  # 18 % и 25 %: сумма — не среднее долей
    for prompt, completion in split_runs:
        numbers = {"prompt_tokens": prompt, "completion_tokens": completion}
        await judged(session, owner, {"tokens": prompt + completion, **numbers})

    found = await estimate(session, ask(owner.id))

    share = (6_000 + 3_000) / (27_000 + 6_000 + 9_000 + 3_000)
    assert (found.output_share, found.output_share_runs) == (pytest.approx(share), 2)
    assert found.usd_per_1m_tokens == pytest.approx(usd_per_million(share))
    assert (found.usd_per_1m_input, found.usd_per_1m_output) == (0.25, 2.0)
    assert found.lines[2].endswith(
        f"; доля вывода — {round(share * 100)}% токенов, как в прошлых прогонах с раздельным "
        "счётом (2)."
    )
    saved = found.as_dict()
    assert (saved["output_share"], saved["output_share_runs"]) == (pytest.approx(share), 2)
    assert (saved["usd_per_1m_input"], saved["usd_per_1m_output"]) == (0.25, 2.0)


async def test_a_handful_of_split_tokens_is_not_history_and_the_share_is_the_fallback(
    session: AsyncSession,
) -> None:
    """Горсть сайтов — не история: доля из неё случайна, смета берёт запасную и так и говорит."""
    owner = await hypothesis(session)
    small = segment_history.MIN_SPLIT_TOKENS - 1
    numbers = {"prompt_tokens": small - 100, "completion_tokens": 100}
    await judged(session, owner, {"tokens": small, **numbers})

    found = await estimate(session, ask(owner.id))

    assert (found.output_share, found.output_share_runs) == (OUTPUT_SHARE, 0)
    assert found.lines[2].endswith("; доля вывода — запасная, замеров ещё нет: 20% токенов.")


async def test_runs_without_the_split_do_not_take_the_window_of_the_history(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Окно истории — последние прогоны С разбивкой: свежие прогоны без неё (судились до раздельного
    счёта или судья был выключен) не вытесняют замер."""
    monkeypatch.setattr(segment_history, "HISTORY_RUNS", 2)
    owner = await hypothesis(session)
    numbers = {"prompt_tokens": 15_000, "completion_tokens": 5_000}
    await judged(session, owner, {"tokens": 20_000, **numbers})
    for _ in range(2):
        await judged(session, owner, {"tokens": 30_000})

    assert await segment_history.output_share(session) == (pytest.approx(0.25), 1)
