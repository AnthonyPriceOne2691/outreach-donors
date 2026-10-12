"""Задача прогона гипотезы (`run_segment`): экран ставит, воркер продаж проводит.

Тонкая обёртка над `segment_run.execute`, как у очистки (`clean_jobs.py`): своя сессия, свои
клиенты — выдача по настройке (`serp/factory.build_provider`), модель судьи, клиент главных
с защитой исходящих (адреса приходят из выдачи) и проверка адресов по настройке. Очередь
зовёт задачу путём строкой (`SEGMENT_JOB`): общий код очереди о продажах знает одну строку —
имя задачи словами (`ops/job_outcome.KINDS`).

**Отказы.** Постоянный (`permanent`: настройка, отказ провайдера по ключу, потолок модели) —
итог задачи и причина в строке прогона словами, а не три повтора. Остальное (сеть, база) —
исключение, и очередь повторит задачу: этапы закоммичены, повтор продолжает с места и не
покупает выдачу второй раз.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from typing import Any

import httpx
from rq import get_current_job
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import judge as judge_cfg
from backend.config import llm as llm_cfg
from backend.config import sales as sales_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.ahrefs.client import AhrefsClient
from backend.features.runs.failures import described, is_permanent
from backend.features.sales.segment_estimate import SALES_OFF, SERP_AHREFS
from backend.features.sales.segment_judge import SegmentClient
from backend.features.sales.segment_models import SalesSegmentRunModel, Stage
from backend.features.sales.segment_run import RunDeps, execute
from backend.features.sales.verifier import build_verifier
from backend.features.serp.factory import AHREFS, UnknownProviderError, build_provider
from backend.features.serp.protocol import SerpProvider
from backend.shared.logs import setup_logging
from backend.shared.net.url_guard import guarded_client
from backend.shared.queue import remember_job_error

logger = logging.getLogger(__name__)

#: Путь задачи строкой: очередь импортирует её в воркере продаж.
SEGMENT_JOB = "backend.features.sales.segment_jobs.run_segment"


class SerpNotSetError(RuntimeError):
    """Источник выдачи не собирается по настройке. Повтор это не исправит."""

    permanent = True


class SalesOffError(RuntimeError):
    """Модуль продаж выключили после запуска: сбор не тратит. Повтор это не исправит."""

    permanent = True


def segment_job_id(run_id: int) -> str:
    """Номер задачи прогона — от прогона: один проход прогона за раз."""
    return f"sales-segment-{run_id}"


async def _stop(factory: async_sessionmaker[Any], run_id: int, words: str) -> None:
    """Причину остановки — в строку прогона: экран читает её оттуда."""
    async with factory() as session:
        run = await session.get(SalesSegmentRunModel, run_id)
        if run is not None:
            run.stage, run.error = Stage.STOPPED, words
            await session.commit()


def _serp(ahrefs: AhrefsClient) -> SerpProvider:
    """Источник выдачи прогона. Не собирается по настройке или это Ahrefs (настройку сменили
    после сметы: его юниты ушли бы мимо журнала, `SERP_AHREFS`) — постоянный отказ словами."""
    try:
        serp = build_provider(ahrefs)
    except UnknownProviderError as exc:
        raise SerpNotSetError(str(exc)) from exc
    if serp.name == AHREFS:
        raise SerpNotSetError(SERP_AHREFS)
    return serp


async def _with_clients(factory: async_sessionmaker[Any], run_id: int) -> dict[str, Any]:
    """Клиенты прохода — и сам проход; клиенты закрываются при любом исходе."""
    ahrefs = AhrefsClient()
    try:
        serp = _serp(ahrefs)
        async with AsyncExitStack() as stack:
            # Модель судьи — со своим сроком (`LLM_TIMEOUT_S`), как у прогона доноров: с умолчанием
            # httpx (5 с) модель с рассуждением не успевала ни в одну из трёх попыток, а оборванный
            # запрос провайдер может довести и выставить счёт мимо журнала. Проверка адресов — на
            # своём клиенте с умолчанием, как у очистки (`clean_jobs.py`).
            model = await stack.enter_async_context(httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S))
            http = await stack.enter_async_context(httpx.AsyncClient())
            home = await stack.enter_async_context(
                guarded_client(timeout=judge_cfg.HOME_TIMEOUT_SEC)
            )
            session = await stack.enter_async_context(factory())
            deps = RunDeps(
                serp=serp,
                judge=SegmentClient(model),
                home=home if judge_cfg.HOME_CHECK else None,
                verifier=build_verifier(http),
            )
            try:
                return await execute(session, run_id, deps)
            finally:
                closing = getattr(serp, "aclose", None)
                if closing is not None:
                    await closing()
    finally:
        await ahrefs.aclose()


async def run_once(run_id: int) -> dict[str, Any]:
    """Один проход прогона: своя сессия и свои клиенты на задачу. Постоянный отказ —
    причиной в строке прогона: экран читает её оттуда."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if not sales_cfg.ENABLED:
            raise SalesOffError(SALES_OFF)
        return await _with_clients(factory, run_id)
    except Exception as exc:
        if is_permanent(exc):
            await _stop(factory, run_id, described(exc))
        raise
    finally:
        await engine.dispose()


def run_segment(run_id: int) -> dict[str, Any]:
    """Задача очереди: провести прогон гипотезы с места, где он стоит. Писем не пишет."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(run_once(run_id))
    except Exception as exc:
        if is_permanent(exc):
            logger.warning(
                "сбор продаж: прогон не выполнен", extra={"run": run_id, "error": described(exc)}
            )
            return {"error": described(exc), "permanent": True}
        if (job := get_current_job()) is not None:
            remember_job_error(job.id, described(exc))
        raise
