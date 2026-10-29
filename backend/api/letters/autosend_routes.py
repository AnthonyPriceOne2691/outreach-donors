"""Очередь, которая уходит сама (`letters/autosend.py`): кнопка «Остановить» под пачкой.

Включает режим отметка «дальше — сама» в окне пачки (`routes.send_queue`), а останавливает —
эта кнопка или сам проход, когда очередь пуста. Остановка — строкой журнала, с тем, кто нажал:
письма очереди остаются в очереди и уходят дальше только кнопкой. Право — то же, что у пачки.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.letters.autosend_schemas import QueueAutosendView
from backend.api.letters.routes import autosend_details
from backend.api.letters.schemas import QueueAutosendBody
from backend.api.stage_access import LETTERS
from backend.features.access.permissions import require_stage
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.letters import autosend

router = APIRouter(prefix="/letters", tags=["письма"])

_sender = Depends(needs(Permission.SEND))

#: Отказ, когда очередь и так сама не уходит.
NOT_RUNNING = "Эта очередь и так не уходит сама — останавливать нечего"


@router.post(
    "/send-queue/stop",
    response_model=QueueAutosendView,
    summary="Остановить очередь, которая уходит сама",
)
async def stop(
    body: QueueAutosendBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> QueueAutosendView:
    """Очередь этапа и аудитории больше не уходит сама: письма остаются в очереди."""
    require_stage(actor(author), body.stage, LETTERS)
    mode = await autosend.running(session, body.stage, body.audience)
    if mode is None:
        raise HTTPException(status.HTTP_409_CONFLICT, NOT_RUNNING)
    autosend.finish(mode, by=author.id, reason=autosend.BY_HUMAN, now=datetime.now(UTC))
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author.id,
        target=f"queue_autosend:{mode.id}",
        details=autosend_details("очередь больше не уходит сама", body.stage, body.audience),
    )
    await session.commit()
    return QueueAutosendView.of(await autosend.state(session, body.stage, body.audience))
