"""Обходы Этапа 2: кого можно обойти, запуск и ход.

**Запуск кладёт обход в очередь обходов, а не выполняет его.** Обход донора
идёт до получаса; порядок постановки общий с консолью (`crawl/launch.py`):
строка обхода и номер задачи — до постановки, второй незаконченный обход
донора не заводится, отказ очереди закрывает обход с причиной.

**Обходят только принятых доноров со свежей ценой** (`targets.choose`) — тем
же отбором, что предлагает экран. Проверка на сервере, а не только в списке
экрана: оффер «мы дешевле» без свежей цены донора — обещание, которого не
сдержать, отклонённый человеком — не донор, и мимо экрана это не обойти.

Смотреть — под правом `view`, запускать — под `run`: это то же «запустить
работу, которая идёт сама», что и прогон Этапа 1.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.crawls.donor_schemas import (
    BulkBody,
    BulkResult,
    CountsView,
    DonorsView,
    SummaryView,
    donor_params,
)
from backend.api.crawls.schemas import (
    MAX_HOSTS,
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
from backend.features.crawl import board, donor_page, targets
from backend.features.crawl.board import CrawlLine
from backend.features.crawl.donor_page import DonorQuery
from backend.features.crawl.launch import launch
from backend.shared.crawl_marks import Held, crawlers_held
from backend.shared.queue import (
    CRAWL_BULK_JOB,
    CRAWL_QUEUE_NAME,
    crawl_job_id,
    enqueue_crawl,
    job_positions,
    quick_queue,
    with_retries,
    workers_alive,
)

router = APIRouter(prefix="/crawls", tags=["обходы"])

_viewer = Depends(needs(Permission.VIEW))
_runner = Depends(needs(Permission.RUN))

#: Потолок отбора при проверке запуска: доноров со свежей ценой — единицы
#: и десятки, а проверить надо каждого названного.
_ALL = 10_000


async def _workers() -> int | None:
    """Сколько обходчиков слушает очередь. Клиент Redis синхронный — в пуле потоков, а не
    в цикле событий: зависший Redis иначе держал бы весь процесс API (аудит 10.10.2026)."""
    return await asyncio.to_thread(workers_alive, queue=CRAWL_QUEUE_NAME)


async def _held() -> Held | None:
    """Скольких обходчиков держит сторож нагрузки — тем же пулом потоков, что `_workers`.
    Не спросили — `None`, а не ноль: экран тогда молчит, а не говорит «никого»."""
    return await asyncio.to_thread(crawlers_held)


async def _queue(session: AsyncSession, lines: list[CrawlLine | None]) -> board.QueueState:
    """Занятые обходчики и места показанных ждущих обходов — для строки «в очереди». Место
    спрашивается только у показанных задач (`job_positions`), а не всей очередью: при десяти
    тысячах в очереди экран читал бы её целиком на каждом опросе. Очередь — в пуле потоков, как
    `_workers`: не ответила — у ждущих нет места, а не ошибка экрана."""
    waiting = board.waiting_jobs(line for line in lines if line is not None)
    places = await asyncio.to_thread(job_positions, list(waiting.values()), queue=CRAWL_QUEUE_NAME)
    return board.queue_state(await board.running_count(session), waiting, places)


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
    workers, held = await _workers(), await _held()
    queue = await _queue(session, [donor.crawl for donor in found.donors])
    return TargetsView(
        donors=[
            DonorRow.of(line, max_pages=pages, queue=queue, workers=workers, held=held)
            for line in found.donors
        ],
        no_price=len(found.chosen.no_price),
        stale_price=len(found.chosen.stale_price),
        supplier=len(found.chosen.supplier),
        notes=targets.explain(found.chosen),
        max_pages=pages,
        workers=workers,
        held=None if held is None else held.count,
        held_reason=None if held is None else held.words,
        health_window=crawl_cfg.HEALTH_WINDOW,
        slow_down_share=crawl_cfg.SLOW_DOWN_SHARE,
        stop_share=crawl_cfg.STOP_SHARE,
        max_seconds_slow=crawl_cfg.MAX_SECONDS_SLOW,
    )


@router.get("", response_model=CrawlsView, summary="Последние обходы и их ход")
async def crawls(
    limit: int = Query(default=20, ge=1, le=100),
    page: int = Query(default=1, ge=1, le=100_000, description="страница журнала обходов"),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> CrawlsView:
    pages = crawl_cfg.MAX_PAGES_PER_DONOR
    lines = await board.recent(session, limit=limit, offset=(page - 1) * limit)
    workers, held = await _workers(), await _held()
    queue = await _queue(session, list(lines))
    return CrawlsView(
        rows=[
            CrawlRow.of(line, max_pages=pages, queue=queue, workers=workers, held=held)
            for line in lines
        ],
        active=await board.active_count(session),
        workers=workers,
        held=None if held is None else held.count,
        held_reason=None if held is None else held.words,
        total=await board.total_count(session),
    )


@router.get("/donors", response_model=DonorsView, summary="Доноры вкладки «Обход доноров»")
async def donors(
    query: DonorQuery = Depends(donor_params),
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> DonorsView:
    """Доноры страницей — с условиями, порядком и счётчиками по статусу и цене на сервере
    (`crawl/donor_page.py`): их тысячи, и вкладка не грузит их целиком."""
    found = await donor_page.page(session, query)
    workers, held = await _workers(), await _held()
    return DonorsView.of(
        found,
        query,
        queue=await _queue(session, [row.crawl for row in found.rows]),
        workers=workers,
        held=held,
        typical=await donor_page.typical_seconds(session),
        max_pages=crawl_cfg.MAX_PAGES_PER_DONOR,
    )


@router.get("/summary", response_model=SummaryView, summary="Обход доноров одной строкой")
async def summary(
    _: UserModel = _viewer,
    session: AsyncSession = Depends(db_session),
) -> SummaryView:
    """Счётчики всей выборки и что идёт — для «Проверки», где вкладки нет, а сводка есть."""
    counts, startable = await donor_page.summary(session)
    return SummaryView(
        counts=CountsView.of(counts),
        startable=startable,
        active=await board.active_count(session),
        workers=await _workers(),
    )


def _why_not(host: str, chosen: targets.Targets) -> str | None:
    """Почему донора не обходим. `None` — обходим."""
    names = {name.lower(): name for name in chosen.hosts}
    if host in names:
        return None
    if host in {name.lower() for name in chosen.stale_price}:
        return (
            f"цена старше {filters.PRICE_TTL_DAYS} дней — сначала перезапрос цены "
            "(«Перезапросить цену»)"
        )
    if host in {name.lower() for name in chosen.no_price}:
        return "цены нет — по донору без цены Этап 2 не запускается; знаете цену — укажите её"
    if host in {name.lower() for name in chosen.supplier}:
        return "донор-поставщик — его рекламодателей не трогаем"
    return "не принятый донор: Этап 2 — по донорам из базы Этапа 1 или заведённым вручную с ценой"


async def _audit_runs(session: AsyncSession, author: UserModel, queued: dict[str, int]) -> None:
    """Запуск — в журнал действий строкой на поставленный обход: кто, какого донора."""
    audit = AccessRepository(session)
    for host, run_id in queued.items():
        await audit.record(
            AuditAction.RUN_STARTED,
            author_id=author.id,
            target=f"crawl:{run_id}",
            details={"этап": "2 — обход донора", "донор": host},
        )


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
    await _audit_runs(session, author, done.queued)
    await session.commit()
    return LaunchResult(
        queued=done.queued,
        busy=done.busy,
        failed=done.failed,
        refused=refused,
        workers=await _workers(),
    )


def _defer(hosts: list[str], by: str | None) -> str:
    """Постановка многих — фоновой задачей быстрой очереди (`workers/crawl_bulk.py`)."""
    return str(quick_queue().enqueue(CRAWL_BULK_JOB, hosts, by, **with_retries()).id)


@router.post("/donors/crawl", response_model=BulkResult, summary="Обойти всех доноров под фильтром")
async def crawl_filtered(
    body: BulkBody,
    author: UserModel = _runner,
    session: AsyncSession = Depends(db_session),
) -> BulkResult:
    """Поставить в обход всех под условиями вкладки, кого можно обойти сейчас (свежая цена,
    обход не идёт и не ждёт), — но не больше, чем человек видел на кнопке (`expected`).
    До `MAX_HOSTS` — сразу, как «Обойти отмеченных»; больше — фоновой задачей."""
    hosts = await donor_page.startable_hosts(session, body.query(), limit=body.expected)
    if len(hosts) > MAX_HOSTS:
        job_id = await asyncio.to_thread(_defer, hosts, author.email)
        await AccessRepository(session).record(
            AuditAction.RUN_STARTED,
            author_id=author.id,
            target=f"crawl-bulk:{job_id}",
            details={"этап": "2 — обход доноров по фильтру", "доноров": len(hosts)},
        )
        await session.commit()
        return BulkResult(asked=len(hosts), deferred=True, job_id=job_id, workers=await _workers())
    done = await launch(session, hosts, by=author.email, job_id=crawl_job_id, enqueue=enqueue_crawl)
    await _audit_runs(session, author, done.queued)
    await session.commit()
    return BulkResult(
        asked=len(hosts),
        deferred=False,
        queued=len(done.queued),
        busy=len(done.busy),
        failed=len(done.failed),
        workers=await _workers(),
    )
