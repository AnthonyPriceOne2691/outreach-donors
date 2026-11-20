"""Переходы конвейера «Плана работ»: этап гипотезы плана, её проблема, пауза плана — и итог прохода.

Пишут их проход (`plan_tick.py`) и постановка прогона (`plan_runs.py`) — одним путём: строка
журнала процесса с полями на каждый переход (гейт логов: значения — полями, а не в тексте), слова —
человеку в строку гипотезы или плана. Запись — в сессии вызывающего, коммит — за ним.

**Слова «нужны ключи» следуют за «найдено»** (`restate_keys`): их число — снимок на момент
остановки, а «найдено» пересчитывают и после неё (`plan_found.recount_row`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from backend.features.sales.plan_budget import Problem, keys_words, problem, put, reworded
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


def restate_keys(row: SalesPlanHypothesisModel) -> None:
    """Слова «нужны ключи» — на нынешнее «найдено» строки (`plan_budget.keys_words`). Стенд 20.11:
    слова «…найдено 24 из 100 — добавьте ключи» — снимок на момент остановки, а после «Принять»
    рядом уже 25. Проблемы «нужны ключи» нет — ничего.

    Слова меняются **на месте** (`plan_budget.reworded`), а не через `trouble`: порядок проблем —
    признак того, на чём гипотеза встала (`plan_budget.stopped_on_cap` берёт последнюю из проблем
    остановки), а время — когда кончились ключи; пересчёт числа ни того ни другого не меняет.
    Признаком состояния слова не служат: «нужны ключи» узнают по виду (`plan_actions.keys_back`,
    кнопка «Добавить ключи» экрана).

    **Цель набрана** (найдено ≥ цели) — проблема снимается: «нужны ключи» в коде значит «ключи и
    глубина кончились, а цель не набрана» (`plan_actions.resume_hypothesis` отказывает только так),
    и слова с просьбой о ключах спорили бы с числом рядом; уходит и кнопка «Добавить ключи». Этап не
    трогается: остановленная гипотеза идёт в письма только через «Продолжить» — проход её не
    переводит (остановленная хода не получает, а в письмах она держала бы очередь «одна вперёд»,
    `plan_tick`), и «Продолжить» при набранной цели не отказывает. Другие проблемы — как были.
    Отмена решения, вернувшая число ниже цели, проблему не возвращает: что ключей нет, снова скажут
    «Продолжить» (отказ словами) и «Собрать ещё» — как у гипотезы, остановленной кнопкой."""
    if not any(item.get("code") == Problem.KEYS for item in row.problems):
        return
    if row.found >= row.target:
        solved(row, frozenset({Problem.KEYS}))
        logger.info(
            "конвейер плана: «нужны ключи» сняты — цель набрана",
            extra={
                "plan_id": row.plan_id,
                "plan_hypothesis_id": row.id,
                "found": row.found,
                "target": row.target,
            },
        )
        return
    words = keys_words(row.max_pages, row.found, row.target)
    row.problems = reworded(row.problems, Problem.KEYS, words)


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
