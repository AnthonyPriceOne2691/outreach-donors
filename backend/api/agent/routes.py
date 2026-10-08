"""Настройки агента переписки и решения по его черновикам.

Смотрят все, у кого есть доступ к базе: по этим настройкам агент пишет
собеседникам, и оператор должен видеть, почему черновик такой. Правит —
право «настройки», как пороги: это решение о том, что и за сколько мы
обещаем людям снаружи. Отправить или отклонить черновик — право send: это
решение о письме наружу.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.agent.schemas import (
    AgentSettingsBody,
    AgentSettingsVersion,
    AgentStageView,
    AgentView,
    DraftCard,
    DraftDetail,
    RejectDraftBody,
    SendDraftBody,
)
from backend.api.deps import actor, db_session, needs
from backend.api.letters.schemas import SendResult
from backend.features.access.permissions import require
from backend.features.access.repository import AccessRepository
from backend.features.agent import autopilot, drafts
from backend.features.agent.drafts import Decider
from backend.features.agent.settings import AUTOPILOT, AgentSettingsRepository, AutopilotOffError
from backend.features.agent.stages import AGENT_STAGES, agent_stage
from backend.features.core.domain import AuditAction, DraftStatus, Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.letters.sending import Sending
from backend.features.letters.transport_factory import Transports, in_use

router = APIRouter(prefix="/agent", tags=["агент переписки"])

_settler = Depends(needs(Permission.SETTINGS))
_viewer = Depends(needs(Permission.VIEW))
_sender = Depends(needs(Permission.SEND))

#: Черновиков в списке за раз: список — очередь «ждут человека», а не архив.
DRAFTS_PAGE = 200


@router.get("/settings", response_model=AgentView, summary="Настройки агента по этапам")
async def agent_settings(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> AgentView:
    repository = AgentSettingsRepository(session)
    stages = []
    for stage, parts in AGENT_STAGES.items():
        current = await repository.current(stage)
        refused = autopilot.refusal(stage)
        stages.append(
            AgentStageView(
                stage=stage,
                title=parts.title or stage.value,
                lead=parts.lead,
                price_side=parts.price,
                current=None if current is None else AgentSettingsVersion.of(current),
                defaults=AgentSettingsBody.of(parts.defaults),
                autopilot_allowed=refused is None,
                autopilot_refusal=refused,
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
    parts = agent_stage(stage)  # этапа без агента нет — 404 словами
    repository = AgentSettingsRepository(session)
    previous = await repository.current(stage)
    settings = body.to_settings(previous, parts.defaults)
    if settings.mode == AUTOPILOT:
        # Письмо наружу без человека: включают там, где разрешают код этапа и сервер;
        # версию в автопилоте (и правку без режима поверх него) сохраняет тот, у кого send.
        if previous is None or previous.mode != AUTOPILOT:
            why = autopilot.refusal(stage)
            if why is not None:
                raise AutopilotOffError(why)
        require(actor(author), Permission.SEND)
    row = await repository.save(stage, settings, author=author.email)
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
            "режим": settings.mode,
            "ответов автопилота в переписке": settings.max_turns,
        },
    )
    await session.commit()
    return AgentSettingsVersion.of(row)


@router.get("/drafts", response_model=list[DraftCard], summary="Черновики агента в статусе")
async def drafts_in(
    status: DraftStatus = DraftStatus.ESCALATED,
    limit: int = Query(default=50, ge=1, le=DRAFTS_PAGE),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> list[DraftCard]:
    """По умолчанию — отданные человеку: их никто, кроме человека, не решит."""
    return [DraftCard.of(shown) for shown in await drafts.waiting(session, status, limit=limit)]


@router.get("/drafts/{draft_id}", response_model=DraftDetail, summary="Черновик целиком")
async def draft_detail(
    draft_id: int,
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> DraftDetail:
    return DraftDetail.of(await drafts.one(session, draft_id))


@router.post(
    "/drafts/{draft_id}/send", response_model=SendResult, summary="Отправить ответ по черновику"
)
async def send_draft(
    draft_id: int,
    body: SendDraftBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> SendResult:
    """Без текста — как есть (у отданного человеку — 409), с текстом — с правкой."""
    async with in_use(Transports()) as transports:
        outcome = await drafts.send_draft(
            session,
            Sending(session, transports),
            draft_id,
            body=body.body,
            by=Decider.of(author),
        )
    await session.commit()
    return SendResult(id=outcome.message_id, sender_email=outcome.sender_email, real=outcome.real)


@router.post("/drafts/{draft_id}/reject", response_model=DraftDetail, summary="Отклонить черновик")
async def reject_draft(
    draft_id: int,
    body: RejectDraftBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> DraftDetail:
    """Причина обязательна, у этапа со строгим списком — из него: иначе 422 словами."""
    await drafts.reject_draft(session, draft_id, reason=body.reason, by=Decider.of(author))
    await session.commit()
    return DraftDetail.of(await drafts.one(session, draft_id))
