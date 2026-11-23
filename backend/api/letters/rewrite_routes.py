"""«Переписать шаблонные · N» на экране писем (`letters/rewrite_queue.py`).

Письма очереди, оставшиеся шаблоном из-за отказа модели, переписываются заново задачей — тем
же путём, что сборка. Право — как у «Собрать очередь»: это вызовы модели и текст, который уйдёт
адресату. Задача одна на этап и аудиторию за раз (`once.py`); нажатие — строкой журнала с тем,
кто нажал, как у сборки.

**Пока очередь уходит сама — отказ словами** (`letters/autosend.py`): шаблонные письма уходили
бы раньше, чем до них дойдёт модель, — а ради них очередь и останавливают.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.letters import once
from backend.api.letters.routes import autosend_details
from backend.api.letters.schemas import Audience, stage_two_only
from backend.api.stage_access import LETTERS
from backend.features.access.permissions import require_stage
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.letters import autosend
from backend.features.letters.rewrite_queue import (
    NOTHING,
    SENDING_ITSELF,
    TAKE_MAX,
    rewritable_stage,
    templated_count,
)
from backend.shared.queue import REWRITE_JOB, runs_queue, with_retries

router = APIRouter(prefix="/letters", tags=["письма"])

_sender = Depends(needs(Permission.SEND))


class RewriteBody(BaseModel):
    """Чьи шаблонные письма переписать: этап и, на Этапе 2, аудитория."""

    stage: Stage = Stage.DONORS
    audience: Audience = "links"

    @model_validator(mode="after")
    def _niche_is_stage_two(self) -> RewriteBody:
        stage_two_only(self.stage, self.audience)
        return self


class RewriteQueued(BaseModel):
    """Переписывание ушло в очередь задач: каждое письмо — вызов модели, это минуты."""

    job_id: str
    #: Сколько писем возьмёт задача: шаблонных в очереди, но не больше её потолка.
    letters: int


@router.post(
    "/rewrite", response_model=RewriteQueued, summary="Переписать шаблонные письма очереди"
)
async def rewrite(
    body: RewriteBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> RewriteQueued:
    """Поставить переписывание шаблонных писем очереди этапа и аудитории. Ничего не отправляет."""
    require_stage(actor(author), body.stage, LETTERS)
    stage = rewritable_stage(body.stage)
    found = await templated_count(session, stage, body.audience)
    if found == 0:
        raise HTTPException(status.HTTP_409_CONFLICT, NOTHING)
    if await autosend.running(session, stage, body.audience) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, SENDING_ITSELF)
    # Клиент Redis синхронный — постановка в потоке, как у сборки (`routes._enqueue_once`).
    job = await asyncio.to_thread(
        once.enqueue_once,
        runs_queue(),
        once.rewrite_job_id(stage, body.audience),
        REWRITE_JOB,
        stage.value,
        body.audience,
        **with_retries(),
    )
    if job is None:
        raise HTTPException(status.HTTP_409_CONFLICT, once.REWRITE_RUNNING)
    taken = min(found, TAKE_MAX)
    await AccessRepository(session).record(
        AuditAction.RUN_STARTED,
        author_id=author.id,
        target=f"job:{job.id}",
        details=autosend_details("переписывание шаблонных писем", stage, body.audience)
        | {"писем": str(taken)},
    )
    await session.commit()
    return RewriteQueued(job_id=str(job.id), letters=taken)
