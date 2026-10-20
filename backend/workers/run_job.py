"""Тело задачи прогона доноров — вход очереди остаётся `jobs.run_donor_search`.

Своим модулем, а не строками `jobs.py`: тот стоял у предела длины, а у задачи прогона
появился шаг до воронки — списки ссылающихся доменов конкурентов (`runs/competitor_run`).
Своей логики здесь по-прежнему нет: задача собирает исполнителей и зовёт то же ядро, что
консольная команда, а исход пишет в строку прогона.

**Списки — уже с пульсом.** Удары о жизни начинаются до шага конкурентов, а прогон тот шаг
сам переводит в «идёт» до первого списка: до 20.10.2026 списки покупались у прогона «в
очереди» и без пульса, и экран показывал оплачиваемую работу ждущей.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_collect
from backend.features.ahrefs.client import AhrefsClient
from backend.features.crawl.niche import collect as collect_niche
from backend.features.donors.doors import door_check
from backend.features.donors.geo import assert_settings_allow_limited_fetch
from backend.features.donors.repository import DonorRepository
from backend.features.review.candidates import RunReview
from backend.features.runs.budget import ceiling_at_start
from backend.features.runs.competitor_run import journal_of, lists_paid, prepare
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.failures import described, is_permanent
from backend.features.runs.lifecycle import heartbeat
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.reasons import explained
from backend.features.runs.repository import FAILURE_KEY, REASON_KEY, RunRepository
from backend.features.runs.stopped import tell_stopped
from backend.features.runs.thresholds import rules_of
from backend.features.serp.factory import build_provider
from backend.features.serp.modes import mode_of

logger = logging.getLogger(__name__)


async def _run(run_id: int) -> dict[str, Any]:
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    client = AhrefsClient()
    try:
        async with factory() as session:
            runs = RunRepository(session)
            run = await runs.get(run_id)
            # Режим выдачи — из строки прогона: тот, что человек выбрал в смете.
            provider = build_provider(client, mode=mode_of(run.serp_mode))
            settings = await runs.settings_of(run)
            # Пороги, правило региона и срок метрик — своей строки настроек, а не
            # окружения (сверка ТЗ 11.10.2026): вердикт объясним по `settings_id`.
            rules = rules_of(settings)
            # Несогласованное правило региона — отказ словами до первой траты: прогон
            # остановлен с причиной, а не судит молча неверно (`geo.py`).
            assert_settings_allow_limited_fetch(rules.geo.top_n, rules.geo.min_share)
            # Потолок — на старте, а не с нажатия: остаток месяца к этой минуте мог уйти
            # прогонам, поставленным раньше (аудит 10.10.2026, `budget.ceiling_at_start`).
            # Списки конкурентов прошлых попыток — уже из обещанного: остаток капа цикла
            # их учёл сам, по журналу (`competitor_run.lists_paid`).
            promised = settings.units_cap - lists_paid(run)
            cap = await ceiling_at_start(session, run_id=run.id, promised=promised)
            # Удары о жизни идут своей короткой сессией: длинная в это
            # время занята пачкой доменов, и ждать её значит молчать
            # ровно тогда, когда прогон работает.
            async with factory() as ticker, door_check() as doors:
                beat = asyncio.create_task(heartbeat(RunRepository(ticker), run_id))
                try:
                    # Прогон по ссылкам конкурентов идёт в воронку с кандидатами — списки
                    # покупаются уже с пульсом (`competitor_run.prepare`).
                    source = await prepare(session, client, run, rules.thresholds, cap=cap)
                    report = await execute_run(
                        RunDeps(
                            provider=provider,
                            client=client,
                            donors=DonorRepository(session),
                            runs=runs,
                            exclusions=Exclusions(session),
                            review=RunReview(session),
                            doors=doors,
                            niche=lambda run_id: collect_niche(session, run_id),
                        ),
                        RunRequest(
                            keywords=list(run.keywords),
                            country=run.country,
                            thresholds=rules.thresholds,
                            geo=rules.geo,
                            metrics_ttl_days=rules.metrics_ttl_days,
                            settings_id=run.settings_id,
                            cap=source.cap,
                            depth_pages=run.depth_pages,
                            run=run,
                            candidates=source.candidates,
                            spent_before=source.spent,
                        ),
                    )
                finally:
                    beat.cancel()
            await session.commit()
            return {
                "run": run_id,
                "checked": len(report.plan.new),
                "by_status": {status.value: n for status, n in report.by_status.items()},
                "spent_units": report.spent_units,
                # Смета прогона из его строки: у прогона по конкурентам — со списками.
                "estimated_units": run.estimated_units,
            }
    finally:
        await client.aclose()
        await engine.dispose()


async def _search(run_id: int) -> dict[str, Any]:
    """Прогон и его исход — в самом прогоне, а не только в журнале воркера.

    Экран читает причину из записи прогона. Без этой обёртки упавшая задача
    оставляла прогон «в очереди» или «идёт», и правду о нём узнавал только
    разбор мёртвых через три минуты — и то гадая.

    Причина пишется словами человека (`runs/reasons.py`): имя класса
    исключения нужно журналу, а не экрану, и лежит рядом отдельно.
    """
    try:
        check_collect()
        return await _run(run_id)
    except Exception as exc:
        if is_permanent(exc):
            # Повтор не поможет (`runs/failures.py`): закрыть сразу, с причиной.
            logger.warning("Прогон %s остановлен: %s", run_id, described(exc))
            await _mark(run_id, f"остановлен: {explained(exc)}", exc, stop=True)
            return {"run": run_id, "refused": described(exc)}
        # Не глушим: очередь должна увидеть падение, а разбор — продолжить
        # прогон с последней точки. Но причина в прогоне — уже сейчас.
        await _mark(run_id, f"сбой, будет продолжен: {explained(exc)}", exc, stop=False)
        raise


async def _mark(run_id: int, reason: str, failed: BaseException, *, stop: bool) -> None:
    """Записать причину в прогон. Своя сессия: основная могла сломаться
    вместе с задачей. Сбой записи не глушит исходную ошибку — только
    громко логируется.

    Остановка — ещё и тревога человеку, но только если прогон закрыла
    эта запись: отказ посреди сбора прогон уже закрыл сам и сам же о нём
    сказал (`runs/stopped.py`). И только после фиксации: не записалось —
    прогон остался открытым, и о нём скажет разбор мёртвых."""
    engine = create_async_engine(storage.DSN)
    closed = False
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            runs = RunRepository(session)
            run = await runs.get(run_id)
            stats = {
                **(run.stats or {}),
                REASON_KEY: reason[:500],
                FAILURE_KEY: described(failed)[:500],
            }
            if stop:
                # Журнал списков конкурентов — и у прогона, вставшего до воронки.
                closed = await runs.stop_run(run, stats={**stats, **journal_of(run)})
            else:
                await runs.save_stats(run, stats)
            await session.commit()
    except Exception:
        logger.exception("Прогон %s: причину «%s» записать не удалось", run_id, reason[:120])
    else:
        if closed:
            await tell_stopped(run_id, reason[:500])
    finally:
        await engine.dispose()
