"""Виды отказов сметы и остановок прогона «Плана работ» (`sales/plan_failures.py`).

Слова — те, что пишут сами источники: задача прогона (`segment_jobs.stop_words`, повторы кончились),
проход прогона (судья встал, описание стёрто), проверка модели (`shared/llm.words_of`), счёт выдачи
(`serp/funds.refusal`) и смета (`segment_estimate`). Разъедутся слова и вид — красное здесь, а не
план, остановленный по кругу или вставший на паузу без причины.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest
from backend.config.judge import JudgeMode
from backend.features.sales import segment_estimate as se
from backend.features.sales.plan_failures import Kind, of_refusal, of_stop
from backend.features.sales.segment_estimate import Estimate
from backend.features.sales.segment_jobs import (
    RETRIES_SPENT,
    SETTINGS_WORDS,
    SalesOffError,
    SerpNotSetError,
    stop_words,
)
from backend.features.sales.segment_run import JUDGE_STOPPED
from backend.features.sales.segment_window import NO_CREDITS
from backend.features.sales.verify_cap import Month
from backend.features.serp import funds
from backend.features.serp.funds import FundsTooLowError
from backend.shared.llm import KEY_WORDS, NO_CREDITS_WORDS, SILENT_WORDS

#: Отказ сметы выше остатка дневного потолка — словами сметы (`segment_estimate._money_refusal`).
OVER_CAP = "смета модели выше остатка дневного потолка: ≈ 88 400 токенов при остатке 977 — …"


@pytest.mark.parametrize(
    ("words", "kind"),
    [
        (f"{JUDGE_STOPPED}{NO_CREDITS}", Kind.RETRY),
        (f"{JUDGE_STOPPED}дневной потолок вызовов модели продаж выбран: 977 из 977", Kind.RETRY),
        (RETRIES_SPENT.format(why="база не ответила"), Kind.RETRY),
        (f"прогон остановлен: {se.NO_SEGMENT}", Kind.STOP),
        (stop_words(FundsTooLowError(funds.refusal(1.0, 0.1) or "")), Kind.PAUSE),
        (stop_words(FundsTooLowError(funds.refusal(9.0, 12.5) or "")), Kind.PAUSE),
        (stop_words(SerpNotSetError(se.SANDBOX)), Kind.PAUSE),
        (stop_words(SalesOffError(se.SALES_OFF)), Kind.PAUSE),
        (SETTINGS_WORDS, Kind.PAUSE),
        (NO_CREDITS_WORDS, Kind.PAUSE),
        (KEY_WORDS, Kind.PAUSE),
        (SILENT_WORDS.format(why="таймаут"), Kind.PAUSE),
        ("слова, которых конвейер не знает", Kind.PAUSE),
        (None, Kind.PAUSE),
    ],
    ids=[
        "judge-no-credits",
        "judge-model-cap",
        "retries-spent",
        "segment-erased",
        "funds-below-the-floor",
        "funds-below-the-serp",
        "serp-sandbox",
        "sales-off",
        "a-setting",
        "model-no-credits",
        "model-key",
        "model-silent",
        "unknown",
        "no-words",
    ],
)
def test_a_run_stop_is_judged_by_the_words_of_its_source(words: str | None, kind: Kind) -> None:
    assert of_stop(words) is kind


def estimate_of(**changes: Any) -> Estimate:
    """Смета прогона плана: рынок и язык листа годны, описание есть; отказ — из `changes`."""
    found = Estimate(
        hypothesis="Гипотеза плана",
        segment="Сегмент: мастерские по ремонту кофемашин.",
        keywords=["ремонт кофемашин"],
        rejected={},
        country="de",
        language="en",
        depth_pages=1,
        mode=JudgeMode.SHADOW.value,
    )
    return replace(found, **changes)


@pytest.mark.parametrize(
    ("changes", "kind"),
    [
        ({"refusal": OVER_CAP, "tokens": 88_400, "tokens_left": 977}, Kind.RETRY),
        # Отказ настройки при смете выше остатка: смета считает токены и при отказе адресов.
        ({"refusal": se.VERIFIER_NOT_SET, "tokens": 88_400, "tokens_left": 977}, Kind.PAUSE),
        ({"refusal": se.SANDBOX}, Kind.PAUSE),
        ({"refusal": se.CAP_UNREAD, "tokens": 88_400, "tokens_left": 0}, Kind.PAUSE),
        (
            {
                "refusal": "потолок проверок не задан",
                "month": Month(3, "потолок проверок не задан"),
            },
            Kind.PAUSE,
        ),
        ({"refusal": funds.refusal(1.0, 0.0)}, Kind.PAUSE),
        ({"refusal": NO_CREDITS_WORDS}, Kind.PAUSE),
        ({"refusal": se.NO_SEGMENT, "segment": ""}, Kind.STOP),
        ({"refusal": se.NO_KEYWORDS, "keywords": []}, Kind.STOP),
        ({"refusal": "рынка «zz» нет в карте выдачи", "country": "zz"}, Kind.STOP),
        ({"refusal": "язык писем «de» — цепочка пишется на ru, en", "language": "de"}, Kind.STOP),
        # Судья выключен — пустое описание не отказ гипотезы: отказ — другой, у сервиса.
        ({"refusal": se.MODEL_NOT_SET, "segment": "", "mode": JudgeMode.OFF.value}, Kind.PAUSE),
    ],
    ids=[
        "model-cap",
        "verifier-before-tokens",
        "serp-sandbox",
        "cap-unread",
        "verify-month-cap",
        "funds",
        "model-no-credits",
        "segment-erased",
        "no-keys",
        "market",
        "language",
        "judge-off",
    ],
)
def test_an_estimate_refusal_is_judged_by_its_fields(changes: dict[str, Any], kind: Kind) -> None:
    assert of_refusal(estimate_of(**changes)) is kind
