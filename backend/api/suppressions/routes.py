"""Стоп-лист на экране: кто в нём, кто его завёл и как оттуда выйти.

**Смотреть — под правом `view`, менять — под `send`.** Стоп-лист
отвечает на вопрос «кому мы пишем», а это ровно то право, которого
у оператора нет. Видеть список при этом должен каждый, кто видит базу:
иначе «почему донору не ушло письмо» остаётся без ответа.

**Каждая правка — в журнал.** Запись решает, придёт ли письмо, а снятие
отписки разрешает написать тому, кто просил не писать: у обоих действий
должен быть автор и время.

**Запись этапа продаж — только с правом «Продажи»** (решение Anthony 10.10.2026,
П2б): без права её нет ни в списке, ни в числах над ним, а снять или завести её —
403 словами. Запись без этапа держит все этапы: её видят и правят, как раньше.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.stage_access import SUPPRESSIONS, on_suppression
from backend.api.suppressions.schemas import (
    AddBody,
    AddedEntry,
    RemoveBody,
    StopEntry,
    StopListView,
)
from backend.features.access.permissions import require_stage, visible_stages
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.letters import stoplist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/suppressions", tags=["стоп-лист"])

_viewer = Depends(needs(Permission.VIEW))
_sender = Depends(needs(Permission.SEND))
#: Запись по номеру: у записи этапа продаж — ещё право «Продажи» (П2б).
_row_sender = Depends(on_suppression(Permission.SEND))


@router.get("", response_model=StopListView, summary="Стоп-лист целиком")
async def all_rows(
    user: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> StopListView:
    # Записи и числа шапки — видимых этапов: без права «Продажи» — без записей продаж.
    rows = await stoplist.rows(session, stages=visible_stages(actor(user)))
    moment = datetime.now(UTC)
    return StopListView(
        rows=[StopEntry.of(row, now=moment) for row in rows],
        total=len(rows),
        donor_decisions=sum(1 for row in rows if row.donor_decision),
        expired=sum(1 for row in rows if row.expired(moment)),
    )


@router.post("", response_model=AddedEntry, summary="Завести запись руками")
async def add_row(
    body: AddBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> AddedEntry:
    """Домен целиком или один адрес. Письма адресату снимаются с очереди.

    Домен приходит любым написанием — ссылкой, с `www.`, поддоменом — и ложится
    на корень сайта, как его пишет база; не было его там — ответ так и скажет.
    Запись этапа продаж — с правом «Продажи» (П2б).
    """
    require_stage(actor(author), body.stage, SUPPRESSIONS)
    row = await stoplist.add(
        session,
        body.target,
        reason=body.reason,
        stage=body.stage,
        expires_at=body.expires_at,
        author=author.email,
    )
    await AccessRepository(session).record(
        AuditAction.SUPPRESSION_ADDED,
        author_id=author.id,
        target=f"suppression:{row.id}",
        details={
            "кому не пишем": row.target,
            "причина": row.reason.value,
            "до": row.expires_at.isoformat() if row.expires_at else "навсегда",
        },
    )
    await session.commit()
    logger.info("стоп-лист: %s добавил %s (%s)", author.email, row.target, row.reason.value)
    return AddedEntry.added(row)


@router.post("/{row_id}/remove", response_model=StopEntry, summary="Снять запись")
async def remove_row(
    row_id: int,
    body: RemoveBody,
    author: UserModel = _row_sender,
    session: AsyncSession = Depends(db_session),
) -> StopEntry:
    """Снятое решение адресата уходит в журнал вместе с причиной.

    Причина не хранится в самой записи: записи после снятия нет вовсе.
    Журнал — единственное место, где это видно через полгода.
    """
    row = await stoplist.remove(session, row_id, reason=body.reason)
    await AccessRepository(session).record(
        AuditAction.SUPPRESSION_REMOVED,
        author_id=author.id,
        target=f"suppression:{row.id}",
        details={
            "кому снова пишем": row.target,
            "была причина": row.reason.value,
            "почему сняли": (body.reason or "").strip() or None,
            "решение адресата": row.donor_decision,
        },
    )
    await session.commit()
    logger.info(
        "стоп-лист: %s снял %s (была причина «%s»)", author.email, row.target, row.reason.value
    )
    return StopEntry.of(row)
