"""Общий стоп-лист глазами продаж: какие его строки держат лидов — одно правило на всех.

Общие `suppressions` пишут приём ответов (отписка с этапом кампании, `replies/pipeline.py`),
страница отписки, экран «Стоп-лист» и чистка поставщиков. Продажи держит строка без этапа,
строка этапа продаж — и строка любого этапа, где человек сам просил не писать (отписался
или пожаловался): он отписывается от нас, а не от рассылки. Это правило судят очистка
лидов (`cleaning.py`), сборка и отправка (`mail.stopped_by`) и экран стоп-листа продаж —
до этого модуля у очистки и отправки было по своей копии условия.

На экране стоп-листа продаж эти строки только видны: снимают их на общем экране
«Стоп-лист» — там решение адресата снимается с причиной в журнале (`api/suppressions`).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import ColumnElement, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.domain import Stage, SuppressionReason
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel

#: Сам просил не писать — держит при любом этапе записи, не только без этапа.
ASKED_NOT_TO_WRITE = (SuppressionReason.UNSUBSCRIBED, SuppressionReason.COMPLAINED)


def holds_sales(moment: datetime) -> ColumnElement[bool]:
    """Строка общего стоп-листа держит продажи в минуту `moment`: без этапа и этапа продаж —
    любая, другого этапа — только та, где человек сам просил не писать; и срок не истёк."""
    anywhere = or_(
        SuppressionModel.stage.is_(None),
        SuppressionModel.stage == Stage.SALES,
        SuppressionModel.reason.in_(ASKED_NOT_TO_WRITE),
    )
    return and_(anywhere, SuppressionModel.in_force(moment))


@dataclass(frozen=True, slots=True)
class Held:
    """Строка общего стоп-листа, которая держит продажи, — как её показывает экран."""

    id: int
    #: Адрес или домен — кому не пишем.
    target: str
    reason: SuppressionReason
    stage: Stage | None
    created_at: datetime


async def holding(
    session: AsyncSession, *, limit: int, now: datetime | None = None
) -> tuple[list[Held], int]:
    """Строки общего стоп-листа, которые держат продажи, — новые первыми, не больше `limit`;
    и сколько их всего."""
    held = holds_sales(now or datetime.now(UTC))
    rows = await session.execute(
        select(SuppressionModel, DomainModel.host)
        .outerjoin(DomainModel, DomainModel.id == SuppressionModel.domain_id)
        .where(held)
        .order_by(SuppressionModel.created_at.desc(), SuppressionModel.id.desc())
        .limit(limit)
    )
    total = await session.scalar(select(func.count()).select_from(SuppressionModel).where(held))
    found = [
        Held(row.id, row.email or host or "—", row.reason, row.stage, row.created_at)
        for row, host in rows.tuples()
    ]
    return found, int(total or 0)
