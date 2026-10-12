"""Стоп-лист поставщиков («стоп-лист доноров» требования): одно правило, каким бы путём
запись ни пришла.

Требование: «все, у кого агентство размещалось за 12 мес., и текущие партнёры —
исключаются автоматически». Их рекламодатели — чужие клиенты и свои же
размещения: обходить такого донора незачем, переводить его рекламодателей —
писать тем, с кем агентство уже работает.

Поставщик — запись экрана «Стоп-лист» с причиной «поставщик» (`suppressions`,
`letters/stoplist.py`); консоль вносит список туда же (`supplier_import.py`, решение
12.10.2026). Прежний список консоли — таблица `supplier_donors` — читается как раньше.
До сверки ТЗ 11.10.2026 обход и перевод читали только его, и тот без срока: истёкшая
запись держала донора навсегда, а поставщик с экрана Этапу 2 не мешал вовсе.

**Правило одно** (`supplier_hosts`): поставщик — домен с действующей записью
любого из двух мест. Из стоп-листа — запись по домену с причиной «поставщик» без
этапа или этапа рекламодателей: этап у записи стоп-листа говорит, где она держит,
так же читает его отбор прогона Этапа 1 (`runs/exclusions.py`).

**Срок — у размещавшихся, бессрочно — у партнёров** (`term_end`): список размещений
держит `ADVERTISERS_SUPPLIER_TERM_MONTHS` (12 месяцев), партнёров — флаг `--forever`.
"""

from __future__ import annotations

import calendar
from datetime import UTC, datetime

from sqlalchemy import or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import advertisers as cfg
from backend.features.core.domain import Stage, SuppressionReason
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.ops import SuppressionModel


async def supplier_hosts(session: AsyncSession, *, now: datetime | None = None) -> set[str]:
    """Поставщики, чьи записи действуют сейчас, — из обоих путей, в нижнем регистре."""
    moment = now or datetime.now(UTC)
    listed = select(SupplierDonorModel.host).where(SupplierDonorModel.in_force(moment))
    marked = (
        select(DomainModel.host)
        .join(SuppressionModel, SuppressionModel.domain_id == DomainModel.id)
        .where(
            SuppressionModel.reason == SuppressionReason.SUPPLIER,
            or_(SuppressionModel.stage.is_(None), SuppressionModel.stage == Stage.ADVERTISERS),
            SuppressionModel.in_force(moment),
        )
    )
    return {str(host).lower() for host in (await session.scalars(union(listed, marked))).all()}


def term_end(moment: datetime, *, months: int | None = None) -> datetime:
    """Докуда держит запись, внесённая в `moment`: начало дня через столько календарных
    месяцев, сколько в настройке, как «12 месяцев» у экрана. 31-е без пары — последний
    день месяца. Срок — днём, а не секундой: повторный импорт того же дня его не двигает."""
    day = moment.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    total = day.month - 1 + (cfg.SUPPLIER_TERM_MONTHS if months is None else months)
    year, month = day.year + total // 12, total % 12 + 1
    return day.replace(
        year=year, month=month, day=min(day.day, calendar.monthrange(year, month)[1])
    )
