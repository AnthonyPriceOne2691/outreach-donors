"""Прогон гипотезы «Плана работ»: новый — по пачке волны, или продолжение того же, что встал.

Зовёт проход конвейера (`plan_tick.py`) под замком строк плана и гипотезы, когда её ход.

**Новый прогон** (`new_run`) — пачка волны (`plan_waves.next_batch`): рынок, страницы волны, ключи
по порядку листа — не больше `SALES_PLAN_KEYS_PER_RUN` и предела ключей прогона выдачи. Пачки нет —
«нужны ключи», гипотеза `stopped`. До сметы — тормоза (`plan_gates`: повтор после сбоя, пауза между
прогонами, письма первыми, доля потолка модели): смета спрашивает модель проверкой и счёт выдачи, и
спрашивать их каждым проходом незачем. Смета — та же, что у экрана (`segment_estimate.estimate`):
выдача и тариф судьи — режима плана (`plan_store.PLAN_MODES`), язык писем — листа. Дальше:

- отказ сметы — по виду (`plan_failures.of_refusal`): у гипотезы — `stopped` и проблема словами
  отказа; у сервиса — план на паузу; временный — проблема и повтор не раньше `SALES_PLAN_RETRY_MIN`;
- судья сегодня и смета прогона выше доли конвейера в потолке модели (`plan_gates.model_share`) —
  проблема и повтор позже;
- не помещается в потолок гипотезы — `stopped` и «кончился бюджет»; в потолок плана — план на паузу
  (`plan_budget.room`: израсходовано и резерв идущих прогонов с этой сметой);
- иначе — `segment_launch.launch`: строка прогона, задача очереди и журнал без автора; отметки
  гипотезы плана (страница волны, снятые проблемы) уходят тем же коммитом. Очередь не ответила —
  прогона нет, а отметки остались: повтор — следующим проходом.

**Продолжение** (`resume`) — как «Продолжить» экрана (`api/sales/segment.resume_run`): тот же прогон
с места, где встал, выдачу второй раз не покупает; тормоза — те же, кроме сметы. Посреди пересуда
прогона — нет; номер держит задача умершего воркера — её убирают (`segment_watch.drop_dead`). Журнал —
«продолжение … — автоматически», без автора.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from redis.exceptions import RedisError
from rq import Queue
from rq.job import Job
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales_plan as plan_cfg
from backend.config import serp as serp_cfg
from backend.features.access.repository import AccessRepository
from backend.features.core import domain
from backend.features.core.domain import AuditAction
from backend.features.sales import deferred
from backend.features.sales.job_holder import holder
from backend.features.sales.plan_budget import Outcome, Problem, Spent, keys_words, room
from backend.features.sales.plan_failures import Kind, of_refusal
from backend.features.sales.plan_gates import QUEUE_SILENT, Gates, model_share, retry_after
from backend.features.sales.plan_models import (
    PlanMode,
    PlanStage,
    SalesPlanHypothesisModel,
    SalesPlanModel,
)
from backend.features.sales.plan_moves import Did, Turn, move, pause, solved, trouble
from backend.features.sales.plan_store import PLAN_MODES
from backend.features.sales.plan_waves import Batch, NeedKeys, next_batch
from backend.features.sales.segment_estimate import Ask, Estimate, estimate
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_launch import launch
from backend.features.sales.segment_models import SalesSegmentRunModel
from backend.features.sales.segment_rejudge import rejudge_job_id
from backend.features.sales.segment_watch import drop_dead
from backend.shared.queue import job_alive

logger = logging.getLogger(__name__)

#: Действие в журнале: остановленный или брошенный прогон плана продолжил конвейер.
RESUMED = "продолжение сбора базы продаж: конвейер плана работ — автоматически"
#: Начало слов проблемы, когда прогон не поставлен: отказ сметы или доля потолка модели.
NOT_PUT = "прогон не поставлен: "
NO_KEYS = "нужны ключи"
RUN_BUSY = "проход прогона уже стоит в очереди или идёт пересуд — продолжение после него"
JOB_TAKEN = "номер задачи свежего прогона занят — прогон заведён без задачи"
#: Проблемы, которые снимает заведённый прогон: значит, они решены.
SOLVED = frozenset({Problem.RUN_FAILED, Problem.BUDGET, Problem.KEYS})
#: Проблема, которую снимает продолжение прогона.
RETRIED = frozenset({Problem.RUN_FAILED})


def keys_per_run() -> int:
    """Ключей на прогон плана: настройка, но не больше предела ключей прогона выдачи."""
    return min(plan_cfg.KEYS_PER_RUN, serp_cfg.MAX_KEYWORDS_PER_RUN)


async def new_run(
    session: AsyncSession,
    gates: Gates,
    plan: SalesPlanModel,
    row: SalesPlanHypothesisModel,
    spent: dict[int, Spent],
    now: datetime,
) -> Turn:
    """Новый прогон гипотезы: пачка волны, тормоза, смета, потолки — и заведение (шапка модуля)."""
    batch = await next_batch(session, row, keys_per_run=keys_per_run())
    if isinstance(batch, NeedKeys):
        trouble(row, Problem.KEYS, keys_words(batch.max_pages, row.found, row.target), now)
        return move(row, PlanStage.STOPPED, now, why=NO_KEYS)
    held = retry_after(row, now) or await gates.held()
    if held is not None:
        return Turn(Did.WAITED, held)
    found = await estimate(session, ask(plan, row, batch), now=now)
    refused = await _judged(session, plan, row, found, spent, now)
    if refused is not None:
        return refused
    return await _launch(session, gates.jobs, row, batch, found)


def ask(plan: SalesPlanModel, row: SalesPlanHypothesisModel, batch: Batch) -> Ask:
    """Смета прогона пачки: ключи, рынок и страницы волны, язык листа, выдача и судья — режима
    плана."""
    pace = PLAN_MODES[PlanMode(plan.mode)]
    return Ask(
        hypothesis_id=row.hypothesis_id,
        keywords="\n".join(batch.keywords),
        country=batch.market,
        language=row.language,
        depth_pages=batch.wave.last,
        serp_mode=pace.serp_mode.value,
        judge_tier=pace.judge_tier.value,
        from_page=batch.wave.first,
    )


async def _judged(
    session: AsyncSession,
    plan: SalesPlanModel,
    row: SalesPlanHypothesisModel,
    found: Estimate,
    spent: dict[int, Spent],
    now: datetime,
) -> Turn | None:
    """Смета судит прогон: отказ — по виду, выше доли потолка модели — позже, потолки гипотезы и
    плана. Можно ставить — `None`."""
    if found.refusal is not None:
        return _refused(plan, row, found, now)
    held = await model_share(session, found.tokens, now)
    if held is not None:
        trouble(row, Problem.RUN_FAILED, NOT_PUT + held, now)
        return Turn(Did.WAITED, held)
    decided = room(
        hypothesis=spent.get(row.id, Spent()),
        hypothesis_cap=row.cap_usd,
        plan=sum(spent.values(), Spent()),
        plan_cap=plan.cap_usd,
        next_usd=found.serp_usd + found.tokens_usd,
    )
    words = decided.words or ""
    if decided.outcome is Outcome.HYPOTHESIS_CAP:
        trouble(row, Problem.BUDGET, words, now)
        return move(row, PlanStage.STOPPED, now, why=words)
    return pause(plan, words) if decided.outcome is Outcome.PLAN_CAP else None


def _refused(
    plan: SalesPlanModel, row: SalesPlanHypothesisModel, found: Estimate, now: datetime
) -> Turn:
    """Отказ сметы — по виду (`plan_failures.of_refusal`)."""
    refusal = found.refusal or ""
    kind = of_refusal(found)
    if kind is Kind.PAUSE:
        return pause(plan, refusal)
    trouble(row, Problem.RUN_FAILED, NOT_PUT + refusal, now)
    if kind is Kind.STOP:
        return move(row, PlanStage.STOPPED, now, why=refusal)
    return Turn(Did.WAITED, refusal)


async def _launch(
    session: AsyncSession,
    jobs: Queue,
    row: SalesPlanHypothesisModel,
    batch: Batch,
    found: Estimate,
) -> Turn:
    """Завести прогон пачки (`segment_launch.launch`): отметки гипотезы — тем же коммитом."""
    row.wave_page = batch.wave.first
    solved(row, SOLVED)
    try:
        launched = await launch(
            session, row.hypothesis_id, found, jobs=jobs, plan_hypothesis_id=row.id
        )
    except RedisError as exc:
        logger.warning(
            "конвейер плана: прогон не заведён — очередь не ответила",
            extra={"plan_hypothesis_id": row.id, "error": str(exc)},
        )
        return Turn(Did.WAITED, QUEUE_SILENT)
    if launched is None:
        logger.warning("конвейер плана: %s", JOB_TAKEN, extra={"plan_hypothesis_id": row.id})
        return Turn(Did.WAITED, JOB_TAKEN)
    logger.info(
        "конвейер плана: прогон заведён",
        extra={
            "plan_hypothesis_id": row.id,
            "run": launched.run.id,
            "market": batch.market,
            "pages": [batch.wave.first, batch.wave.last],
            "keys": len(found.keywords),
        },
    )
    return Turn(Did.LAUNCHED, run_id=launched.run.id)


async def resume(
    session: AsyncSession,
    gates: Gates,
    row: SalesPlanHypothesisModel,
    run: SalesSegmentRunModel,
    now: datetime,
) -> Turn:
    """Продолжить тот же прогон с места, где он встал (шапка модуля)."""
    held = retry_after(row, now) or await gates.held()
    if held is not None:
        return Turn(Did.WAITED, held)
    try:
        job = await asyncio.to_thread(_queued, gates.jobs, run.id)
    except RedisError as exc:
        logger.warning("конвейер плана: очередь не ответила", extra={"error": str(exc)})
        return Turn(Did.WAITED, QUEUE_SILENT)
    if job is None:
        return Turn(Did.WAITED, RUN_BUSY)
    solved(row, RETRIED)
    # Проход поставлен: прогон идёт, пока проход не кончится, — как у «Продолжить» экрана.
    run.job_id, run.finished_at = str(job.id), None
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=None,
        target=f"job:{job.id}",
        details={"действие": RESUMED, "прогон": run.id, "гипотеза плана": row.id},
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона
    )
    await session.commit()
    logger.info(
        "конвейер плана: прогон продолжен", extra={"run": run.id, "plan_hypothesis_id": row.id}
    )
    return Turn(Did.RESUMED, run_id=run.id)


def _queued(jobs: Queue, run_id: int) -> Job | None:
    """Проход продолжения — в очередь под номером прогона; идёт пересуд прогона или номер держит
    живая задача — `None`. Задачу умершего воркера rq держит «идёт» до таймаута — она убирается
    (`segment_watch.drop_dead`), и проход ставится снова. Клиент Redis синхронный — в потоке."""
    rejudge = rejudge_job_id(run_id)
    if holder(jobs, rejudge) is not None and job_alive(rejudge, jobs.connection) is not False:
        return None
    job = deferred.once(jobs, segment_job_id(run_id), SEGMENT_JOB, run_id)
    if job is None and drop_dead(jobs, run_id):
        job = deferred.once(jobs, segment_job_id(run_id), SEGMENT_JOB, run_id)
    return job
