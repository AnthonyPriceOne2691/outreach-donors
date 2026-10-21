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
  коммитом и снимается после куска. Проход упал посреди куска — резерв остаётся, и следующий проход
  прогона сводит его (`settle`; номер задачи прогона держит он, так что прежний мёртв): **весь — в
  «принято, но не записано»** (ревью #426). Сайт куска — не больше одного платного запроса, и
  резерв — верхняя граница оплаченного; точнее не узнать: строка журнала расхода поиска прогона не
  знает, а исход сайта пишется домену во всех прогонах (`attempts.record`). Прежняя оценка «журнал с
  момента резерва + сайты куска без исхода» теряла оплаченное целиком — до пачки поиска, 32, — когда
  между сбоем и сведением те же сайты проходил прогон другой гипотезы. Пересчёт — в сторону траты:
  лучше остаток сметы меньше на неоплаченное, чем повтор, заплативший сверх сметы.
- **Провайдер отказал или кусков набралось на проход** — остальных сайтов этот проход не трогает
  (`walk` → `held`): бесплатные ступени отправили бы ответивших без адреса в «адреса нет» при живом
  остатке, а следующий проход продолжит. **Отказ** (квота, частота, сбой, ключ) — моментом в отчёт
  (`REFUSED_AT`, ревью #426): сроки задержанных сайтов уже настали, и проход через минуту упёрся бы в
  ту же стену, — следующий по сроку не раньше `SALES_SEGMENT_AGAIN_MIN` после отказа
  (`segment_again.next_at`, `segment_jobs.put_due`). Кусков набралось — продолжит через минуту.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.contacts import search as contacts_search
from backend.features.sales.segment_counts import contacts_report, number
from backend.features.sales.segment_models import SalesSegmentRunModel as Run
from backend.features.sales.segment_queue import Retry, SegmentContactQueue, paid_left
from backend.features.sales.segment_spend import PENDING_KEY, UNRECORDED_KEY, pending

logger = logging.getLogger(__name__)

#: Кусков платного поиска за проход — не больше: остальное — следующему проходу.
CHUNKS = 20
#: Когда платная часть повтора последний раз получила отказ провайдера (UTC, `merged`): проход по
#: сроку — не раньше `SALES_SEGMENT_AGAIN_MIN` после него (`segment_again.paused_until`).
REFUSED_AT = "paid_refused_at"


async def ready() -> bool:
    """Платная ступень у прохода есть — тем же правилом, что у поиска (`contacts.search`): ключ
    задан, остаток узнаётся и не выбран."""
    async with httpx.AsyncClient() as http:
        return await contacts_search.paid_step(http, contacts_search.SearchReport()) is not None


def _contacts(run: Run) -> dict[str, Any]:
    return dict(run.report.get("contacts") or {})


async def settle(session: AsyncSession, run: Run) -> None:
    """Свести резерв, оставленный упавшим проходом, — весь в «принято, но не записано» (шапка
    модуля): в начале каждого прохода прогона (`segment_run.execute`, `again`); резерва нет —
    ничего."""
    reserved = pending(run.report)
    if not reserved:
        return
    contacts = _contacts(run)
    contacts[UNRECORDED_KEY] = number(contacts.get(UNRECORDED_KEY)) + reserved
    contacts[PENDING_KEY] = 0
    run.report = {**run.report, "contacts": contacts}
    await session.commit()
    logger.warning(
        "сбор продаж: резерв платного поиска после сбоя сведён — весь в «принято без записи»",
        extra={"run": run.id, "reserved": reserved},
    )


async def _reserve(session: AsyncSession, run: Run, size: int) -> None:
    contacts = _contacts(run)
    contacts[PENDING_KEY] = size
    run.report = {**run.report, "contacts": contacts}
    await session.commit()


def refused_at(report: Mapping[str, Any]) -> datetime | None:
    """Последний отказ провайдера платной части повтора (`REFUSED_AT`); не было или момент не
    разобрать (запись правили руками) — пусто: паузы нет, срок прохода — как без отказа."""
    contacts = report.get("contacts")
    raw = contacts.get(REFUSED_AT) if isinstance(contacts, Mapping) else None
    if not isinstance(raw, str):
        return None
    try:
        moment = datetime.fromisoformat(raw)
    except ValueError:
        logger.warning("сбор продаж: момент отказа провайдера не разобран", extra={"value": raw})
        return None
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


async def merged(session: AsyncSession, run: Run, found: dict[str, Any]) -> int:
    """Итог куска поиска — в отчёт прогона своим коммитом, резерв куска снят; провайдер отказал —
    и момент отказа (`REFUSED_AT`). Возвращает, скольких сайтов он прошёл."""
    contacts = contacts_report(run.report.get("contacts"), found)
    contacts[PENDING_KEY] = 0
    if _refused(found):
        contacts[REFUSED_AT] = datetime.now(UTC).isoformat()
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
