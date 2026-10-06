"""Задачи Этапа 2: обход донора (`crawl_donor`) и пересчёт кандидатов (`judge_crawl`).

**Обход — в своей очереди** (`crawl`) у своего сервиса (`crawler`, N процессов).
Он идёт до получаса, и в общей очереди занял бы единственный воркер: разбор
ответов и сборка писем ждали бы его.

**Пересчёт — короткой задачей в общую очередь** (`runs`). В нём платный шаг —
DR у Ahrefs, ключ общий с соседней системой, а ограничитель частоты живёт
в памяти процесса: платное идёт из одного места по одному, а не из четырёх
обходчиков разом.

Задача — тонкая обёртка над ядром, как и в `workers/jobs.py`: порядок обхода
живёт в `crawl/walk.py`, пачки и чекпоинт — в `crawl/progress.py`, запись —
в `crawl/repository.py`, разбор мёртвых — в `crawl/lifecycle.py`.

**Выкатка не обрывает обход.** Сервис передаёт просьбу докера остановиться
процессу задачи (`workers/crawler.py`), обработчик ставит флаг (`ask_to_stop`),
обход дописывает пачку на границе страницы и выходит с чекпоинтом, а задача
ставит себе продолжение. Флаг — на процесс: rq выполняет каждую задачу
в свежем дочернем процессе, и флаг рождается чистым.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import threading
from collections import Counter
from types import FrameType
from typing import Any

from rq import get_current_job
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import ahrefs as ahrefs_cfg
from backend.config import crawl as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import UsageCollector
from backend.features.contacts.browser import PlaywrightRenderer
from backend.features.core import usage
from backend.features.core.domain import CrawlStatus
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl.big_sites import ahrefs_ratings
from backend.features.crawl.gate import judge_run
from backend.features.crawl.lifecycle import FAILURE_KEY, REASON_KEY, heartbeat, noted
from backend.features.crawl.progress import Batch, Checkpoint, CrawlInterruptedError, Progress
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.repository import finish_crawl, save_batch
from backend.features.crawl.walk import DonorCrawler
from backend.features.runs.failures import described
from backend.features.runs.reasons import explained
from backend.shared.logs import setup_logging
from backend.shared.net.url_guard import guarded_client
from backend.shared.queue import (
    JUDGE_CRAWL_JOB,
    crawl_job_id,
    enqueue_crawl,
    runs_queue,
    with_retries,
)

logger = logging.getLogger(__name__)

#: Сколько соединений с базой держит один обход: основная запись и удар
#: о жизни. Обходчиков N, и пул по умолчанию (5 + 10) на каждого съел бы
#: потолок соединений базы (60 на проде) на четырёх.
POOL_SIZE = 2

_STOP = threading.Event()


def ask_to_stop(_signum: int | None = None, _frame: FrameType | None = None) -> None:
    """Обработчик SIGTERM в процессе задачи: остановиться на границе страницы."""
    _STOP.set()


def stop_requested() -> bool:
    return _STOP.is_set()


def crawl_donor(crawl_run_id: int) -> dict[str, Any]:
    """Обойти донора по строке обхода — с начала или с чекпоинта.

    Довод один — номер обхода: хост и чекпоинт лежат в строке, и продолжение
    ставит ту же задачу с тем же доводом.
    """
    setup_logging()
    check_storage()
    return asyncio.run(_crawl(crawl_run_id))


async def _crawl(run_id: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN, pool_size=POOL_SIZE, max_overflow=1)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        taken = await _take(factory, run_id)
        if isinstance(taken, dict):
            return taken
        host, point = taken
        try:
            report = await _walk(factory, run_id, host, point)
        except CrawlInterruptedError as exc:
            logger.info("обход №%s (%s): %s", run_id, host, exc)
            return await _requeue(factory, run_id, exc.checkpoint)
        except Exception as exc:
            # Не глушим: очередь видит падение, разбор мёртвых продолжит обход
            # с чекпоинта. Причина — в строке обхода уже сейчас.
            await _note(factory, run_id, f"сбой, будет продолжен: {explained(exc)}", exc)
            raise
        stats = await _finish(factory, run_id, report)
        return {
            "crawl": run_id,
            "host": host,
            "outcome": report.outcome.value,
            "pages": len(report.pages),
            "links": stats["links_found"],
            "judge_job": _judge_later(run_id),
        }
    finally:
        await engine.dispose()


def _judge_later(run_id: int) -> str | None:
    """Пересчёт кандидатов — в общую очередь. Обход уже записан «done», и падать
    из-за молчащей очереди здесь незачем: кандидатов досчитает `outreach advertisers`."""
    try:
        return str(runs_queue().enqueue(JUDGE_CRAWL_JOB, run_id, **with_retries()).id)
    except Exception:
        logger.exception(
            "обход №%s закончен, но пересчёт кандидатов не поставлен — "
            "досчитать: outreach advertisers --run %s",
            run_id,
            run_id,
        )
        return None


async def _take(
    factory: async_sessionmaker[AsyncSession], run_id: int
) -> tuple[str, Checkpoint | None] | dict[str, Any]:
    """Взять обход: «идёт», номер этой задачи. Законченный — не трогать."""
    async with factory() as session:
        run = await session.get(CrawlRunModel, run_id)
        if run is None:
            logger.warning("обход №%s: строки нет — задаче нечего делать", run_id)
            return {"crawl": run_id, "missing": True}
        if run.status not in (CrawlStatus.QUEUED, CrawlStatus.RUNNING):
            logger.warning("обход №%s уже %s — задача не нужна", run_id, run.status.value)
            return {"crawl": run_id, "skipped": run.status.value}
        job = get_current_job()
        run.status = CrawlStatus.RUNNING
        run.job_id = job.id if job is not None else run.job_id
        host, point = run.host, Checkpoint.of(run.checkpoint)
        await session.commit()
    logger.info("обход №%s (%s): %s", run_id, host, "продолжение" if point else "начало")
    return host, point


async def _walk(
    factory: async_sessionmaker[AsyncSession], run_id: int, host: str, point: Checkpoint | None
) -> CrawlReport:
    async def flush(batch: Batch) -> None:
        async with factory() as session:
            await save_batch(session, run_id, batch)
            await session.commit()

    progress = Progress(flush=flush, resume=point, stop=stop_requested)
    beat = asyncio.create_task(heartbeat(factory, run_id))
    try:
        async with contextlib.AsyncExitStack() as stack:
            # Браузер — только когда включён: без этого условия каждая задача
            # искала бы необязательный пакет и предупреждала о его отсутствии.
            renderer = (
                await stack.enter_async_context(PlaywrightRenderer())
                if cfg.BROWSER_ENABLED
                else None
            )
            client = await stack.enter_async_context(guarded_client(timeout=cfg.PAGE_TIMEOUT_SEC))
            crawler = DonorCrawler(
                client, renderer=renderer, use_browser=renderer is not None, identify=cfg.IDENTIFY
            )
            try:
                return await crawler.crawl(host, progress)
            finally:
                await crawler.aclose()
    finally:
        beat.cancel()


async def _requeue(
    factory: async_sessionmaker[AsyncSession], run_id: int, point: Checkpoint
) -> dict[str, Any]:
    """Остановлен просьбой (выкатка) — поставить продолжение. Не неудача:
    продолжений разбора не тратит. Номер задачи записан до постановки."""
    job_id = crawl_job_id(run_id)
    reason = f"остановлен на {len(point.pages)} страницах (выкатка или перезапуск) — продолжится"
    async with factory() as session:
        run = await session.get(CrawlRunModel, run_id)
        if run is not None:
            run.status, run.job_id = CrawlStatus.QUEUED, job_id
            run.stats = noted(run, **{REASON_KEY: reason})
            await session.commit()
    enqueue_crawl(run_id, job_id)
    logger.info("обход №%s: %s, задача %s", run_id, reason, job_id)
    return {"crawl": run_id, "interrupted": True, "pages": len(point.pages), "next_job": job_id}


async def _note(
    factory: async_sessionmaker[AsyncSession], run_id: int, reason: str, failed: BaseException
) -> None:
    """Причина сбоя — в строку обхода. Своя сессия: прежняя могла сломаться
    вместе с задачей. Сбой записи не глушит исходную ошибку."""
    try:
        async with factory() as session:
            run = await session.get(CrawlRunModel, run_id)
            if run is not None:
                run.stats = noted(
                    run, **{REASON_KEY: reason[:500], FAILURE_KEY: described(failed)[:500]}
                )
                await session.commit()
    except Exception:
        logger.exception("обход №%s: причину «%s» записать не удалось", run_id, reason[:120])


async def _finish(
    factory: async_sessionmaker[AsyncSession], run_id: int, report: CrawlReport
) -> dict[str, Any]:
    async with factory() as session:
        run = await session.get(CrawlRunModel, run_id)
        if run is None:
            raise ValueError(f"обход №{run_id} исчез из базы до конца обхода")
        stats = await finish_crawl(session, run, report)
        await session.commit()
    return stats


def judge_crawl(crawl_run_id: int) -> dict[str, Any]:
    """Посчитать кандидатов законченного обхода — сразу после него, без кнопки.

    DR для «DR > 80 — не пишем» спрашивается, если есть ключ; расход —
    в журнал той же транзакцией, что и вердикты.
    """
    setup_logging()
    check_storage()
    return asyncio.run(_judge(crawl_run_id))


async def _judge(run_id: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    spent = UsageCollector()
    client = AhrefsClient(on_usage=spent) if ahrefs_cfg.API_KEY else None
    ratings = functools.partial(ahrefs_ratings, client) if client is not None else None
    try:
        async with factory() as session:
            candidates = await judge_run(session, run_id, ratings=ratings)
            for operation, cost in spent.drain():
                usage.record(session, operation=operation, units=cost.billable)
            await session.commit()
    finally:
        if client is not None:
            await client.aclose()
        await engine.dispose()
    verdicts = Counter(candidate.verdict.value for candidate in candidates)
    return {"crawl": run_id, "candidates": len(candidates), "by_verdict": dict(verdicts)}
