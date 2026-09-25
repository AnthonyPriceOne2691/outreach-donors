"""Главная: ключевые числа и то, что ждёт человека.

Смотрят все, у кого есть доступ к базе: те же числа видны им на своих
экранах по отдельности. К провайдерам главная не ходит — остаток у них
спрашивают экран расхода и сторож тишины, а сводку открывают чаще всего,
и каждый раз платить за неё запросом наружу незачем.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.letters.schemas import Transport
from backend.api.overview.schemas import OverviewView
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.ops.overview import overview

router = APIRouter(prefix="/overview", tags=["главная"])

_viewer = Depends(needs(Permission.VIEW))


@router.get("", response_model=OverviewView, summary="Ключевые числа и работа для человека")
async def summary(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> OverviewView:
    return OverviewView.of(await overview(session), transport=Transport.current())
