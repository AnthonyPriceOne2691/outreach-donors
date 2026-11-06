"""«Переобойти спорные» задачей: тот же проход судьи, что у команды (`crawl/judge_pass.py`).

**Очередь — обходов.** Разбор идёт часами: страницы открываются по одной на донора, с паузой
обходчика. В общей очереди он держал бы прогоны и письма, а обходчиков много, и один из них почти
никому не мешает. Встаёт в начало очереди: нажал человек, а за ним могут стоять тысячи обходов.

**Повторов очереди нет.** У обходчиков нет планировщика (`workers/crawler.py`), отложенный повтор не
сработал бы никогда. И он не нужен: каждый суд записан сразу, упавший разбор человек запускает снова,
и он идёт с места, по новой смете.

**Выкатка не обрывает посреди случая.** Обходчик передаёт просьбу остановиться задаче
(`crawl_jobs.ask_to_stop`), и разбор встаёт на границе пачки. Не успел до убийства — теряется только
случай, который летел.

**Сколько судить — из подтверждённой сметы**: за время в очереди спорных могло стать больше, а
потратить больше, чем видел человек, разбор не должен.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx
from redis.exceptions import RedisError
from rq import get_current_job
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import llm as llm_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.crawl.judge_client import JudgeClient
from backend.features.crawl.judge_estimate import disputed_plan
from backend.features.crawl.judge_pass import report_lines, run_pass
from backend.features.donors.home_signals import home_timeout
from backend.shared.logs import setup_logging
from backend.shared.net.url_guard import guarded_client
from backend.shared.numbers import spaced
from backend.workers.crawl_jobs import POOL_SIZE, stop_requested

# Постоянный отказ — итогом задачи: правило общее у всех задач сервиса (`jobs._settled`).
from backend.workers.jobs import _settled

logger = logging.getLogger(__name__)


def _note_progress(done: int, total: int) -> None:
    """Ход — в мету задачи: его читает экран. Очередь не ответила — разбор идёт дальше."""
    job = get_current_job()
    if job is None:
        return
    job.meta |= {"done": done, "total": total}
    try:
        job.save_meta()  # type: ignore[no-untyped-call]  # rq без аннотаций
    except RedisError as exc:
        logger.warning("разбор спорных: ход не записан — %s", exc)


async def _pass(cases: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN, pool_size=POOL_SIZE, max_overflow=1)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with (
            factory() as session,
            httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S) as model_http,
            guarded_client(timeout=home_timeout()) as site_http,
        ):
            client = JudgeClient(model_http)
            plan = await disputed_plan(session, limit=cases, model=client.model)
            report = await run_pass(
                session,
                plan,
                client=client,
                http=site_http,
                progress=_note_progress,
                stop=stop_requested,
            )
    finally:
        await engine.dispose()
    return {
        "total": len(plan.ids),
        "done": report.done,
        "tokens": report.tokens,
        "left": report.left,
        "stopped": report.stopped,
        "lines": [*report_lines(report), f"токенов: {spaced(report.tokens)}"],
    }


def judge_disputed(cases: int) -> dict[str, Any]:
    """Задача очереди: разобрать спорных, не больше `cases`. Пишет только строки суда."""
    setup_logging()
    check_storage()
    return _settled(lambda: asyncio.run(_pass(cases)), what="разбор спорных")
