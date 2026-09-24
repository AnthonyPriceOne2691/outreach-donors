"""Исход фоновой задачи для экрана: что с ней сейчас и почему.

Экран, поставивший задачу, спрашивает здесь по её номеру — вместо того чтобы
ждать, появится ли результат. Смотрят все, у кого есть доступ к базе: итог
сборки писем или поиска контактов касается того, кто ждёт писем и адресов.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from backend.api.deps import needs
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.ops.job_outcome import JobOutcome, job_outcome

router = APIRouter(prefix="/jobs", tags=["задачи"])

_viewer = Depends(needs(Permission.VIEW))


class JobCard(BaseModel):
    """Исход задачи словами человека."""

    job_id: str
    kind: str
    #: queued | running | retry_wait | done | refused | failed | unknown
    state: str
    title: str
    error: str | None = None
    report: dict[str, Any] | None = None
    retries_left: int | None = None
    next_try_at: datetime | None = None
    ended_at: datetime | None = None

    @classmethod
    def of(cls, outcome: JobOutcome) -> JobCard:
        return cls(
            job_id=outcome.job_id,
            kind=outcome.kind,
            state=outcome.state,
            title=outcome.title,
            error=outcome.error,
            report=outcome.report,
            retries_left=outcome.retries_left,
            next_try_at=outcome.next_try_at,
            ended_at=outcome.ended_at,
        )


@router.get("/{job_id}", response_model=JobCard, summary="Исход фоновой задачи")
async def outcome(job_id: str, _: UserModel = _viewer) -> JobCard:
    found = job_outcome(job_id)
    if found is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Задачи с таким номером очередь не знает: итог хранится неделю, номер мог устареть",
        )
    return JobCard.of(found)
