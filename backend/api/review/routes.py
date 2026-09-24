"""Рассмотрение прогона: принять или отклонить предложенных доноров.

**Смотреть — под `view`, решать — под `prices`.** Тот же выбор, что
у экрана «Отбор»: `prices` — право «человек поправляет то, что решила
машина». Решение меняет, кому уйдёт письмо, поэтому пишется в журнал.

**Принятие ставит поиск контактов сразу.** Контакт ищется после решения
человека, а не до: так же в соседней системе, и платный поиск не тратится
на бренды и госсайты.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.review.schemas import AccuracyView, DecideBody, DecideResult, ReviewView
from backend.features.access.repository import AccessRepository
from backend.features.contacts.repository import ContactRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.review.candidates import Decision, RunReview
from backend.features.review.keyword_yield import run_yield
from backend.shared.queue import CONTACTS_JOB, remember_contacts_job, runs_queue, with_retries

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/review", tags=["рассмотрение"])

_viewer = Depends(needs(Permission.VIEW))
_reviewer = Depends(needs(Permission.PRICES))


@router.get("/runs/{run_id}", response_model=ReviewView, summary="Кандидаты прогона")
async def review(
    run_id: int,
    status: Decision = Query(
        default=Decision.PENDING, description="предложены, приняты, отклонены"
    ),
    show_doubtful: bool = Query(
        default=False, description="показать тех, кому судья советует отказ"
    ),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> ReviewView:
    page = await RunReview(session).page(run_id, status=status, show_doubtful=show_doubtful)
    return ReviewView.of(page, await run_yield(session, page.run))


@router.post("/runs/{run_id}/decide", response_model=DecideResult, summary="Решение человека")
async def decide(
    run_id: int,
    body: DecideBody,
    author: UserModel = _reviewer,
    session: AsyncSession = Depends(db_session),
) -> DecideResult:
    report = await RunReview(session).decide(
        run_id, body.candidate_ids, body.decision, by=author.email, note=body.note
    )
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author.id,
        target=f"run:{run_id}",
        details={
            "действие": "рассмотрение прогона",
            "решение": body.decision.value,
            "кандидатов": report.changed,
        },
    )
    await session.commit()

    job_id: str | None = None
    if report.accepted_domains:
        pending = await ContactRepository(session).pending_count()
        job = runs_queue().enqueue(
            CONTACTS_JOB,
            max(pending, len(report.accepted_domains)),
            False,
            False,
            **with_retries(),
        )
        job_id = str(job.id)
        remember_contacts_job(job_id)
        logger.info(
            "рассмотрение: принято %s — поиск контактов поставлен", len(report.accepted_domains)
        )
    return DecideResult(
        changed=report.changed, accepted=len(report.accepted_domains), contacts_job_id=job_id
    )


@router.get("/accuracy", response_model=AccuracyView, summary="Судья против человека")
async def accuracy(
    run_id: int | None = Query(default=None, description="один прогон; пусто — все"),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> AccuracyView:
    return AccuracyView.of(await RunReview(session).accuracy(run_id))
