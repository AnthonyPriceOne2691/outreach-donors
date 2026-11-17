"""Переходы конвейера «Плана работ»: этап гипотезы плана, её проблема, пауза плана — и итог прохода.

Пишут их проход (`plan_tick.py`) и постановка прогона (`plan_runs.py`) — одним путём: строка
журнала процесса с полями на каждый переход (гейт логов: значения — полями, а не в тексте), слова —
человеку в строку гипотезы или плана. Запись — в сессии вызывающего, коммит — за ним.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from backend.features.sales.plan_budget import Problem, problem, put
from backend.features.sales.plan_models import (
    PlanStage,
    PlanStatus,
    SalesPlanHypothesisModel,
    SalesPlanModel,
)

logger = logging.getLogger(__name__)


class Did(StrEnum):
    """Что сделал проход конвейера."""

    NOTHING = "nothing"  # планов нет или всё ждёт своей очереди
    GOING = "going"  # идёт прогон плана — числа пересчитаны
    WAITED = "waited"  # ставить есть что, но держит тормоз или очередь
    MOVED = "moved"  # переходы без постановки: этап гипотезы, пауза или конец плана
    LAUNCHED = "launched"  # прогон заведён
    RESUMED = "resumed"  # остановленный или брошенный прогон продолжен


#: Проход поставил задачу — второй в нём не будет.
ACTED = frozenset({Did.LAUNCHED, Did.RESUMED})


@dataclass(frozen=True, slots=True)
class Turn:
    """Итог прохода: что сделано, почему (ожидание, переход) и какой прогон."""

    did: Did
    why: str | None = None
    run_id: int | None = None


NOTHING = Turn(Did.NOTHING)


def move(row: SalesPlanHypothesisModel, stage: PlanStage, now: datetime, *, why: str) -> Turn:
    """Этап гипотезы плана: начало сбора и конец (`done`) — со временем."""
    fields: dict[str, Any] = {"plan_id": row.plan_id, "plan_hypothesis_id": row.id}
    logger.info(
        "конвейер плана: этап гипотезы плана",
        extra={**fields, "stage_was": row.stage, "stage": stage.value, "why": why},
    )
    row.stage = stage.value
    if stage is PlanStage.COLLECTING and row.started_at is None:
        row.started_at = now
    if stage is PlanStage.DONE:
        row.finished_at = now
    return Turn(Did.MOVED, why)


def trouble(row: SalesPlanHypothesisModel, code: Problem, words: str, at: datetime) -> None:
    """Проблема гипотезы плана — прежняя того же вида уходит (`plan_budget.put`)."""
    row.problems = put(row.problems, problem(code, words, at))


def solved(row: SalesPlanHypothesisModel, codes: frozenset[Problem]) -> None:
    """Проблемы этих видов решены — уходят из списка."""
    row.problems = [item for item in row.problems if item.get("code") not in codes]


def pause(plan: SalesPlanModel, words: str) -> Turn:
    """План — на паузу словами: так встало бы у каждой его гипотезы."""
    plan.status, plan.problem = PlanStatus.PAUSED.value, words
    logger.warning("конвейер плана: план на паузе", extra={"plan_id": plan.id, "why": words})
    return Turn(Did.MOVED, words)


def finish(plan: SalesPlanModel, now: datetime, *, why: str) -> Turn:
    """План окончен: все гипотезы в конце."""
    plan.status, plan.finished_at = PlanStatus.DONE.value, now
    logger.info("конвейер плана: план окончен", extra={"plan_id": plan.id, "why": why})
    return Turn(Did.MOVED, why)
