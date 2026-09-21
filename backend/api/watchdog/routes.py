"""Сторож тишины на экране.

Смотрят все, у кого есть доступ к базе: тревога «ответов нет ни одного»
касается не админа, а того, кто каждый день ждёт этих ответов.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.ops.silence import Alarm, alarms

router = APIRouter(prefix="/watchdog", tags=["сторож тишины"])

_viewer = Depends(needs(Permission.VIEW))


class AlarmCard(BaseModel):
    """Одна тревога: что молчит и что это значит."""

    code: str
    title: str
    detail: str

    @classmethod
    def of(cls, alarm: Alarm) -> AlarmCard:
        return cls(code=alarm.code, title=alarm.title, detail=alarm.detail)


class WatchdogView(BaseModel):
    """Что сейчас молчит не по делу. Пусто — тихо и правильно."""

    alarms: list[AlarmCard]


@router.get("", response_model=WatchdogView, summary="Тишина, которая означает поломку")
async def silence(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> WatchdogView:
    return WatchdogView(alarms=[AlarmCard.of(alarm) for alarm in await alarms(session)])
