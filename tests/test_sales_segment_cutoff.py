"""Судья сегмента: ответ, обрезанный потолком вывода (`finish_reason == "length"`).

У модели с рассуждением потолок `max_completion_tokens` включает рассуждение: оно может съесть
весь ответ (200 OK, пустой `content`) или оборвать JSON посередине. Это не вердикт и не «ответ не
разобран», а своя причина: WARNING в журнал, «на проверку» без вердикта, следующий проход спросит
снова, а доля таких за прогон — в итоге судьи. Модель — подставным транспортом.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx
import pytest
from backend.features.sales import segment_client
from backend.features.sales import segment_judge as sj
from backend.features.sales.segment_models import Lane
from backend.features.sales.segment_run import _judge_report
from backend.features.sales.segment_window import Judged, JudgeTally, count
from tests.segment_facts_fakes import decide_any, facts
from tests.test_sales_segment_judge import MODEL_KEY, SEGMENT, SHOP, text

USAGE = {
    "prompt_tokens": 1400,
    "completion_tokens": 2000,
    "total_tokens": 3400,
    "completion_tokens_details": {"reasoning_tokens": 2000},
}


def answered(content: str | None, finish: str) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": USAGE,
    }


async def judged(body: dict[str, Any]) -> sj.SegmentVerdict:
    def reply(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body, request=request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(reply))
    client = sj.SegmentClient(http, model="gpt-5-mini", api_key=MODEL_KEY)
    try:
        return await decide_any(client, host=SHOP, segment=SEGMENT, text=text())
    finally:
        await client.aclose()


@pytest.mark.parametrize(
    "content",
    ["", None, '{"kind": "own", "fit": "ye'],
    ids=["empty", "null", "json-cut-in-the-middle"],
)
async def test_an_answer_cut_by_the_output_cap_is_its_own_reason_and_a_warning(
    content: str | None, caplog: pytest.LogCaptureFixture
) -> None:
    """200 OK, а ответа нет: рассуждение съело потолок. Ни вердикта, ни «ответ не разобран»."""
    with caplog.at_level(logging.WARNING, logger=segment_client.logger.name):
        verdict = await judged(answered(content, "length"))

    assert (verdict.lane, verdict.reason) == (Lane.REVIEW, sj.CUT_OFF)
    assert (verdict.unanswered, verdict.cut_off, verdict.quote) == (True, True, None)
    assert (verdict.tokens, verdict.spent) == (3400, sj.Spent(1400, 2000, 2000))
    assert not verdict.proven  # в кэш не ложится: следующий проход спросит снова
    [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "потолком вывода" in record.getMessage()


async def test_an_empty_answer_that_was_not_cut_stays_an_empty_answer() -> None:
    verdict = await judged(answered("", "stop"))

    assert (verdict.lane, verdict.reason) == (Lane.REVIEW, "пустой ответ модели")
    assert (verdict.unanswered, verdict.cut_off) == (True, False)


async def test_a_whole_answer_is_judged_whatever_the_finish_reason_says() -> None:
    body = facts("yes", "Велосипеды и запчасти", "магазин")

    verdict = await judged(answered(json.dumps(body, ensure_ascii=False), "stop"))

    assert (verdict.lane, verdict.cut_off) == (Lane.ACCEPT, False)


@pytest.mark.parametrize(
    ("body", "found"),
    [
        ({"choices": [{"finish_reason": "length"}]}, "length"),
        ({"choices": [{"message": {}}]}, None),
        ({"choices": []}, None),
        ({}, None),
        ({"choices": [{"finish_reason": 3}]}, None),
    ],
    ids=["named", "not-named", "no-choices", "no-body", "not-a-word"],
)
def test_the_finish_reason_is_read_only_when_named(body: dict[str, Any], found: str | None) -> None:
    assert sj.finish_of(body) == found


def test_cut_offs_are_counted_in_the_pass_and_summed_over_passes() -> None:
    """Доля обрезанных за прогон — из итога судьи: проход считает, отчёт прогона складывает."""
    cut = sj.SegmentVerdict(Lane.REVIEW, sj.CUT_OFF, unanswered=True, cut_off=True, tokens=3400)
    empty = sj.SegmentVerdict(Lane.REVIEW, "пустой ответ модели", unanswered=True, tokens=900)
    tally = JudgeTally()

    count(Judged(cut, text()), tally)
    count(Judged(empty, text()), tally)

    assert (tally.unanswered, tally.cut_off) == (2, 1)
    assert tally.as_dict()["cut_off"] == 1
    report = _judge_report({"cut_off": 2, "unanswered": 5}, tally)
    assert report["cut_off"] == 3
