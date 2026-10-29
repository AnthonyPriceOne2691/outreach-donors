"""Письма, доставка которых откладывается, — блок «Доставка откладывается» на экране писем.

Письмо ушло, а сервер получателя его не принимает: платформа повторяет до 72 часов, и исход
придёт сам (`letters/deferrals.py`). До 29.10.2026 такое письмо на экране было просто
«отправлено»: отсрочки отбрасывались. **Смотреть может каждый, у кого есть доступ к базе**, как
очередь писем; письма продаж — ещё с правом «Продажи» (`require_stage`). Только чтение.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.letters.delivery_schemas import DeferredLettersView
from backend.api.letters.schemas import Audience, stage_two_only
from backend.api.stage_access import LETTERS
from backend.features.access.permissions import require_stage
from backend.features.core.domain import Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.letters.deferrals import waiting

router = APIRouter(prefix="/letters", tags=["письма"])

_viewer = Depends(needs(Permission.VIEW))


@router.get(
    "/deferred",
    response_model=DeferredLettersView,
    summary="Письма, доставка которых откладывается",
)
async def deferred(
    stage: Stage = Stage.DONORS,
    audience: Audience = "links",
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> DeferredLettersView:
    """Письма этапа и аудитории, ушедшие без исхода, у которых платформа откладывает доставку:
    первые — дольше всех, с причиной словами. Ничего не пишет."""
    try:
        stage_two_only(stage, audience)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    require_stage(actor(user), stage, LETTERS)
    return DeferredLettersView.of(stage, await waiting(session, stage=stage, audience=audience))
