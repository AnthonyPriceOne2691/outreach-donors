"""Настройки агента переписки.

Смотрят все, у кого есть доступ к базе: по этим настройкам агент пишет
собеседникам, и оператор должен видеть, почему черновик такой. Правит —
право «настройки», как пороги: это решение о том, что и за сколько мы
обещаем людям снаружи.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.agent.schemas import (
    AgentSettingsBody,
    AgentSettingsVersion,
    AgentStageView,
    AgentView,
)
from backend.api.deps import db_session, needs
from backend.features.access.repository import AccessRepository
from backend.features.agent.settings import AgentSettingsRepository
from backend.features.agent.stages import AGENT_STAGES, agent_stage
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import UserModel

router = APIRouter(prefix="/agent", tags=["агент переписки"])

_settler = Depends(needs(Permission.SETTINGS))
_viewer = Depends(needs(Permission.VIEW))


@router.get("/settings", response_model=AgentView, summary="Настройки агента по этапам")
async def agent_settings(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> AgentView:
    repository = AgentSettingsRepository(session)
    stages = []
    for stage, parts in AGENT_STAGES.items():
        current = await repository.current(stage)
        stages.append(
            AgentStageView(
                stage=stage,
                current=None if current is None else AgentSettingsVersion.of(current),
                defaults=AgentSettingsBody.of(parts.defaults),
                history=[AgentSettingsVersion.of(row) for row in await repository.history(stage)],
            )
        )
    return AgentView(stages=stages)


@router.post(
    "/settings/{stage}",
    response_model=AgentSettingsVersion,
    summary="Новая версия настроек агента этапа",
)
async def save_agent_settings(
    stage: Stage,
    body: AgentSettingsBody,
    author: UserModel = _settler,
    session: AsyncSession = Depends(db_session),
) -> AgentSettingsVersion:
    agent_stage(stage)  # этапа без агента нет — 404 словами
    row = await AgentSettingsRepository(session).save(
        stage, body.to_settings(), author=author.email
    )
    await AccessRepository(session).record(
        AuditAction.AGENT_SETTINGS_CHANGED,
        author_id=author.id,
        target=f"agent_settings:{row.id}",
        details={
            "этап": stage.value,
            "версия": row.version,
            "включён": body.enabled,
            "предел цены": None if body.price_limit_usd is None else str(body.price_limit_usd),
            "доводов": len(body.points),
            "тем человеку": len(body.stop_topics),
        },
    )
    await session.commit()
    return AgentSettingsVersion.of(row)
