"""Стоп-лист на экране: кто в нём, кто его завёл и как оттуда выйти.

**Смотреть — под правом `view`, менять — под `send`.** Стоп-лист
отвечает на вопрос «кому мы пишем», а это ровно то право, которого
у оператора нет. Видеть список при этом должен каждый, кто видит базу:
иначе «почему донору не ушло письмо» остаётся без ответа.

**Каждая правка — в журнал.** Запись решает, придёт ли письмо, а снятие
отписки разрешает написать тому, кто просил не писать: у обоих действий
должен быть автор и время.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.suppressions.schemas import (
    AddBody,
    RemoveBody,
    StopEntry,
    StopListView,
)
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.letters import stoplist

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/suppressions", tags=["стоп-лист"])

_viewer = Depends(needs(Permission.VIEW))
_sender = Depends(needs(Permission.SEND))


@router.get("", response_model=StopListView, summary="Стоп-лист целиком")
async def all_rows(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> StopListView:
    rows = await stoplist.rows(session)
    return StopListView(
        rows=[StopEntry.of(row) for row in rows],
        total=len(rows),
        donor_decisions=sum(1 for row in rows if row.donor_decision),
    )


@router.post("", response_model=StopEntry, summary="Завести запись руками")
async def add_row(
    body: AddBody,
    author: UserModel = _sender,
    session: AsyncSession = Depends(db_session),
) -> StopEntry:
    """Домен целиком или один адрес. Письма адресату снимаются с очереди."""
    row = await stoplist.add(
        session, body.target, reason=body.reason, stage=body.stage, author=author.email
    )
    await AccessRepository(session).record(
        AuditAction.SUPPRESSION_ADDED,
        author_id=author.id,
        target=f"suppression:{row.id}",
        details={"кому не пишем": row.target, "причина": row.reason.value},
    )
    await session.commit()
    logger.info("стоп-лист: %s добавил %s (%s)", author.email, row.target, row.reason.value)
    return StopEntry.of(row)


@router.post("/{row_id}/remove", response_model=StopEntry, summary="Снять запись")
async def remove_row(
    row_id: int,
    body: RemoveBody,
    author: UserModel = _sender,
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
