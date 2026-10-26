"""Прогноз рассылки этапа — экран «Рассылка»: сколько первых писем ждёт, когда уйдёт очередь,
мощность ящиков по дням с разгоном, план и факт по дням, ящики и их ступени (`letters/forecast.py`).

**Смотреть может каждый, у кого есть доступ к базе** (`Permission.VIEW`), как очередь писем; этап
продаж — ещё с правом «Продажи» (решение Anthony 10.10.2026, П2): очередь и ящики продаж — данные
этапа, и без права — 403 словами (`require_stage`), как у остальных общих экранов. Только чтение.

**Дней — не больше месяца** вперёд и назад (`DAYS_MAX`): ответ и счёт растут с числом дней, а
прогноз дальше месяца — уже не план.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.letters.forecast_schemas import MailingForecastView
from backend.api.stage_access import LETTERS
from backend.features.access.permissions import require_stage
from backend.features.core.domain import Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.letters.forecast import forecast

router = APIRouter(prefix="/letters", tags=["письма"])

_viewer = Depends(needs(Permission.VIEW))

#: Дней плана и факта — не больше месяца.
DAYS_MAX = 31


@router.get("/forecast", response_model=MailingForecastView, summary="Прогноз рассылки этапа")
async def mailing_forecast(
    stage: Stage = Stage.DONORS,
    days: int = Query(default=7, ge=1, le=DAYS_MAX, description="дней плана, считая сегодня"),
    back: int = Query(default=7, ge=0, le=DAYS_MAX, description="дней факта назад, без сегодня"),
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> MailingForecastView:
    """Прогноз этапа на `days` дней от сегодня и факт за `back` дней назад. Ничего не пишет."""
    require_stage(actor(user), stage, LETTERS)
    return MailingForecastView.of(await forecast(session, stage, days=days, back=back))
