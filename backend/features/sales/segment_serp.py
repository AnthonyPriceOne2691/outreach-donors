"""Выдача прогона гипотезы → кандидаты сбора: один раз за прогон, со строкой расхода.

Выдача — общий путь прогонов доноров (`runs/planning.gather_candidates`): ключи уходят
источнику выдачи пачкой, адреса сводятся к корневым доменам (`domains.host` — ключ дедупа),
у каждого домена — текст его верхней позиции: по нему судит судья сегмента. Своей копии
разбора выдачи нет.

**Платная часть — один раз.** Цена выдачи пишется строкой расхода `sales_serp` в той же
транзакции, что кандидаты, и этап отмечается сделанным: повтор задачи после сбоя выдачу
заново не покупает (вызывающий коммитит сразу).

**Домен, уже собранный гипотезой, второй раз не заводится**: его не судят и не ищут ему
адрес снова — отчёт называет его «уже в сборе гипотезы».
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from backend.features.core import usage
from backend.features.donors.repository import DonorRepository
from backend.features.runs.planning import Candidates, gather_candidates
from backend.features.sales.segment_models import (
    Lane,
    SalesSegmentCandidateModel,
    SalesSegmentRunModel,
    Stage,
)
from backend.features.serp.protocol import SerpProvider

#: Операция журнала расхода: выдача сбора продаж, единица — доллар.
SERP_OPERATION = "sales_serp"
#: Строк в одной вставке: параметров у Postgres не больше 32 767 на запрос.
CHUNK = 1000


def _rows(
    run: SalesSegmentRunModel, found: Candidates, ids: dict[str, int]
) -> list[dict[str, Any]]:
    rows = []
    for host in found.hosts:
        if host not in ids:
            continue
        page = found.texts.get(host)
        keys = found.found_by.get(host) or []
        rows.append(
            {
                "run_id": run.id,
                "hypothesis_id": run.hypothesis_id,
                "domain_id": ids[host],
                "keyword": keys[0] if keys else None,
                "page_url": page.url if page else None,
                "page_title": page.title if page else None,
                "page_description": page.description if page else None,
                "lane": Lane.PENDING.value,
            }
        )
    return rows


async def _insert_new(session: AsyncSession, rows: Sequence[dict[str, Any]]) -> int:
    """Новые кандидаты гипотезы; уже собранные домены остаются как были."""
    new = 0
    for start in range(0, len(rows), CHUNK):
        statement = (
            insert(SalesSegmentCandidateModel)
            .values(list(rows[start : start + CHUNK]))
            .on_conflict_do_nothing(constraint="uq_sales_segment_candidates_domain")
            .returning(SalesSegmentCandidateModel.id)
        )
        new += len((await session.scalars(statement)).all())
    return new


async def collect(
    session: AsyncSession, run: SalesSegmentRunModel, provider: SerpProvider
) -> dict[str, Any]:
    """Купить выдачу по ключам прогона и завести кандидатов. Коммит — за вызывающим,
    сразу: строка расхода и кандидаты — одна транзакция."""
    found = await gather_candidates(
        provider, run.keywords, run.country, depth_pages=run.depth_pages
    )
    if found.cost_usd:
        usage.record(session, operation=SERP_OPERATION, amount_usd=found.cost_usd)
    domains = DonorRepository(session)
    ids: dict[str, int] = {}
    for start in range(0, len(found.hosts), CHUNK):
        ids |= await domains.ensure_domains(found.hosts[start : start + CHUNK])
    new = await _insert_new(session, _rows(run, found, ids))
    tally = {
        "keywords": found.keywords,
        "results": found.results,
        "hosts": len(found.hosts),
        "new": new,
        "known": len(found.hosts) - new,
        "dropped": found.dropped,
        "empty_keywords": list(found.empty_keywords),
        "lost_keywords": list(found.lost_keywords),
        "cost_usd": round(found.cost_usd, 4),
    }
    run.report = {**run.report, "serp": tally}
    run.stage = Stage.SERP
    return tally
