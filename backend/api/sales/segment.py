"""Прогон гипотезы с экрана: смета до запуска, запуск задачей, отчёт и решение человека.

`GET /sales/segment?hypothesis=N` — вкладка сбора: описание сегмента, режим судьи, рынки
выдачи, языки писем, границы формы и прогоны гипотезы. `PATCH /sales/hypotheses/{N}` —
описание сегмента: его читает судья сегмента (правка — новый вопрос модели, прежние вердикты
по нему не годятся).

`POST /sales/segment/estimate` — смета без записи: выдача, модель, Hunter строкой и отказ
словами. `POST /sales/segment/runs` — запуск: смета считается заново на сервере, отказ — 409
словами до очереди задач; иначе прогон заводится и встаёт задачей очереди продаж
(`sales/segment_jobs.py`). `POST /sales/segment/runs/{N}/resume` — продолжить прогон: после
потолка модели, сбоя или решений человека по «на проверку».

`GET /sales/segment/runs/{N}` — прогон и его отчёт; `POST /sales/segment/candidates/{N}/decision`
— решение человека по сайту «на проверку».

**Права.** Смотреть, считать смету и решать «на проверку» — `sales`. Запуск и продолжение —
`sales` и `run`: выдача, модель и платный поиск адресов — траты, как у прогона доноров и
очистки лидов.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.letters.once import enqueue_once
from backend.api.sales.segment_schemas import (
    CandidateRow,
    DecisionBody,
    EstimateView,
    RunDetail,
    RunQueued,
    RunRow,
    SegmentAsk,
    SegmentBody,
    SegmentCard,
    SegmentHome,
)
from backend.config import sales as sales_cfg
from backend.features.access.repository import AccessRepository
from backend.features.core import domain
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.sales import chain_text, segment_report
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import SalesHypothesisModel
from backend.features.sales.segment_estimate import Ask, estimate
from backend.features.sales.segment_jobs import SEGMENT_JOB, segment_job_id
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from backend.features.serp import markets
from backend.shared.queue import sales_queue, with_retries

logger = logging.getLogger(__name__)

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`).
router = APIRouter()

_seller = Depends(needs(Permission.SALES))
_runner = Depends(needs(Permission.RUN))

RUN_GOING = (
    "Этот прогон уже идёт — дождитесь его итога: второй проход судил бы и искал адреса "
    "тем же сайтам и заплатил бы дважды"
)
UNKNOWN_RUN = "прогона сбора №{run_id} нет — обновите список прогонов"


def _ask(body: SegmentAsk) -> Ask:
    return Ask(
        hypothesis_id=body.hypothesis_id,
        keywords=body.keywords,
        country=body.country,
        language=body.language,
        depth_pages=body.depth_pages,
    )


async def _hypothesis(session: AsyncSession, hypothesis_id: int) -> SalesHypothesisModel:
    found = await session.get(SalesHypothesisModel, hypothesis_id)
    if found is None:
        raise UnknownHypothesisError(f"гипотезы №{hypothesis_id} нет — обновите список гипотез")
    return found


@router.get("/segment", response_model=SegmentHome, summary="Сбор базы гипотезы: форма и прогоны")
async def read_segment(
    hypothesis: int = Query(ge=1, description="номер гипотезы"),
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SegmentHome:
    found = await _hypothesis(session, hypothesis)
    runs = await segment_report.runs_of(session, hypothesis)
    return SegmentHome(
        hypothesis_id=hypothesis,
        segment=found.description,
        mode=sales_cfg.SEGMENT_JUDGE_MODE.value,
        countries=sorted(markets.SERP_LANGUAGE),
        languages=list(chain_text.LANGUAGES),
        runs=[RunRow.of(run) for run in runs],
    )


@router.patch(
    "/hypotheses/{hypothesis_id}",
    response_model=SegmentCard,
    summary="Описание сегмента гипотезы",
)
async def write_segment(
    hypothesis_id: int,
    body: SegmentBody,
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SegmentCard:
    """Описание сегмента — данными в базе: репозиторий публичный. Пусто — описания нет."""
    found = await _hypothesis(session, hypothesis_id)
    found.description = body.description.strip() or None
    await session.commit()
    return SegmentCard(hypothesis_id=hypothesis_id, description=found.description)


@router.post("/segment/estimate", response_model=EstimateView, summary="Смета прогона: без записи")
async def read_estimate(
    body: SegmentAsk, _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> EstimateView:
    return EstimateView.of(await estimate(session, _ask(body)))


@router.post(
    "/segment/runs",
    response_model=RunQueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Запустить прогон гипотезы",
)
async def start_run(
    body: SegmentAsk,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> RunQueued:
    """Смета — заново, на сервере: выше потолка или негодный запрос — 409 словами, до
    очереди задач. Прогон и запись журнала — одна транзакция с постановкой."""
    found = await estimate(session, _ask(body))
    if found.refusal is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Прогон не запущен: {found.refusal}")
    run = SalesSegmentRunModel(
        hypothesis_id=body.hypothesis_id,
        keywords=found.keywords,
        country=found.country,
        language=found.language,
        depth_pages=found.depth_pages,
        mode=found.mode,
        estimate=found.as_dict(),
        report={},
        stage=Stage.QUEUED,
        created_by=author.email,
    )
    session.add(run)
    # Строка прогона — в базе до постановки: задача читает её своей сессией, и взятая воркером
    # раньше коммита не нашла бы прогона. Очередь не ответила — строка уходит: прогона без
    # задачи, который «стоит в очереди», нет.
    await session.commit()
    try:
        job = await asyncio.to_thread(
            enqueue_once,
            sales_queue(),
            segment_job_id(run.id),
            SEGMENT_JOB,
            run.id,
            **with_retries(),
        )
    except RedisError:
        await session.delete(run)
        await session.commit()
        raise
    if job is None:  # номер свежего прогона занят быть не может — но молча не теряем
        raise HTTPException(status.HTTP_409_CONFLICT, RUN_GOING)
    run.job_id = str(job.id)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details={
            "действие": "сбор базы продаж",
            "гипотеза": found.hypothesis,
            "ключей": len(found.keywords),
            "рынок": found.country,
            "смета": " ".join(found.lines),
        },
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона (`Stage` ниже)
    )
    await session.commit()
    return RunQueued(run_id=run.id, job_id=str(job.id))


@router.post(
    "/segment/runs/{run_id}/resume",
    response_model=RunQueued,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Продолжить прогон гипотезы",
)
async def resume_run(
    run_id: int,
    author: UserModel = _seller,
    __: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> RunQueued:
    """Тот же прогон с места, где он стоит: выдачу второй раз не покупает."""
    run = await session.get(SalesSegmentRunModel, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, UNKNOWN_RUN.format(run_id=run_id))
    job = await asyncio.to_thread(
        enqueue_once, sales_queue(), segment_job_id(run.id), SEGMENT_JOB, run.id, **with_retries()
    )
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, RUN_GOING)
    run.job_id = str(job.id)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details={"действие": "продолжение сбора базы продаж", "прогон": run.id},
        stage=domain.Stage.SALES,  # этап воронки, а не стадия прогона (`Stage` ниже)
    )
    await session.commit()
    return RunQueued(run_id=run.id, job_id=str(job.id))


@router.get("/segment/runs/{run_id}", response_model=RunDetail, summary="Прогон гипотезы и отчёт")
async def read_run(
    run_id: int, _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> RunDetail:
    view = await segment_report.run_view(session, run_id)
    if view is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, UNKNOWN_RUN.format(run_id=run_id))
    return RunDetail.of(view)


@router.post(
    "/segment/candidates/{candidate_id}/decision",
    response_model=CandidateRow,
    summary="Решение человека по сайту «на проверку»",
)
async def decide(
    candidate_id: int,
    body: DecisionBody,
    author: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> CandidateRow:
    try:
        candidate = await segment_report.decide(
            session, candidate_id, accept=body.accept, author=author.email
        )
    except segment_report.UnknownCandidateError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except segment_report.DecisionRefusedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    host = await session.scalar(
        select(DomainModel.host).where(DomainModel.id == candidate.domain_id)
    )
    await session.commit()
    return CandidateRow.of(candidate, str(host))
