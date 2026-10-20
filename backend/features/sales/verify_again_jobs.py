"""Задача перепроверки (`recheck`): лиды, за которых Hunter «ещё проверял», — когда срок настал.

Ставит её не человек, а очередь — на самый ранний срок в базе (`verify_again.plan`,
`deferred.py`). Тело — та же очистка названных лидов (`cleaning.clean_leads`): правила, DNS,
держатель компании и проверка под месячным потолком; партии — одной транзакцией задачи, как у
лидов прогона сбора. В конце задача ставит следующую по базе: срок мог появиться у лидов, которых
проход отложил снова, или у тех, кого отложили другие проходы, пока эта шла.

Отказы — как у задачи очистки (`clean_jobs.py`): негодная настройка проверяльщика — итог «не
выполнена» словами человека, а не три повтора; остальное (база) — исключение, и очередь повторит
задачу: лиды, чей срок уже прошёл, она возьмёт и при повторе.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from rq import get_current_job
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import ConfigError, check_storage
from backend.features.runs.failures import described, is_permanent
from backend.features.sales import cleaning, verify_again
from backend.features.sales.clean_jobs import VERIFIER_NOT_SET, report_of
from backend.features.sales.clean_report import CleaningReport
from backend.features.sales.models import SalesLeadModel
from backend.features.sales.verifier import EmailVerifier, build_verifier
from backend.shared.logs import setup_logging
from backend.shared.queue import remember_job_error

logger = logging.getLogger(__name__)


async def again_pass(
    session: AsyncSession, verifier: EmailVerifier, *, now: datetime
) -> CleaningReport | None:
    """Лиды, чей срок перепроверки настал, — проходом очистки; коммит здесь. `None` — таких нет."""
    leads = list(
        (
            await session.scalars(
                select(SalesLeadModel).where(verify_again.due(now)).order_by(SalesLeadModel.id)
            )
        ).all()
    )
    if not leads:
        return None
    report = await cleaning.clean_leads(session, leads, verifier, now=now)
    await session.commit()
    logger.info(
        "продажи: перепроверка «ещё проверяет» прошла",
        extra={"leads": len(leads), "still_checking": report.still_checking},
    )
    return report


async def run_again() -> dict[str, Any]:
    """Одна перепроверка: своя сессия и свой клиент HTTP; следующая — по базе."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with httpx.AsyncClient() as http, factory() as session:
            now = datetime.now(UTC)
            found = await again_pass(session, build_verifier(http), now=now)
            await verify_again.plan(session, now=now)
    finally:
        await engine.dispose()
    return {"checked": 0} if found is None else report_of(found)


def recheck() -> dict[str, Any]:
    """Задача очереди: перепроверить лидов, чей срок «ещё проверяет» настал. Писем не пишет."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(run_again())
    except Exception as exc:
        if is_permanent(exc):
            logger.warning(
                "продажи: перепроверка адресов не выполнена", extra={"error": described(exc)}
            )
            said = VERIFIER_NOT_SET if isinstance(exc, ConfigError) else described(exc)
            return {"error": said, "permanent": True}
        if (job := get_current_job()) is not None:
            remember_job_error(job.id, described(exc))
        raise
