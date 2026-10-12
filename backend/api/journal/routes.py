"""Журнал действий: кто, что и когда сделал в сервисе.

Читает тот, кто заводит учётки (`users`), — как и весь раздел «Учётки»: в журнале
входы и отказы входа с почтами, выданные права и сброшенные пароли. Проверка — по
действию, а не по роли: появится третья роль, и матрица прав ответит за неё сама.

Без права «Продажи» — без действий этапа продаж (П2, `access/journal.sales_entry`).
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import actor, db_session, needs
from backend.api.journal.schemas import JournalView
from backend.features.access.journal import (
    MAX_PAGE_SIZE,
    PAGE_SIZE,
    JournalQuery,
    read_journal,
)
from backend.features.access.permissions import sees_stage
from backend.features.core.domain import AuditAction, Permission, Stage
from backend.features.core.models.access import UserModel

router = APIRouter(prefix="/journal", tags=["журнал действий"])

_admin = Depends(needs(Permission.USERS))


@router.get("", response_model=JournalView, summary="Журнал действий, по странице")
async def journal(
    user: UserModel = _admin,
    session: AsyncSession = Depends(db_session),
    page: int = Query(default=1, ge=1, description="страница журнала, с единицы"),
    limit: int = Query(default=PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE, description="записей"),
    action: AuditAction | None = Query(default=None, description="только это действие"),
    author: int | None = Query(default=None, ge=1, description="только этот автор — номер учётки"),
    since: datetime | None = Query(default=None, description="с этого момента, включительно"),
    until: datetime | None = Query(default=None, description="до этого момента, не включая"),
) -> JournalView:
    """Страница журнала, новые сверху, с фильтрами по действию, автору и времени.

    Границы времени — моментами с поясом, а не датами: «день» у человека — по его часам,
    и сутки по UTC сдвинули бы вечер в завтра. Начало дня в его поясе считает экран.
    """
    with_sales = sees_stage(actor(user), Stage.SALES)
    found = await read_journal(
        session,
        JournalQuery(
            page=page,
            size=limit,
            action=action,
            author_id=author,
            since=since,
            until=until,
            with_sales=with_sales,
        ),
    )
    return JournalView.of(found, page=page, limit=limit, sales_hidden=not with_sales)
