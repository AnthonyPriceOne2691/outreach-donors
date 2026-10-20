"""Непрочитанный потолок или лимит продаж — словами там, где продажи по нему считают.

Общий разбор настроек без падения (`config/_base.spared`) за опечатку в `SALES_DAILY_TOKEN_CAP`
даёт продажам ноль токенов (`llm.UNREAD_CAPS`: их вызовы модели отказывают), а за опечатку в
`OUTREACH_SALES_DAILY_LIMIT` — ноль первых писем (`outreach.UNREAD_LIMITS`). Смета сбора базы и
недельный отчёт считали бы по ним как по заданным: смета — по доле общего потолка («остаток
6 300») и запускала бы прогон, судья которого не ответит; отчёт — «0 писем» по дням, что читается
как выбранный лимит. Теперь — ноль и слова; имени настройки в словах нет: его называет журнал.
"""

from __future__ import annotations

import csv
import io

import pytest
from backend.config import llm as llm_cfg
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.config.judge import JudgeMode
from backend.features.sales import segment_estimate as se
from backend.features.sales import weekly, weekly_text
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_segment_estimate import _settings, ask
from tests.test_sales_segment_run import hypothesis
from tests.test_sales_weekly_rows import BUILT, WEEK, WeekRows

__all__ = ["_settings"]  # настройки сметы — те же, что у её тестов (фикстура видна отсюда)

CAP = "SALES_DAILY_TOKEN_CAP"
LIMIT = "OUTREACH_SALES_DAILY_LIMIT"


# --- смета сбора базы: свой потолок модели не прочитан ------------------------------------------


async def test_unread_sales_cap_is_zero_tokens_and_refuses_the_run_in_words(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Общий потолок задан — доля его была бы «остатком»; свой не прочитан — ноль и отказ."""
    monkeypatch.setattr(llm_cfg, "DAILY_TOKEN_CAP", 9001)
    owner = await hypothesis(session)
    before = await se.estimate(session, ask(owner.id))
    monkeypatch.setattr(llm_cfg, "UNREAD_CAPS", frozenset({CAP}))

    found = await se.estimate(session, ask(owner.id))

    assert before.tokens_left == int(9001 * llm_cfg.SALES_CAP_SHARE)
    assert found.tokens_left == 0
    assert found.refusal == se.CAP_UNREAD
    assert CAP not in se.CAP_UNREAD
    assert found.lines[2] == (
        "Судья сегмента: сайтов ≈ 26, токенов модели ≈ 83\u00a0200 (по 3\u00a0200 на сайт — "
        "запасное число: прогонов сбора с судьёй ещё нет) — ≈ $0,0645, "
        "дневной потолок модели продаж не прочитан из настроек — токенов ноль; доля вывода — "
        "запасная, замеров ещё нет: 30% токенов."
    )


async def test_unread_sales_cap_does_not_refuse_a_run_without_the_judge(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Судья сегмента выключен — модель не зовётся, токены не нужны: непрочитанный потолок модели
    такой прогон не держит."""
    monkeypatch.setattr(llm_cfg, "UNREAD_CAPS", frozenset({CAP}))
    monkeypatch.setattr(sales_cfg, "SEGMENT_JUDGE_MODE", JudgeMode.OFF)
    owner = await hypothesis(session)

    found = await se.estimate(session, ask(owner.id))

    assert (found.tokens, found.tokens_left, found.refusal) == (0, 0, None)


async def test_tokens_left_counts_as_before_when_the_cap_is_read(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Непрочитан другой потолок (не продаж) — смета продаж прежняя."""
    monkeypatch.setattr(llm_cfg, "UNREAD_CAPS", frozenset({"AGENT_DAILY_TOKEN_CAP"}))
    monkeypatch.setattr(llm_cfg, "SALES_DAILY_TOKEN_CAP", 41113)

    assert await se.tokens_left(session) == 41113


# --- недельный отчёт: лимит направления продаж не прочитан ----------------------------------------


async def test_unread_direction_limit_is_said_in_the_plan_not_zero_letters(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = WeekRows(session)
    await rows.box("grown@one.example.test", cap=23)
    monkeypatch.setattr(outreach_cfg, "DIRECTION_LIMITS", {"sales": 0})
    monkeypatch.setattr(outreach_cfg, "UNREAD_LIMITS", frozenset({LIMIT}))

    report = await weekly.build(session, WEEK, now=BUILT)

    runway = report.plan.runway
    assert runway.unread == LIMIT
    assert {day.letters for day in runway.days} == {0}
    plan = {section.key: section.lines for section in weekly_text.sections(report)}["plan"]
    assert plan[1] == f"разгон на 08.03–14.03: {weekly_text.RUNWAY_UNREAD}"
    assert LIMIT not in weekly_text.RUNWAY_UNREAD
    text = weekly_text.to_csv(report).decode("utf-8-sig")
    table = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert weekly_text.RUNWAY_UNREAD in [row[2] for row in table if row[0] == "план"]


async def test_read_direction_limit_keeps_the_plan_as_before(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = WeekRows(session)
    await rows.box("grown@one.example.test", cap=23)
    monkeypatch.setattr(outreach_cfg, "DIRECTION_LIMITS", {"sales": 37})
    monkeypatch.setattr(outreach_cfg, "UNREAD_LIMITS", frozenset())

    report = await weekly.build(session, WEEK, now=BUILT)

    assert report.plan.runway.unread is None
    plan = {section.key: section.lines for section in weekly_text.sections(report)}["plan"]
    assert weekly_text.RUNWAY_UNREAD not in plan[1]
