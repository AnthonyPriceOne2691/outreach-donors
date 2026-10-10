"""Домены и ящики рассылки: посмотреть, включить, выключить.

Раздел закрыт именованным действием `senders`, и оно есть только
у админа. Причина не в иерархии: включённый заново домен начинает разгон
с начала, и ошибка здесь стоит репутации домена — а она не
восстанавливается, в отличие от юнитов.

**Ящики продаж — только с правом «Продажи»** (решение Anthony 10.10.2026, П2б):
без права их нет в списке, в числах и в направлениях экрана, а включить или
выключить ящик продаж — 403 словами.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.senders.schemas import (
    DirectionLimit,
    DisableRequest,
    DomainLimit,
    SenderCard,
    SendersView,
)
from backend.api.stage_access import on_sender
from backend.config import outreach as cfg
from backend.features.access.permissions import visible_stages
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import UserModel
from backend.features.core.models.outreach import SenderModel, SendingDomainModel
from backend.features.outreach import limits
from backend.features.outreach import senders as rules
from backend.features.outreach.repository import OutreachRepository

router = APIRouter(prefix="/senders", tags=["рассылка"])

_admin = Depends(needs(Permission.SENDERS))
#: Ящик по номеру: у ящика продаж — ещё право «Продажи» (П2б).
_box_admin = Depends(on_sender(Permission.SENDERS))


@router.get("", response_model=SendersView, summary="Домены и ящики рассылки")
async def all_senders(
    user: UserModel = _admin,
    session: AsyncSession = Depends(db_session),
) -> SendersView:
    stages = visible_stages(actor(user))
    repository = OutreachRepository(session)
    found = await repository.senders(stages=stages)
    # Ящик, домен и направление считают первые письма — тем же счётом, что кап и фильтр.
    first = await repository.sent_today(first_only=True)
    rows = _domain_rows(await limits.sending_domains(session), stages, found)
    return SendersView(
        senders=[SenderCard.of(s, sent_today=first.get(s.id, 0)) for s in found],
        enabled_domains=len(await repository.enabled_domains(stages=stages)),
        domains=[
            DomainLimit.model_validate(row).model_copy(
                update={"sent_today": sum(first.get(s.id, 0) for s in found if s.domain == name)}
            )
            for name, row in sorted(rows.items())
        ],
        directions=[
            DirectionLimit(
                stage=stage,
                daily_limit=cfg.direction_limit(stage.value),
                sent_today=sum(first.get(s.id, 0) for s in found if s.stage is stage),
            )
            for stage in Stage
            if stage in stages
        ],
    )


@router.post("/{sender_id}/enable", response_model=SenderCard, summary="Включить, с начала разгона")
async def enable_sender(
    sender_id: int,
    author: UserModel = _box_admin,
    session: AsyncSession = Depends(db_session),
) -> SenderCard:
    repository = OutreachRepository(session)
    sender = await repository.sender(sender_id)
    rules.enable(sender)
    # Решение должно иметь автора: включение домена — это возобновление
    # отправки с его репутацией на кону.
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author.id,
        target=f"sender:{sender.id}",
        details={"действие": "включён", "домен": sender.domain, "разгон": "с начала"},
    )
    await session.commit()
    return await _card(repository, sender)


@router.post("/{sender_id}/disable", response_model=SenderCard, summary="Выключить")
async def disable_sender(
    sender_id: int,
    body: DisableRequest,
    author: UserModel = _box_admin,
    session: AsyncSession = Depends(db_session),
) -> SenderCard:
    repository = OutreachRepository(session)
    sender = await repository.sender(sender_id)
    rules.disable(sender, body.reason)
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        author_id=author.id,
        target=f"sender:{sender.id}",
        details={"действие": "выключен", "домен": sender.domain, "причина": body.reason},
    )
    await session.commit()
    return await _card(repository, sender)


def _domain_rows(
    rows: Mapping[str, SendingDomainModel], stages: Collection[Stage], shown: Sequence[SenderModel]
) -> dict[str, SendingDomainModel]:
    """Строки доменов видимых направлений — и доменов, где пишут показанные ящики (П2б):
    строка чужого направления закрывает такой домен (`limits.domain_shut`), и без неё экран
    звал бы закрытый домен открытым."""
    hosts = {box.domain for box in shown}
    return {name: row for name, row in rows.items() if row.stage in stages or name in hosts}


async def _card(repository: OutreachRepository, sender: SenderModel) -> SenderCard:
    """Карточка ящика после переключения — тем же счётом, что в списке: первые письма."""
    first = await repository.sent_today(first_only=True)
    return SenderCard.of(sender, sent_today=first.get(sender.id, 0))
