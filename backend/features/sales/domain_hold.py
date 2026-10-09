"""Домен, который держит лид продаж: одно правило для чисток аутрича.

Лид ссылается на домен без каскада (`sales_leads.domain_id` `RESTRICT`): строка домена
общая, и база не даст удалить её, пока на ней лид. Обе донорские чистки — основная
(`runs/prune.py`) и липовых доноров (`donors/probe.py`, `prune --probes`) — держат одно
правило: такой домен не удаляют и не падают на нём, а называют словами. Решать за продажи
они не вправе: пробу продаж целиком убирает только `prune --test-traces` (`sales/trials.py`).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence

from sqlalchemy import ColumnElement, Exists, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core.models.domain import DomainModel
from backend.features.sales.models import SalesLeadModel


def holds_domain() -> Exists:
    """Лид продаж на домене — условием запроса по `DomainModel`."""
    return exists().where(SalesLeadModel.domain_id == DomainModel.id)


def kept_words(lead_ids: Sequence[int]) -> str:
    """Почему домен оставлен — словами плана чистки."""
    numbers = ", ".join(f"№{number}" for number in lead_ids)
    if len(lead_ids) == 1:
        return f"домен держит лид продаж {numbers} — его убирает `prune --test-traces`"
    return f"домен держат лиды продаж {numbers} — их убирает `prune --test-traces`"


async def held(session: AsyncSession, domains: ColumnElement[bool]) -> dict[str, str]:
    """Домены под условием, которые держит лид продаж: домен → почему оставлен. Ничего не меняет."""
    rows = await session.execute(
        select(DomainModel.host, SalesLeadModel.id)
        .join(SalesLeadModel, SalesLeadModel.domain_id == DomainModel.id)
        .where(domains)
        .order_by(DomainModel.host, SalesLeadModel.id)
    )
    leads: defaultdict[str, list[int]] = defaultdict(list)
    for host, lead_id in rows.tuples():
        leads[host].append(lead_id)
    return {host: kept_words(numbers) for host, numbers in leads.items()}
