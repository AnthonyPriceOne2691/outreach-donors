"""Задача очистки лидов продаж (`clean_sales_leads`): экран ставит, воркер продаж чистит.

Путь тот же, что у `outreach sales-clean --hypothesis`: проверяльщик по настройке
(`verifier.build_verifier`: `fixture` — без сети и денег, `live` — Hunter общим ключом)
и проход `cleaning.clean` — лиды `new` гипотезы партиями, коммит и строка расхода за
платные проверки на каждую партию. Здесь — своя сессия, свой клиент HTTP и итог для
строки задачи на экране. Очередь зовёт задачу путём строкой (`CLEAN_JOB`), как сборку
очереди писем (`queue_jobs.py`): общий код очереди о продажах знает одну строку — имя
задачи словами (`ops/job_outcome.KINDS`).

**Сколько ждёт** (`waiting`) — тем же условием, каким лиды берёт проход: состояние `new`
у гипотезы. Это число называет окно подтверждения расхода — каждый лид стоит не больше
одной платной проверки, а дубли, стоп-листы и домены без почты отсеиваются до неё.

**Отказы.** Негодная настройка проверяльщика (`live` без ключа, незнакомое имя) — итог
«не выполнена», а не три повтора: повтор её не исправит. Остальное (база) — исключение,
и очередь повторит задачу; повтор не платит дважды — очищенный лид уже не `new`, а
партии закоммичены. Квота и закрытая учётка Hunter — не отказ задачи, а итог прохода
(`stopped`): непроверенные лиды остались `new`, итог называет причину.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx
from rq import get_current_job
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.runs.failures import described, is_permanent
from backend.features.sales import cleaning
from backend.features.sales.cleaning import CleaningReport
from backend.features.sales.intake import UnknownHypothesisError
from backend.features.sales.models import LeadStatus, SalesHypothesisModel, SalesLeadModel
from backend.features.sales.verifier import build_verifier
from backend.shared.logs import setup_logging
from backend.shared.queue import remember_job_error

logger = logging.getLogger(__name__)

#: Путь задачи строкой: очередь импортирует её в воркере продаж.
CLEAN_JOB = "backend.features.sales.clean_jobs.clean_sales_leads"


@dataclass(frozen=True, slots=True)
class Waiting:
    """Что возьмёт очистка гипотезы: имя гипотезы и сколько её лидов ждут."""

    name: str
    count: int


async def waiting(session: AsyncSession, hypothesis_id: int) -> Waiting:
    """Лиды гипотезы, которых возьмёт очистка, — состояние `new`, как у прохода.
    Гипотезы нет — отказ словами (404)."""
    hypothesis = await session.get(SalesHypothesisModel, hypothesis_id)
    if hypothesis is None:
        raise UnknownHypothesisError(f"гипотезы №{hypothesis_id} нет — обновите список гипотез")
    count = await session.scalar(
        select(func.count())
        .select_from(SalesLeadModel)
        .where(
            SalesLeadModel.hypothesis_id == hypothesis_id,
            SalesLeadModel.status == LeadStatus.NEW,
        )
    )
    return Waiting(hypothesis.name, int(count or 0))


async def clean_hypothesis(
    session: AsyncSession, http: httpx.AsyncClient, hypothesis_id: int
) -> CleaningReport:
    """Очистка лидов гипотезы теми же двумя шагами, что у консоли: проверяльщик по
    настройке — до первого лида, затем проход партиями."""
    verifier = build_verifier(http)
    return await cleaning.clean(session, verifier, hypothesis_id=hypothesis_id)


def report_of(found: CleaningReport) -> dict[str, Any]:
    """Итог для строки задачи: отказы — кодами причин, слова к ним у экрана — те же,
    что у фильтра лидов."""
    return {
        "checked": found.checked,
        "ready": found.ready,
        "rejected": dict(found.rejected),
        "unverified": found.unverified,
        "mx_unknown": found.mx_unknown,
        "verified": found.verified,
        "paid_units": found.paid_units,
        "verifier": found.verifier,
        "stopped": found.stopped,
    }


async def run_clean(hypothesis_id: int) -> dict[str, Any]:
    """Одна очистка: своя сессия и свой клиент HTTP на задачу."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with httpx.AsyncClient() as http, factory() as session:
            return report_of(await clean_hypothesis(session, http, hypothesis_id))
    finally:
        await engine.dispose()


def clean_sales_leads(hypothesis_id: int) -> dict[str, Any]:
    """Задача очереди: очистить лидов `new` гипотезы. Писем не пишет и не отправляет."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(run_clean(hypothesis_id))
    except Exception as exc:
        if is_permanent(exc):
            logger.warning(
                "продажи: очистка лидов не выполнена",
                extra={"hypothesis_id": hypothesis_id, "error": described(exc)},
            )
            return {"error": described(exc), "permanent": True}
        if (job := get_current_job()) is not None:
            remember_job_error(job.id, described(exc))
        raise
