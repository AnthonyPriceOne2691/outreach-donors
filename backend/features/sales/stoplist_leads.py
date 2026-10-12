"""Лиды под стоп-листом продаж: пополнили список — снять лидов, сняли запись — вернуть их.

**Правило — очистки, а не второе.** Стоп-лист продаж закрывает адрес, домен компании и домен
адреса — тем же ключом, каким записан (`host_key`); судит это правило очистки
(`cleaning._stoplisted` над тем, что база знает о партии, — `cleaning._load_known`). Очистка
берёт лидов `new`; после пополнения списка с экрана или консоли тем же правилом снимаются и
`new`, и `ready`: лид, которому уже собрано письмо или идёт цепочка, — тоже клиент или
партнёр. Письмо снятого лида в очереди не уходит, добивки встают: отправка отказывает лиду,
которого сняли (`mail.unwritable`).

**Снятая запись возвращает своих лидов** — отклонённых с причиной «стоп-лист продаж», которых
не закрывает больше ни одна запись: в `new`, без причины, и следующая очистка проверит их
целиком — дубли, отписки, почту домена, адрес. Иначе ошибочная запись снимала бы лидов
насовсем: новая загрузка их не вернёт — очистка отсечёт её как дубль.

Лиды читаются партиями по номеру: база — тысячи лидов, а список пополняют редко.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.ext.asyncio import AsyncSession

# Правило очистки — им же, а не копией: вторая копия разошлась бы на первой правке.
from backend.features.sales.cleaning import _load_known, _stoplisted
from backend.features.sales.models import LeadStatus, RejectionReason, SalesLeadModel

logger = logging.getLogger(__name__)

#: Лидов за чтение: одна партия — одно чтение того, что о ней знает база.
BATCH = 1000
#: Кого снимает пополнение: ещё не отсеянных.
OPEN = (LeadStatus.NEW, LeadStatus.READY)


async def _batches(
    session: AsyncSession, condition: ColumnElement[bool]
) -> AsyncIterator[list[SalesLeadModel]]:
    after = 0
    while True:
        rows = await session.scalars(
            select(SalesLeadModel)
            .where(condition, SalesLeadModel.id > after)
            .order_by(SalesLeadModel.id)
            .limit(BATCH)
        )
        leads = list(rows)
        if not leads:
            return
        yield leads
        after = leads[-1].id


async def _apply(session: AsyncSession, rows: list[dict[str, Any]]) -> int:
    if rows:
        await session.execute(update(SalesLeadModel), rows)
    return len(rows)


async def close(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Снять лидов `new` и `ready`, которых закрывает стоп-лист продаж, — в отклонённые с
    причиной и словами очистки. Сколько сняли. Коммит — за вызывающим."""
    moment, closed = now or datetime.now(UTC), 0
    async for leads in _batches(session, SalesLeadModel.status.in_(OPEN)):
        known = await _load_known(session, leads, moment)
        found = [(lead, note) for lead in leads if (note := _stoplisted(lead, known))]
        closed += await _apply(
            session,
            [
                {
                    "id": lead.id,
                    "status": LeadStatus.REJECTED,
                    "rejection_reason": RejectionReason.STOPLIST.value,
                    "cleaning_note": note,
                }
                for lead, note in found
            ],
        )
    logger.info("продажи: стоп-лист снял лидов", extra={"closed": closed})
    return closed


async def release(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Вернуть в `new` лидов, отклонённых стоп-листом продаж, которых он больше не закрывает:
    их проверит следующая очистка. Сколько вернули. Коммит — за вызывающим."""
    moment, released = now or datetime.now(UTC), 0
    stopped = (SalesLeadModel.status == LeadStatus.REJECTED) & (
        SalesLeadModel.rejection_reason == RejectionReason.STOPLIST.value
    )
    async for leads in _batches(session, stopped):
        known = await _load_known(session, leads, moment)
        free = [lead for lead in leads if _stoplisted(lead, known) is None]
        released += await _apply(
            session,
            [
                {
                    "id": lead.id,
                    "status": LeadStatus.NEW,
                    "rejection_reason": None,
                    "cleaning_note": None,
                }
                for lead in free
            ],
        )
    logger.info("продажи: лиды вернулись из стоп-листа", extra={"released": released})
    return released
