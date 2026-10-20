"""Платный поиск адреса повтора — в пределах сметы, кусками, с резервом (`segment_run.again`).

Повтор идёт сам, без человека, и платить сверх того, что человек подтвердил запуском, он не должен
(`segment_queue.paid_left`: смета минус принятые провайдером запросы, незаписанные и резерв).

- **Кусок — не больше остатка, сайтов к поиску и одной пачки поиска** (`contacts.search.BATCH`):
  сайт — не больше одного платного запроса, и кусок не потратит больше остатка, а молчащий сайт,
  который не заплатил, остаток не съедает — его получит следующий кусок.
- **Перед куском — платная ступень готова** (`ready`: ключ задан, остаток узнаётся и не выбран —
  то же правило, что у поиска). Не готова — платная часть кончилась, и «не спросили» (квота,
  частота, сбой) не трогаются: без неё повтор отправил бы их в «адреса нет» на полгода.
- **Резерв** (ревью 20.10, второй и третий круг): доля остатка под кусок пишется в отчёт своим
  коммитом с моментом по часам базы и снимается после куска. Проход упал посреди куска — резерв
  остаётся, и следующий проход прогона сводит его (`settle`; номер задачи прогона держит он, так
  что прежний мёртв): верхняя граница оплаченного — строки журнала расхода поиска этапа продаж с
  момента резерва и сайты куска, чей исход не сохранился (каждый — не больше одного запроса); её —
  в «принято, но не записано», остальное — снять.
- **Провайдер отказал или кусков набралось на проход** — остальных сайтов этот проход не трогает
  (`walk` → `held`): бесплатные ступени отправили бы ответивших без адреса в «адреса нет» при живом
  остатке, а следующий проход продолжит.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts import search as contacts_search
from backend.features.core.domain import Stage
from backend.features.core.models.ops import UsageRecordModel
from backend.features.sales.segment_counts import contacts_report, number
from backend.features.sales.segment_models import SalesSegmentCandidateModel as Candidate
from backend.features.sales.segment_models import SalesSegmentRunModel as Run
from backend.features.sales.segment_queue import Retry, SegmentContactQueue, paid_left
from backend.features.sales.segment_spend import PENDING_KEY, UNRECORDED_KEY, pending

logger = logging.getLogger(__name__)

#: Момент резерва по часам базы — с него сводит резерв `settle`.
PENDING_AT = "paid_pending_at"
#: Кусков платного поиска за проход — не больше: остальное — следующему проходу.
CHUNKS = 20


async def ready() -> bool:
    """Платная ступень у прохода есть — тем же правилом, что у поиска (`contacts.search`): ключ
    задан, остаток узнаётся и не выбран."""
    async with httpx.AsyncClient() as http:
        return await contacts_search.paid_step(http, contacts_search.SearchReport()) is not None


def _contacts(run: Run) -> dict[str, Any]:
    return dict(run.report.get("contacts") or {})


async def settle(session: AsyncSession, run: Run) -> None:
    """Свести резерв, оставленный упавшим проходом (шапка модуля), — в начале каждого прохода
    прогона (`segment_run.execute`, `again`); резерва нет — ничего."""
    reserved = pending(run.report)
    contacts = _contacts(run)
    raw = contacts.get(PENDING_AT)
    if not reserved:
        return
    since = datetime.fromisoformat(raw) if isinstance(raw, str) else run.created_at
    journal = await session.scalar(
        select(func.coalesce(func.sum(UsageRecordModel.units), 0)).where(
            UsageRecordModel.operation == contacts_search.CONTACTS_OPERATION,
            UsageRecordModel.stage == Stage.SALES,
            UsageRecordModel.created_at >= since,
        )
    )
    saved = await session.scalar(
        select(func.count(Candidate.id)).where(
            Candidate.run_id == run.id, Candidate.contact_attempted_at >= since
        )
    )
    unrecorded = min(reserved, int(journal or 0) + max(0, reserved - int(saved or 0)))
    contacts[UNRECORDED_KEY] = number(contacts.get(UNRECORDED_KEY)) + unrecorded
    contacts[PENDING_KEY] = 0
    contacts.pop(PENDING_AT, None)
    run.report = {**run.report, "contacts": contacts}
    await session.commit()
    logger.warning(
        "сбор продаж: резерв платного поиска после сбоя сведён",
        extra={"run": run.id, "reserved": reserved, "unrecorded": unrecorded},
    )


async def _reserve(session: AsyncSession, run: Run, size: int) -> None:
    contacts = _contacts(run)
    at = await session.scalar(select(func.now()))
    contacts[PENDING_KEY], contacts[PENDING_AT] = size, at.isoformat() if at else None
    run.report = {**run.report, "contacts": contacts}
    await session.commit()


async def merged(session: AsyncSession, run: Run, found: dict[str, Any]) -> int:
    """Итог куска поиска — в отчёт прогона своим коммитом, резерв куска снят. Возвращает, скольких
    сайтов он прошёл."""
    contacts = contacts_report(run.report.get("contacts"), found)
    contacts[PENDING_KEY] = 0
    contacts.pop(PENDING_AT, None)
    run.report = {**run.report, "contacts": contacts}
    await session.commit()
    return number(found.get("walked"))


def _refused(found: dict[str, Any]) -> bool:
    counters = found.get("counters")
    return isinstance(counters, dict) and number(counters.get("provider_refused")) > 0


async def walk(session: AsyncSession, run: Run, retry: Retry) -> tuple[int, bool]:
    """Платная часть повтора (шапка модуля). Возвращает, скольких сайтов прошёл поиск, и `held` —
    остальных сайтов этот проход не трогает: провайдер отказал или кусков набралось на проход."""
    walked = 0
    for _ in range(CHUNKS):
        queue = SegmentContactQueue(session, run.id, retry=retry, paid=True)
        left = paid_left(run.report, run.estimate)
        size = min(left, contacts_search.BATCH, await queue.pending_count()) if left > 0 else 0
        if size <= 0 or not await ready():
            return walked, False
        await _reserve(session, run, size)
        found = (await contacts_search.search_contacts(session, limit=size, queue=queue)).as_dict()
        walked += await merged(session, run, found)
        if _refused(found):
            return walked, True
    return walked, True
