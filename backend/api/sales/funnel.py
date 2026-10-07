"""Воронка продаж — под правом `sales`: лиды на каждом шаге, по гипотезам и периоду.

`GET /sales/funnel?hypothesis=N&since=…&until=…` — без записи. Числа считает
`features/sales/funnel.py` правилом своего шага — тем же, что у счётчиков и выгрузки: экран
своего счёта не ведёт. Строка на каждую гипотезу (и без писем — с нулями) и итог по ним;
с гипотезой — только её строка.

Период — полуинтервал `[since, until)`, моменты — с поясом (`2026-10-01T00:00:00+03:00`):
сутки считаются по часам человека, а не сервера. Без пояса или начало не раньше конца — 422.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import AwareDatetime, BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session, needs
from backend.features.core.domain import Permission
from backend.features.core.models.access import UserModel
from backend.features.sales import chain, funnel

#: Без префикса и меток: роутер входит в роутер раздела (`routes.py`) — как очередь.
router = APIRouter()

_seller = Depends(needs(Permission.SALES))

#: Начало периода не раньше конца — словами, что поправить.
EMPTY_PERIOD = "начало периода не раньше его конца — поменяйте даты местами"


class FunnelCounts(BaseModel):
    """Лидов на каждом шаге: имена — шаги воронки (`funnel.Step`)."""

    #: Письмо цепочки ждёт отправки, ни одно ещё не ушло.
    queued: int
    #: Первое письмо ушло в период — лидов, а не писем.
    sent: int
    #: Письмо дошло, и ни одно не вернулось.
    delivered: int
    #: Вернулось хоть одно письмо цепочки — отказ сильнее доставки.
    bounced: int
    #: Ответил человек или попросил не писать; автоответ — не ответ.
    answered: int
    #: Передача телемаркетологу заведена.
    handed_off: int

    @classmethod
    def of(cls, found: funnel.Funnel) -> FunnelCounts:
        return cls(**asdict(found))


class FunnelRow(BaseModel):
    """Воронка одной гипотезы."""

    hypothesis_id: int
    name: str
    counts: FunnelCounts


class SalesFunnelView(BaseModel):
    """Экран воронки: строки по гипотезам, старшие первыми, и итог по ним."""

    #: Гипотеза фильтра; пусто — все.
    hypothesis_id: int | None
    since: datetime | None
    until: datetime | None
    rows: list[FunnelRow]
    total: FunnelCounts


@router.get("/funnel", response_model=SalesFunnelView, summary="Воронка продаж по гипотезам")
async def read_funnel(
    hypothesis: int | None = Query(default=None, ge=1, description="номер гипотезы; пусто — все"),
    since: AwareDatetime | None = Query(default=None, description="начало периода, с поясом"),
    until: AwareDatetime | None = Query(default=None, description="конец периода, не включая"),
    _: UserModel = _seller,
    session: AsyncSession = Depends(db_session),
) -> SalesFunnelView:
    if since is not None and until is not None and since >= until:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, EMPTY_PERIOD)
    await chain.known(session, hypothesis)
    found = await funnel.board(session, funnel.Period(since, until), hypothesis_id=hypothesis)
    return SalesFunnelView(
        hypothesis_id=hypothesis,
        since=since,
        until=until,
        rows=[
            FunnelRow(
                hypothesis_id=row.hypothesis_id, name=row.name, counts=FunnelCounts.of(row.funnel)
            )
            for row in found.rows
        ],
        total=FunnelCounts.of(found.total),
    )
