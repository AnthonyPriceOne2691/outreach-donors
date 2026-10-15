"""Лиды под стоп-листом продаж: пополнили список — снять лидов, сняли запись — вернуть их.

**Правило — очистки, а не второе.** Стоп-лист продаж закрывает адрес, домен компании и домен
адреса — тем же ключом, каким записан (`host_key`); судит это правило очистки
(`cleaning._stoplisted` над тем, что база знает о партии, — `clean_known.load_known`). Очистка
берёт лидов `new`; после пополнения списка с экрана или консоли тем же правилом снимаются и
`new`, и `ready`: лид, которому уже собрано письмо или идёт цепочка, — тоже клиент или
партнёр. Первое письмо снятого лида, ждущее в очереди, снимается с очереди тут же, со словами
стоп-листа (`off_queue.of_leads`): до этого оно висело с отказом «стоп-лист» на каждом проходе
автоотправки. Добивки встают: отправка отказывает лиду, которого сняли (`mail.unwritable`).

**Снятая запись возвращает своих лидов** — отклонённых с причиной «стоп-лист продаж», которых
не закрывает больше ни одна запись: в `new`, без причины, и следующая очистка проверит их
целиком — дубли, отписки, почту домена, адрес. Иначе ошибочная запись снимала бы лидов
насовсем: новая загрузка их не вернёт — очистка отсечёт её как дубль. Снятое с очереди письмо
вернувшегося лида сборка пишет заново на том же месте, когда очистка его пропустит
(`off_queue.again`).

**Снятый держатель отпускает компанию** (решение владельца 15.10, «компания — в одной
гипотезе»): у компании не осталось живых лидов — ранний лид другой гипотезы, отклонённый из-за
её держателя, возвращается и ждёт очистки (`company_hold.freed`).

Лиды читаются партиями по номеру: база — тысячи лидов, а список пополняют редко.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import ColumnElement, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.sales import company_hold
from backend.features.sales.clean_known import load_known

# Правило очистки — им же, а не копией: вторая копия разошлась бы на первой правке.
from backend.features.sales.cleaning import _stoplisted
from backend.features.sales.models import LeadStatus, RejectionReason, SalesLeadModel
from backend.features.sales.off_queue import of_leads

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
    причиной и словами очистки, их первые письма из очереди — с очереди. Сколько лидов сняли.
    Коммит — за вызывающим.

    Компания, у которой так не осталось живых лидов, свободна: ранний отклонённый из-за её
    держателя возвращается и ждёт очистки (`company_hold.freed`). Закрывает и его этот же
    стоп-лист (запись — домен компании) — снят и он, а вернётся следующий: «вернулся, компания
    свободна» у компании из стоп-листа было бы неправдой."""
    moment, closed = now or datetime.now(UTC), 0
    async for leads in _batches(session, SalesLeadModel.status.in_(OPEN)):
        found = await _closed(session, leads, moment)
        closed += len(found)
        back = await company_hold.freed(session, _domains(leads, found))
        while back:
            back = await company_hold.freed(
                session, _domains(back, await _closed(session, back, moment))
            )
    logger.info("продажи: стоп-лист снял лидов", extra={"closed": closed})
    return closed


async def _closed(
    session: AsyncSession, leads: list[SalesLeadModel], moment: datetime
) -> dict[int, str]:
    """Лиды, которых закрывает стоп-лист, — в отклонённые, их письма — с очереди: номер → слова."""
    known = await load_known(session, leads, moment)
    found = {lead.id: note for lead in leads if (note := _stoplisted(lead, known))}
    await _apply(
        session,
        [
            {
                "id": lead_id,
                "status": LeadStatus.REJECTED,
                "rejection_reason": RejectionReason.STOPLIST.value,
                "cleaning_note": note,
            }
            for lead_id, note in found.items()
        ],
    )
    await of_leads(session, found)
    return found


def _domains(leads: list[SalesLeadModel], closed: Mapping[int, str]) -> set[int]:
    return {lead.domain_id for lead in leads if lead.id in closed}


async def release(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Вернуть в `new` лидов, отклонённых стоп-листом продаж, которых он больше не закрывает:
    их проверит следующая очистка. Сколько вернули. Коммит — за вызывающим."""
    moment, released = now or datetime.now(UTC), 0
    stopped = (SalesLeadModel.status == LeadStatus.REJECTED) & (
        SalesLeadModel.rejection_reason == RejectionReason.STOPLIST.value
    )
    async for leads in _batches(session, stopped):
        known = await load_known(session, leads, moment)
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
