"""«Переобойти спорные»: смета, запуск, ход и итог.

Смотреть — под правом `view`, запускать — под `run`: тратятся токены модели. Судья только советует:
решения людей, очередь и письма разбор не трогает (`crawl/judge_pass.py`).

**Запуск — по смете, которую видел человек.** `cases` — её число, и больше задача не возьмёт, сколько
бы спорных ни пришло, пока окно было открыто. Сервер считает смету заново: потолок могли выбрать.

**Ход и итог — в самой очереди**, своей строки в базе у задачи нет, как у поиска адресов: числа нужны,
пока их смотрят, суды и расход записаны в базу сразу. Запуск — в журнале действий.

**«Идёт» проверяется воркером** (`queue.job_alive`): обходчик умер вместе с задачей, а статус rq
снимет не сразу — экран говорит «оборвался», и номер задачи освобождается для нового запуска.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.letters.once import enqueue_once, forget_job
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.crawl import judge_estimate
from backend.features.ops.job_outcome import JobOutcome, job_outcome
from backend.shared.database.ids import INT4_MAX
from backend.shared.queue import (
    CRAWL_QUEUE_NAME,
    JUDGE_DISPUTED_JOB,
    JUDGE_DISPUTED_JOB_ID,
    JUDGE_DISPUTED_TIMEOUT,
    RESULT_TTL,
    crawl_queue,
    job_alive,
    job_meta,
    workers_alive,
)

router = APIRouter(prefix="/judge-pass")

_viewer = Depends(needs(Permission.VIEW))
_runner = Depends(needs(Permission.RUN))

ALREADY_RUNNING = "Разбор спорных уже идёт: дождитесь его итога, второй заплатил бы модели дважды"
NOTHING_TO_DO = "Спорных к разбору нет: новые появятся после обхода"
NO_WORKERS = "Обходчики не запущены: разбор некому взять"
WORKER_DIED = "обходчик перезапустился посреди разбора"

State = Literal["idle", "queued", "running", "done", "stopped", "failed", "unknown"]


class JudgePassEstimate(BaseModel):
    """Смета словами сервера: экран их только называет."""

    cases: int
    lines: list[str]
    #: Почему не запустить — словами; `None` — можно.
    refusal: str | None


class JudgePassView(BaseModel):
    """Смета и последний разбор. `state`: idle — разбора не было или итог истёк; queued и running —
    идёт; done — окончен; stopped — встал с причиной (`why`); failed — упал или оборвался; unknown —
    очередь не отвечает. Кроме done, следующий запуск продолжает с места."""

    estimate: JudgePassEstimate
    state: State
    done: int = 0
    total: int = 0
    by: str | None = None
    ended_at: datetime | None = None
    lines: list[str] = Field(default_factory=list)
    why: str | None = None


class JudgePassBody(BaseModel):
    """Сколько спорных в смете, которую подтвердил человек."""

    cases: int = Field(ge=1, le=INT4_MAX)


@dataclass(frozen=True, slots=True)
class _Seen:
    """Что очередь знает о разборе."""

    outcome: JobOutcome | None
    alive: bool | None
    meta: dict[str, Any]
    workers: int | None


def _ask_queue() -> _Seen:
    """Клиент Redis синхронный: в пуле потоков, а процесс API один (аудит 10.10.2026)."""
    return _Seen(
        job_outcome(JUDGE_DISPUTED_JOB_ID),
        job_alive(JUDGE_DISPUTED_JOB_ID),
        job_meta(JUDGE_DISPUTED_JOB_ID),
        workers_alive(queue=CRAWL_QUEUE_NAME),
    )


def _ended(outcome: JobOutcome, said: dict[str, Any]) -> dict[str, Any]:
    """Разбор кончился: итог, остановка с причиной или падение."""
    ended = {**said, "ended_at": outcome.ended_at}
    report = outcome.report
    if outcome.state != "done" or report is None:
        return {**ended, "state": "failed", "why": outcome.error}
    stopped = report.get("stopped") or None
    return {
        **ended,
        "state": "stopped" if stopped else "done",
        "done": int(report.get("done", 0)),
        "total": int(report.get("total", 0)),
        "lines": list(report.get("lines", [])),
        "why": stopped,
    }


def _job_state(seen: _Seen) -> dict[str, Any]:
    """Поля вида о разборе — по исходу задачи (`ops/job_outcome.py`) и живости её воркера."""
    outcome = seen.outcome
    if outcome is None:
        return {"state": "idle"}
    if outcome.state == "unknown":
        return {"state": "unknown", "why": outcome.error}
    said = {
        "by": seen.meta.get("by"),
        "done": int(seen.meta.get("done", 0)),
        "total": int(seen.meta.get("total", 0)),
    }
    if outcome.state == "running":
        if seen.alive is False:
            return {**said, "state": "failed", "why": WORKER_DIED}
        return {**said, "state": "running"}
    if outcome.state in ("queued", "retry_wait"):
        return {**said, "state": "queued"}
    return _ended(outcome, said)


async def _view(session: AsyncSession) -> JudgePassView:
    found = await judge_estimate.estimate(session)
    seen = await asyncio.to_thread(_ask_queue)
    refusal = found.refusal or (NO_WORKERS if seen.workers == 0 else None)
    return JudgePassView(
        estimate=JudgePassEstimate(cases=found.cases, lines=found.lines, refusal=refusal),
        **_job_state(seen),
    )


@router.get("", response_model=JudgePassView, summary="Переобход спорных: смета, ход и итог")
async def read_judge_pass(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> JudgePassView:
    """Смета считается заново на каждый вопрос: только чтение, модель не зовётся."""
    return await _view(session)


def _enqueue(cases: int, by: str) -> bool:
    """Поставить разбор; `False` — один уже идёт. Встаёт в начало очереди обходов."""
    jobs = crawl_queue()
    if job_alive(JUDGE_DISPUTED_JOB_ID) is False:
        forget_job(jobs, JUDGE_DISPUTED_JOB_ID)
    job = enqueue_once(
        jobs,
        JUDGE_DISPUTED_JOB_ID,
        JUDGE_DISPUTED_JOB,
        cases,
        at_front=True,
        job_timeout=JUDGE_DISPUTED_TIMEOUT,
        result_ttl=RESULT_TTL,
        meta={"by": by, "total": cases},
    )
    return job is not None


@router.post(
    "",
    response_model=JudgePassView,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Переобойти спорные по смете",
)
async def start_judge_pass(
    body: JudgePassBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> JudgePassView:
    """Поставить разбор: не больше спорных, чем в подтверждённой смете. Отказ — 409 словами."""
    found = await judge_estimate.estimate(session)
    if found.refusal is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, found.refusal)
    if found.cases == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, NOTHING_TO_DO)
    cases = min(body.cases, found.cases)
    if not await asyncio.to_thread(_enqueue, cases, author.email):
        raise HTTPException(status.HTTP_409_CONFLICT, ALREADY_RUNNING)
    await AccessRepository(session).record(
        AuditAction.ADVERTISER_REVIEWED,
        author_id=author.id,
        target="advertisers:judge-pass",
        details={"действие": "переобход спорных", "случаев": cases, "токенов до": found.tokens},
    )
    await session.commit()
    return await _view(session)
