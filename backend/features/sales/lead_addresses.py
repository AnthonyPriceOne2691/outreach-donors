"""Адрес лида каждой переписки продаж — ответ модуля спискам почты (`mail.addresses`).

Строки `contacts` у переписки продаж нет: адрес живёт у лида, а лид — у переписки по явной связи
`sales_threads` (решение 01.10). Отправка находит его по одной переписке (`mail.linked`), а спискам
«Диалогов» и писем он нужен у тысяч переписок разом — запросом на пачку номеров, а не на строку.
Адрес — лида, того, кому пишет цепочка (`mail.recipient`): кому уходит ответ в переписке — лиду
или его коллеге, — решает отправка, а не список.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales.models import SalesLeadModel, SalesThreadModel

#: Переписок в одном `IN`: параметров у Postgres не больше 32 767 на запрос, а список «Диалогов»
#: спрашивает обо всех переписках продаж разом.
CHUNK = 1000


async def by_thread(session: AsyncSession, thread_ids: Sequence[int]) -> dict[int, str]:
    """Номер переписки → адрес её лида, у каждой переписки из `thread_ids`, пачками по `CHUNK`.
    Переписки без лида — её начала не сборка продаж — в ответе нет."""
    found: dict[int, str] = {}
    for start in range(0, len(thread_ids), CHUNK):
        rows = await session.execute(
            select(SalesThreadModel.thread_id, SalesLeadModel.email)
            .join(SalesLeadModel, SalesLeadModel.id == SalesThreadModel.lead_id)
            .where(SalesThreadModel.thread_id.in_(thread_ids[start : start + CHUNK]))
        )
        found.update(rows.tuples().all())
    return found
