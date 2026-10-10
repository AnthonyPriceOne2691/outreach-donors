"""Сторож тишины на экране.

Смотрят все, у кого есть доступ к базе: тревога «ответов нет ни одного»
касается не админа, а того, кто каждый день ждёт этих ответов. Тревоги о почте
продаж — только с правом «Продажи» (решение Anthony 10.10.2026, П2б): сторож
считает этапы, которые видит учётка, и каждая тревога несёт свой этап.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.features.access.permissions import visible_stages
from backend.features.core.domain import Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.ops.alarms import Alarm
from backend.features.ops.silence import alarms, probe_providers

router = APIRouter(prefix="/watchdog", tags=["сторож тишины"])

_viewer = Depends(needs(Permission.VIEW))


class AlarmCard(BaseModel):
    """Одна тревога: что молчит и что это значит."""

    code: str
    title: str
    detail: str
    #: Этап, о почте которого тревога: экран не показывает тревогу продаж без права
    #: «Продажи», даже пришедшую раньше, чем право сняли. Общая тревога — без этапа.
    stage: Stage | None = None

    @classmethod
    def of(cls, alarm: Alarm) -> AlarmCard:
        return cls(code=alarm.code, title=alarm.title, detail=alarm.detail, stage=alarm.stage)


class WatchdogView(BaseModel):
    """Что сейчас молчит не по делу. Пусто — тихо и правильно."""

    alarms: list[AlarmCard]


@router.get("", response_model=WatchdogView, summary="Тишина, которая означает поломку")
async def silence(
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> WatchdogView:
    found = list(await alarms(session, stages=visible_stages(actor(user))))
    # Два бесплатных запроса к провайдерам: экран открывают редко,
    # а «API недоступен» — это то, ради чего его и открывают.
    unreachable = await probe_providers()
    if unreachable is not None:
        found.insert(0, unreachable)
    return WatchdogView(alarms=[AlarmCard.of(alarm) for alarm in found])
