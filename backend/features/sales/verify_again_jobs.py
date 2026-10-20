"""Задача перепроверки (`recheck`): лиды, за которых Hunter «ещё проверял», — когда срок настал.

Ставит её не человек, а очередь — на самый ранний срок в базе (`verify_again.plan`,
`deferred.py`). Тело — та же очистка названных лидов (`cleaning.clean_leads`): правила, DNS,
держатель компании и проверка под месячным потолком.

**Партиями, и каждая — своим коммитом** (ревью 20.10, второй круг: один коммит на всю задачу
терял при сбое оплаченные вердикты, лиды оставались со старым сроком, и повтор задачи платил за
них снова): партия — лиды одного прогона (`BATCH`), под блокировкой строк с пропуском занятых
(`SKIP LOCKED`, две задачи и «Повторить сейчас» у очистки не проверят один адрес дважды);
вердикты и строки расхода коммитятся вместе с исходом лида, и лид, получивший вердикт, задачу
больше не ждёт. Отчёт прогона и запись в журнале — своей транзакцией после партии: их сбой —
строка журнала, вердикты остаются.

- **продажи выключены — задача ничего не делает**: ни Hunter, ни новой задачи (`SALES_OFF`);
- **расход — в отчёт своего прогона**: проверки лидов прогона сбора ложатся в его
  `report.leads.cleaning` (`segment_run.leads_report`), и панель расхода видит их рядом со сметой;
  лиды загрузки базы — без прогона, их расход — в журнале расхода, как у очистки;
- **в журнал действий** — запись «автоматически, по сроку» на каждую партию прогона (без автора,
  как у автосборки): трату сделал не человек, и это видно;
- **следующая задача — только после прохода, который прошёл**: упавшую задачу повторяет очередь
  (повторы с паузой), а свою новую она не ставит — круг «упала — поставила — упала» платил бы за
  каждую попытку. Не прошла и после повторов — строка журнала; сроки лежат в базе, отчёт прогона
  показывает «наступил» и «Повторить сейчас», следующую задачу поставит любой проход.

Отказы — как у задачи очистки (`clean_jobs.py`): негодная настройка проверяльщика — итог «не
выполнена» словами человека, а не три повтора; остальное (база) — исключение, и очередь повторит
задачу: лиды, чей срок уже прошёл, она возьмёт и при повторе.
"""

from __future__ import annotations

import asyncio
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import fields
from datetime import UTC, date, datetime
from typing import Any

import httpx
from rq import get_current_job
from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import sales as sales_cfg
from backend.config import storage
from backend.config.startup_checks import ConfigError, check_storage
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction, Stage
from backend.features.runs.failures import described, is_permanent
from backend.features.sales import cleaning, verify_again
from backend.features.sales.clean_jobs import VERIFIER_NOT_SET, report_of
from backend.features.sales.clean_report import CleaningReport
from backend.features.sales.models import SalesLeadModel
from backend.features.sales.segment_estimate import SALES_OFF
from backend.features.sales.segment_models import SalesSegmentCandidateModel as Candidate
from backend.features.sales.segment_models import SalesSegmentRunModel as Run
from backend.features.sales.segment_run import leads_report
from backend.features.sales.verifier import EmailVerifier, build_verifier
from backend.shared.logs import setup_logging
from backend.shared.queue import remember_job_error

logger = logging.getLogger(__name__)

#: Действие в журнале: перепроверку запустил срок, а не человек.
AUTO = "перепроверка адресов «Hunter ещё проверяет» — автоматически, по сроку"


#: Лидов в партии задачи — как у очистки: сбой теряет не больше её.
BATCH = cleaning.BATCH
#: Партий за задачу — не больше: каждая снимает своих лидов со срока, предел — страховка от круга.
MAX_BATCHES = 50


def due_rows(now: datetime) -> Select[tuple[SalesLeadModel, int]]:
    """Лиды, чей срок настал, с прогоном сбора (у лида не из прогона — пусто: внешнее
    соединение), прогон за прогоном, — под блокировкой строк лидов до конца транзакции; занятые
    другой транзакцией пропускаются (`SKIP LOCKED`)."""
    return (
        select(SalesLeadModel, Candidate.run_id)
        .outerjoin(Candidate, Candidate.lead_id == SalesLeadModel.id)
        .where(verify_again.due(now))
        .order_by(Candidate.run_id.asc().nulls_last(), SalesLeadModel.id)
        .limit(BATCH)
        .with_for_update(of=SalesLeadModel, skip_locked=True)
    )


async def _batch(session: AsyncSession, now: datetime) -> tuple[int | None, list[SalesLeadModel]]:
    """Следующая партия — лиды одного прогона (`None` — лиды не из прогона); пусто — сроков нет.
    Строки других прогонов, попавшие в выборку, освободит коммит партии."""
    rows = (await session.execute(due_rows(now))).tuples().all()
    if not rows:
        return None, []
    first = rows[0][1]
    leads: dict[int, SalesLeadModel] = {}
    for lead, run_id in rows:
        if run_id == first:
            leads.setdefault(lead.id, lead)
    return first, list(leads.values())


async def _to_run(session: AsyncSession, run_id: int, report: CleaningReport) -> None:
    """Проверки партии — в отчёт прогона, как у его очистки, и запись «автоматически, по сроку» —
    своей транзакцией: вердикты уже закоммичены, и сбой здесь их не откатывает."""
    try:
        # Точка сохранения: сбой здесь откатывает только запись отчёта, а не транзакцию задачи.
        async with session.begin_nested():
            run = await session.get(Run, run_id, populate_existing=True)
            if run is None:
                return
            made = {"created": 0, "cleaning": report_of(report)}
            run.report = {**run.report, "leads": leads_report(run.report.get("leads"), made)}
            await AccessRepository(session).record(
                AuditAction.RUN_STARTED,
                author_id=None,
                target=f"sales_segment_run:{run_id}",
                details={"действие": AUTO, "прогон": run_id, "лидов": report.checked},
                stage=Stage.SALES,
            )
        await session.commit()
    except Exception:
        logger.exception(
            "продажи: проверки перепроверки не записались в отчёт прогона — вердикты сохранены",
            extra={"run": run_id, "checked": report.checked, "paid_units": report.paid_units},
        )


async def again_pass(
    session: AsyncSession, verifier: EmailVerifier, *, now: datetime
) -> CleaningReport | None:
    """Лиды, чей срок перепроверки настал, — партиями по прогонам, каждая своим коммитом
    (шапка модуля). `None` — таких нет."""
    reports: list[CleaningReport] = []
    for _ in range(MAX_BATCHES):
        run_id, leads = await _batch(session, now)
        if not leads:
            break
        report = await cleaning.clean_leads(session, leads, verifier, now=now, commit=True)
        await session.commit()
        reports.append(report)
        if run_id is not None:
            await _to_run(session, run_id, report)
    if not reports:
        return None
    total = _sum(reports)
    logger.info(
        "продажи: перепроверка «ещё проверяет» прошла",
        extra={"leads": total.checked, "still_checking": total.still_checking},
    )
    return total


def _sum(reports: Sequence[CleaningReport]) -> CleaningReport:
    """Итог задачи — проходы по прогонам вместе: числа и причины — суммой, сроки — ближайшим,
    остановка — первой."""
    total = CleaningReport(verifier=reports[0].verifier)
    for report in reports:
        for item in fields(CleaningReport):
            mine, theirs = getattr(total, item.name), getattr(report, item.name)
            if isinstance(theirs, Counter):
                mine.update(theirs)
            elif isinstance(theirs, int):
                setattr(total, item.name, mine + theirs)
            elif isinstance(theirs, date):  # и `datetime`: срок — ближайший
                setattr(total, item.name, theirs if mine is None else min(mine, theirs))
            elif item.name == "stopped":
                total.stopped = total.stopped or report.stopped
    return total


async def _plan(factory: async_sessionmaker[Any]) -> None:
    """Следующая задача — по базе, своей сессией: сбой постановки не подменяет исход прохода."""
    try:
        async with factory() as session:
            await verify_again.plan(session, now=datetime.now(UTC))
    except Exception:
        logger.exception("продажи: следующая перепроверка адресов не поставлена")


async def run_again() -> dict[str, Any]:
    """Одна перепроверка: своя сессия и свой клиент HTTP; следующая — по базе, только если проход
    прошёл (шапка модуля). Продажи выключены — ничего: ни проверки, ни новой задачи."""
    if not sales_cfg.ENABLED:
        logger.info("продажи: перепроверка адресов — продажи выключены")
        return {"skipped": SALES_OFF}
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with httpx.AsyncClient() as http, factory() as session:
            found = await again_pass(session, build_verifier(http), now=datetime.now(UTC))
        await _plan(factory)
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
        job = get_current_job()
        if job is not None:
            remember_job_error(job.id, described(exc))
        # Новой задачи упавшая не ставит (шапка модуля): повтор — за очередью, а после последнего —
        # сроки ждут любого прохода, отчёт прогона показывает «наступил».
        log = logger.error if job is None or not job.retries_left else logger.warning
        log("продажи: перепроверка адресов упала", extra={"error": described(exc)})
        raise
