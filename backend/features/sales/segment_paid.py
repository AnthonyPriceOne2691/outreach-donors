"""Платный поиск адреса — в пределах сметы, кусками, с резервом: повтор (`walk`,
`segment_run.again`) и первый поиск принятым (`first_search`, `segment_run.execute`).

Платить сверх того, что человек подтвердил запуском, не должен ни повтор, который идёт сам, ни
первый поиск (`segment_queue.paid_left`: смета минус принятые провайдером запросы, незаписанные и
резерв).

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
- **Первый поиск принятым — теми же пачками** (`first_search`, находка #447): не больше остатка и
  пачки поиска, резерв до запросов и итог каждой пачки в отчёт своим коммитом. До правки он шёл
  одним вызовом лестницы без резерва, итог ложился в отчёт только в конце, и сбой посреди терял из
  счёта и пачки до него, и упавшую. Сайты прохода выбираются один раз: «не спросили» у первого
  поиска ждут и следующего прохода, и выборка по кругу брала бы их снова. **Сайты пачки — «спрошен,
  ответа нет» тем же коммитом, что резерв**: упал — повтор задачи их не берёт и за те же сайты
  второй раз не платит, а исход — в обычном повторе по сроку, не раньше `SALES_SEGMENT_AGAIN_MIN`, и
  платная ступень там — в пределах остатка, где упавшая пачка уже потрачена. Без сбоя исход пачки
  пометку перезаписывает (счёт проходов и причину она не трогает). Остаток кончился — остальным
  только бесплатные ступени: «не спросили». Готовность платной ступени пачка узнаёт сама
  (`contacts.search.paid_step`): квота кончилась — у сайта её слова, а не безличное «не спросили».
"""

from __future__ import annotations

import logging
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import contacts as contacts_cfg
from backend.config import sales as sales_cfg
from backend.features.contacts import search as contacts_search
from backend.features.contacts.repository import domain_ids
from backend.features.core.domain import ContactStatus
from backend.features.sales.segment_counts import contacts_report, number
from backend.features.sales.segment_models import SalesSegmentCandidateModel as Candidate
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


async def _reserve(
    session: AsyncSession, run: Run, size: int, *, asked: Collection[int] = ()
) -> None:
    """Резерв куска — в отчёт своим коммитом до запросов; `asked` — домены пачки первого поиска:
    тем же коммитом им «спрошен, ответа нет» (шапка модуля)."""
    contacts = _contacts(run)
    contacts[PENDING_KEY] = size
    run.report = {**run.report, "contacts": contacts}
    if asked:
        await session.execute(
            update(Candidate)
            .where(Candidate.run_id == run.id, Candidate.domain_id.in_(list(asked)))
            .values(contact_status=ContactStatus.NO_ANSWER, contact_attempted_at=datetime.now(UTC))
        )
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


async def first_search(session: AsyncSession, run: Run, waiting: int) -> int:
    """Первый поиск принятым при остатке сметы (шапка модуля): сайты прохода — один раз, пачками
    с резервом и пометкой, пока остаток есть; затем — только бесплатные ступени. Возвращает,
    скольких сайтов прошёл поиск."""
    hosts = await SegmentContactQueue(session, run.id).pending_hosts(limit=waiting)
    known = await domain_ids(session, hosts)
    rest = [known[host] for host in hosts if host in known]
    walked = 0
    while rest and (left := paid_left(run.report, run.estimate)) > 0:
        size = min(left, contacts_search.BATCH)
        batch, rest = rest[:size], rest[size:]
        await _reserve(session, run, len(batch), asked=batch)
        walked += await _search(session, run, batch, no_paid=False)
    if rest:
        logger.info(
            "сбор продаж: остаток сметы платного поиска выбран — остальным бесплатные ступени",
            extra={"run": run.id, "sites": len(rest)},
        )
        walked += await _search(session, run, rest, no_paid=True)
    return walked


async def _search(session: AsyncSession, run: Run, chosen: list[int], *, no_paid: bool) -> int:
    """Поиск сайтов, выбранных заранее (`SegmentContactQueue(only=…)`), — итог в отчёт."""
    queue = SegmentContactQueue(session, run.id, only=chosen)
    found = await contacts_search.search_contacts(
        session, limit=len(chosen), queue=queue, no_paid=no_paid
    )
    return await merged(session, run, found.as_dict())


def unpaid(run: Run) -> bool:
    """`no_paid` первого поиска без остатка сметы (`segment_run._contacts`): платная ступень
    выключена у сбора или её не было в смете — «не спросили»; ключ задан — смета выбрана, и сверх
    неё не платим; включена, а ключа нет — ступень скажет сама («не настроен», «адреса нет»
    честное), платить ей нечем."""
    estimated = sales_cfg.SEGMENT_PAID_CONTACTS and number(run.estimate.get("hunter_searches")) > 0
    return not estimated or bool(contacts_cfg.HUNTER_API_KEY)
