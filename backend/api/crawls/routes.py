"""Обходы Этапа 2: кого можно обойти, запуск и ход.

**Запуск кладёт обход в очередь обходов, а не выполняет его.** Обход донора
идёт до получаса; порядок постановки общий с консолью (`crawl/launch.py`):
строка обхода и номер задачи — до постановки, второй незаконченный обход
донора не заводится, отказ очереди закрывает обход с причиной.

**Обходят только доноров со свежей ценой** (`targets.choose`) — тем же
отбором, что предлагает экран. Проверка на сервере, а не только в списке
экрана: оффер «мы дешевле» без свежей цены донора — обещание, которого
не сдержать, и запрос мимо экрана не должен это обходить.

Смотреть — под правом `view`, запускать — под `run`: это то же «запустить
работу, которая идёт сама», что и прогон Этапа 1.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.crawls.schemas import (
    CrawlRow,
    CrawlsView,
    DonorRow,
    LaunchBody,
    LaunchResult,
    TargetsView,
)
from backend.api.deps import db_session, needs
from backend.config import crawl as crawl_cfg
from backend.config import filters
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Permission
from backend.features.core.models.access import UserModel
from backend.features.crawl import board, targets
from backend.features.crawl.launch import launch
from backend.shared.queue import CRAWL_QUEUE_NAME, crawl_job_id, enqueue_crawl, workers_alive

router = APIRouter(prefix="/crawls", tags=["обходы"])

_viewer = Depends(needs(Permission.VIEW))
_runner = Depends(needs(Permission.RUN))

#: Потолок отбора при проверке запуска: доноров со свежей ценой — единицы
#: и десятки, а проверить надо каждого названного.
_ALL = 10_000


def _workers() -> int | None:
    return workers_alive(queue=CRAWL_QUEUE_NAME)


@router.get("/targets", response_model=TargetsView, summary="Кого можно обойти")
async def crawl_targets(
    limit: int = Query(default=100, ge=1, le=500),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> TargetsView:
    """Доноры со свежей ценой — с последним обходом каждого. Кого нельзя —
    числами и словами, что делать (цену перезапросить, прогон запустить)."""
    found = await board.eligible(session, limit=limit)
    pages = crawl_cfg.MAX_PAGES_PER_DONOR
    return TargetsView(
        donors=[DonorRow.of(line, max_pages=pages) for line in found.donors],
        no_price=len(found.chosen.no_price),
        stale_price=len(found.chosen.stale_price),
        supplier=len(found.chosen.supplier),
        notes=targets.explain(found.chosen),
        max_pages=pages,
        workers=_workers(),
    )


@router.get("", response_model=CrawlsView, summary="Последние обходы и их ход")
async def crawls(
    limit: int = Query(default=20, ge=1, le=100),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> CrawlsView:
    pages = crawl_cfg.MAX_PAGES_PER_DONOR
    return CrawlsView(
        rows=[
            CrawlRow.of(line, max_pages=pages) for line in await board.recent(session, limit=limit)
        ],
        active=await board.active_count(session),
        workers=_workers(),
    )


def _why_not(host: str, chosen: targets.Targets) -> str | None:
    """Почему донора не обходим. `None` — обходим."""
    names = {name.lower(): name for name in chosen.hosts}
    if host in names:
        return None
    if host in {name.lower() for name in chosen.stale_price}:
        return f"цена старше {filters.PRICE_TTL_DAYS} дней — сначала перезапрос цены"
    if host in {name.lower() for name in chosen.no_price}:
        return "цены нет — по донору без цены Этап 2 не запускается; знаете цену — укажите её"
    if host in {name.lower() for name in chosen.supplier}:
        return "донор-поставщик — его рекламодателей не трогаем"
    return "не принятый донор: Этап 2 — по донорам из базы Этапа 1 или заведённым вручную с ценой"


@router.post("", response_model=LaunchResult, summary="Обойти доноров")
async def start(
    body: LaunchBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> LaunchResult:
    """Поставить обходы. Каждый донор — отдельной строкой и задачей; занятый
    и не подходящий называются, а не пропадают молча."""
    chosen = await targets.choose(session, limit=_ALL)
    asked = list(dict.fromkeys(h.strip().lower().removeprefix("www.") for h in body.hosts))
    refused = {host: why for host in asked if (why := _why_not(host, chosen)) is not None}
    done = await launch(
        session,
        [host for host in asked if host not in refused],
        by=author.email,
        job_id=crawl_job_id,
        enqueue=enqueue_crawl,
    )
    audit = AccessRepository(session)
    for host, run_id in done.queued.items():
        await audit.record(
            AuditAction.RUN_STARTED,
            author_id=author.id,
            target=f"crawl:{run_id}",
            details={"этап": "2 — обход донора", "донор": host},
        )
    await session.commit()
    return LaunchResult(
        queued=done.queued,
        busy=done.busy,
        failed=done.failed,
        refused=refused,
        workers=_workers(),
    )
