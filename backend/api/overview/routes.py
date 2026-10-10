"""Главная: ключевые числа и то, что ждёт человека.

Смотрят все, у кого есть доступ к базе: те же числа видны им на своих
экранах по отдельности. К провайдерам главная не ходит — остаток у них
спрашивают экран расхода и сторож тишины, а сводку открывают чаще всего,
и каждый раз платить за неё запросом наружу незачем.

Числа у пунктов меню — отдельным ответом (`/overview/work`): меню спрашивает
их с каждого экрана раз в минуту, и считать ради них всю сводку незачем.

Обе — этапов, которые видит учётка: без права «Продажи» письма и ответы продаж
не считаются ни в сводке, ни у пунктов меню (решение Anthony 10.10.2026, П2).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.letters.schemas import Transport
from backend.api.overview.schemas import OverviewView, WorkView
from backend.features.access.permissions import visible_stages
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.ops.overview import overview, work

router = APIRouter(prefix="/overview", tags=["главная"])

_viewer = Depends(needs(Permission.VIEW))


@router.get("", response_model=OverviewView, summary="Ключевые числа и работа для человека")
async def summary(
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> OverviewView:
    counted = await overview(session, stages=visible_stages(actor(user)))
    return OverviewView.of(counted, transport=Transport.current())


@router.get(
    "/work", response_model=WorkView, summary="Сколько ждёт человека — числа у пунктов меню"
)
async def menu_work(
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> WorkView:
    return WorkView.of(await work(session, stages=visible_stages(actor(user))))
