"""Пороги и расход.

Пороги правит оператор — это работа с базой, а не с деньгами; но правка
заводит **новую версию**, а не переписывает старую: вердикты прошлых
прогонов должны оставаться объяснимыми.

Расход смотрят все: это то же содержимое базы, что и доноры.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.settings.schemas import (
    ConsequencesView,
    SpendingView,
    ThresholdsBody,
    ThresholdsVersion,
    ThresholdsView,
)
from backend.config import ahrefs as ahrefs_cfg
from backend.features.access.repository import AccessRepository
from backend.features.ahrefs.client import AhrefsClient, AhrefsError
from backend.features.ahrefs.units import Quota
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.runs.spending import SpendingRepository
from backend.features.runs.thresholds import ThresholdsRepository, consequences, defaults

logger = logging.getLogger(__name__)
router = APIRouter(tags=["настройки"])

_settler = Depends(needs(Permission.SETTINGS))
_viewer = Depends(needs(Permission.VIEW))


def _defaults_body() -> ThresholdsBody:
    values = defaults()
    return ThresholdsBody(
        min_dr=values.min_dr,
        min_org_traffic=values.min_org_traffic,
        min_refdomains=values.min_refdomains,
        min_keywords=values.min_keywords,
    )


@router.get("/settings/thresholds", response_model=ThresholdsView, summary="Пороги и их версии")
async def current_thresholds(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> ThresholdsView:
    repository = ThresholdsRepository(session)
    current = await repository.current()
    return ThresholdsView(
        current=None if current is None else ThresholdsVersion.of(current),
        defaults=_defaults_body(),
        history=[ThresholdsVersion.of(version) for version in await repository.history()],
    )


@router.post(
    "/settings/preview",
    response_model=ConsequencesView,
    summary="Последствия порогов до сохранения",
)
async def preview(
    body: ThresholdsBody,
    _: UserModel = _settler,
    session: AsyncSession = Depends(db_session),
) -> ConsequencesView:
    """Пересчитать базу новыми порогами, ничего не сохраняя.

    Считается тем же правилом, что и отбор: второй экземпляр правила
    разошёлся бы с настоящим при первой правке, и экран показывал бы
    последствия, которых не будет.
    """
    repository = ThresholdsRepository(session)
    donors = await repository.donors()
    return ConsequencesView.of(consequences(donors, body.to_thresholds()))


@router.post(
    "/settings/thresholds",
    response_model=ThresholdsVersion,
    summary="Новая версия порогов",
)
async def save_thresholds(
    body: ThresholdsBody,
    author: UserModel = _settler,
    session: AsyncSession = Depends(db_session),
) -> ThresholdsVersion:
    repository = ThresholdsRepository(session)
    settings = await repository.save(body.to_thresholds(), author=author.email)
    await AccessRepository(session).record(
        AuditAction.THRESHOLDS_CHANGED,
        author_id=author.id,
        target=f"settings:{settings.id}",
        details={
            "версия": settings.version,
            "dr": body.min_dr,
            "трафик": body.min_org_traffic,
            "реф. домены": body.min_refdomains,
            "ключи": body.min_keywords,
        },
    )
    await session.commit()
    return ThresholdsVersion.of(settings)


@router.get("/usage", response_model=SpendingView, summary="Расход по статьям")
async def usage(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> SpendingView:
    """Расход с начала месяца и остаток у провайдера.

    Недоступный остаток — не повод прятать расход: своя таблица знает,
    на что мы потратили, и этот ответ не зависит от провайдера. Поэтому
    неудача запроса остатка отдаётся отдельным полем, а не пятисоткой.
    """
    spending = await SpendingRepository(session).since_month_start()

    # Спрашивается сырой остаток провайдера, а не бюджет прогона: бюджет —
    # это меньшее из остатка и капа, и сравнивать его с нашим расходом
    # бессмысленно (первая версия экрана так и показывала «израсходовано
    # 0» при шести тысячах потраченных).
    left: int | None = None
    error: str | None = None
    client = AhrefsClient()
    try:
        left = Quota.from_payload(await client.limits_and_usage()).available
    except (AhrefsError, OSError) as exc:
        logger.warning("расход: остаток у Ahrefs недоступен (%s)", exc)
        error = (
            "Не удалось узнать остаток у Ahrefs. Расход ниже — из своей таблицы, "
            "он от провайдера не зависит."
        )
    finally:
        await client.aclose()

    return SpendingView.of(spending, ahrefs_left=left, ahrefs_cap=ahrefs_cfg.UNITS_CAP, error=error)
