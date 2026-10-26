"""Рассылка продаж — под правом `sales`: режим и адреса частями для вкладки «Рассылка».

`GET /sales/mailing` — без записи. Режим — строками автоотправки и автосборки, теми же, что у
«Очереди писем» (`sales/auto_lines.py`). Адреса — по гипотезам (и без лидов — с нулями) и итогом:
ушло, в очереди, без письма — сборка напишет или нет и почему, не готовы, сняты и почему. Числа
считает `features/sales/mailing.py` — правилами воронки, очереди пачки и решения сборки, — экран
своего счёта не ведёт. Вкладка спрашивает раз в минуту: счёт — агрегатами и партией, без запроса
на лида.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.api.sales.mailing_schemas import MailingParts, MailingRow, SalesMailingView
from backend.api.sales.queue import AutoLineView
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import auto_lines, mailing
from backend.shared.queue import sales_bulk_queue

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`) — как воронка.
router = APIRouter()

_seller = Depends(needs(Permission.SALES))


@router.get("/mailing", response_model=SalesMailingView, summary="Рассылка продаж: адреса частями")
async def read_mailing(
    _: UserModel = _seller, session: AsyncSession = Depends(db_session)
) -> SalesMailingView:
    now = datetime.now(UTC)
    found = await mailing.board(session, now=now)
    return SalesMailingView(
        autosend=AutoLineView.of(await auto_lines.autosend_line(session, now)),
        autobuild=AutoLineView.of(
            await auto_lines.autobuild_line(session, sales_bulk_queue(), now)
        ),
        rows=[MailingRow.of(row) for row in found.rows],
        total=MailingParts.of(found.total),
    )
