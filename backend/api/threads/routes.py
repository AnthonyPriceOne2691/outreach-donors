"""Диалоги: список и переписка целиком.

Смотреть переписку может каждый, у кого есть доступ к базе: цена,
полученная в письме, — это то же содержимое базы, что и метрики донора.
Отправка и ответы — другое право и другой срез.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.threads.schemas import ThreadCard, ThreadView
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.outreach.repository import OutreachRepository

router = APIRouter(prefix="/threads", tags=["диалоги"])

_viewer = Depends(needs(Permission.VIEW))


@router.get("", response_model=list[ThreadCard], summary="Список диалогов")
async def all_threads(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> list[ThreadCard]:
    rows = await OutreachRepository(session).threads()
    return [ThreadCard.of(row) for row in rows]


@router.get("/{thread_id}", response_model=ThreadView, summary="Переписка целиком")
async def one_thread(
    thread_id: int,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> ThreadView:
    return ThreadView.of(await OutreachRepository(session).thread(thread_id))
