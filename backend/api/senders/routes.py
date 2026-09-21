"""Домены и ящики рассылки: посмотреть, включить, выключить.

Раздел закрыт именованным действием `senders`, и оно есть только
у админа. Причина не в иерархии: включённый заново домен начинает разгон
с начала, и ошибка здесь стоит репутации домена — а она не
восстанавливается, в отличие от юнитов.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.senders.schemas import DisableRequest, SenderCard, SendersView
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.outreach import senders as rules
from backend.features.outreach.repository import OutreachRepository

router = APIRouter(prefix="/senders", tags=["рассылка"])

_admin = Depends(needs(Permission.SENDERS))


@router.get("", response_model=SendersView, summary="Домены и ящики рассылки")
async def all_senders(
    _: UserModel = _admin,
    session: AsyncSession = Depends(db_session),
) -> SendersView:
    repository = OutreachRepository(session)
    found = await repository.senders()
    today = await repository.sent_today()
    return SendersView(
        senders=[SenderCard.of(s, sent_today=today.get(s.id, 0)) for s in found],
        enabled_domains=len(await repository.enabled_domains()),
    )


@router.post("/{sender_id}/enable", response_model=SenderCard, summary="Включить, с начала разгона")
async def enable_sender(
    sender_id: int,
    author: UserModel = _admin,
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
    return SenderCard.of(sender, sent_today=(await repository.sent_today()).get(sender.id, 0))


@router.post("/{sender_id}/disable", response_model=SenderCard, summary="Выключить")
async def disable_sender(
    sender_id: int,
    body: DisableRequest,
    author: UserModel = _admin,
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
    return SenderCard.of(sender, sent_today=(await repository.sent_today()).get(sender.id, 0))
