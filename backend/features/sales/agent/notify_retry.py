"""Повтор сообщения о черновике агента продаж проходом по расписанию (решение владельца).

**Что повторяется.** Сообщение в группу продаж, не ушедшее из-за сети, 5xx или 429, — той же
серией, что сообщение о лиде (`telegram_series.py`): до пяти попыток (задача и четыре повтора),
пауза 5, 10, 20, 40 мин, у 429 — не меньше паузы Telegram, после последней — тревога словами.
Постоянный отказ и бот без токена не повторяются (`notify.py`).

**Только о нынешней версии черновика, который ждёт человека.** Решённый черновик и новая
версия («написать заново» — своё сообщение) прежнего сообщения не повторяют: проход их не
берёт, а задача, поставленная раньше, сверяет версию и попытку (`notify.Retry`).

**Проход — тем же кругом, что повторы передачи** (`handoff_jobs.retry_pass`), после их
постановки. Срок взятых сдвигается сразу на `HANDOFF_RETRY_SEC`: следующий круг их не возьмёт,
пока задача не отработала, а потерянную — возьмёт снова.

**Продажи (`SALES_ENABLED`) или агент продаж (`SALES_AGENT_ENABLED`) выключены — повторов
нет:** проход ничего не берёт и сроков не трогает, задача в Telegram не ходит и строку не
меняет. Строки ждут как есть — после включения их берёт проход.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from rq import get_current_job
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import sales as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import Stage
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.runs.failures import described
from backend.features.sales.agent import notify
from backend.features.sales.models import NoticeStatus, SalesDraftNoticeModel
from backend.features.sales.telegram import SalesBot
from backend.shared.alerts import send_alert
from backend.shared.logs import setup_logging
from backend.shared.queue import remember_job_error

logger = logging.getLogger(__name__)

#: Сколько строк проход берёт за круг: черновики ждут человека единицами.
PASS_LIMIT = 50

#: Почему повтора нет: продажи или агент продаж выключены.
SWITCHED_OFF = (
    "продажи (SALES_ENABLED) или агент продаж (SALES_AGENT_ENABLED) выключены — "
    "сообщение ждёт включения"
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _http() -> httpx.AsyncClient:
    """Клиент на одну задачу. Отдельной функцией — её подменяет тест: сети в тестах нет."""
    return httpx.AsyncClient()


def switched_on() -> bool:
    """Повторяет ли проход сообщения о черновиках: включены и продажи, и агент продаж."""
    return cfg.ENABLED and cfg.AGENT_ENABLED


async def due(
    session: AsyncSession, *, now: datetime, limit: int = PASS_LIMIT
) -> list[tuple[int, int]]:
    """Строки журнала, которым пора повторить сообщение, — номер и неудач в серии. Только о
    нынешней версии черновика продаж, который ждёт человека. Срок взятых сдвигается сразу."""
    if not switched_on():
        return []
    taken = (
        await session.execute(
            select(SalesDraftNoticeModel.id, SalesDraftNoticeModel.tries)
            .join(AgentDraftModel, AgentDraftModel.id == SalesDraftNoticeModel.draft_id)
            .join(AgentSettingsModel, AgentSettingsModel.id == AgentDraftModel.settings_id)
            .where(
                SalesDraftNoticeModel.status == NoticeStatus.UNDELIVERED.value,
                SalesDraftNoticeModel.due_at <= now,
                SalesDraftNoticeModel.written_at == AgentDraftModel.updated_at,
                AgentDraftModel.status.in_(notify.WAITING),
                AgentSettingsModel.stage == Stage.SALES,
            )
            .order_by(SalesDraftNoticeModel.due_at)
            .limit(limit)
            .with_for_update(of=SalesDraftNoticeModel, skip_locked=True)
        )
    ).all()
    found = [(int(notice_id), int(tries)) for notice_id, tries in taken]
    if found:
        await session.execute(
            update(SalesDraftNoticeModel)
            .where(SalesDraftNoticeModel.id.in_([notice_id for notice_id, _ in found]))
            .values(due_at=now + timedelta(seconds=cfg.HANDOFF_RETRY_SEC))
        )
    await session.commit()
    return found


async def resend(
    session: AsyncSession,
    notice_id: int,
    tries: int,
    bot: SalesBot,
    *,
    alert: notify.Alert,
    now: notify.Clock = _utcnow,
) -> dict[str, Any]:
    """Повтор сообщения строки журнала — за неудачей `tries` серии. Выключены продажи или
    агент продаж — ни Telegram, ни правки строки."""
    if not switched_on():
        logger.info(
            "продажи: повтор сообщения о черновике ждёт включения", extra={"notice": notice_id}
        )
        return {"notice": notice_id, "skipped": SWITCHED_OFF}
    row = await session.get(SalesDraftNoticeModel, notice_id)
    if row is None:
        return {"notice": notice_id, "skipped": "строки журнала нет — повторять нечего"}
    retry = notify.Retry(version=row.written_at, tries=tries)
    noticed = await notify.notify(session, row.draft_id, bot, alert=alert, retry=retry, now=now)
    return {"notice": notice_id, **noticed.report()}


async def run_resend(notice_id: int, tries: int) -> dict[str, Any]:
    """Один повтор: своя сессия и свой клиент httpx на задачу."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with _http() as http, factory() as session:
            return await resend(session, notice_id, tries, SalesBot(http), alert=send_alert)
    finally:
        await engine.dispose()


def resend_draft_notice(notice_id: int, tries: int) -> dict[str, Any]:
    """Задача очереди: повтор сообщения о черновике агента продаж в группу продаж.

    Недоставка — исход задачи (срок следующего повтора или тревога), а не падение. Падает
    задача на базе — её повторит очередь, а причина ляжет рядом с задачей."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(run_resend(notice_id, tries))
    except Exception as exc:
        job = get_current_job()
        if job is not None:
            remember_job_error(job.id, described(exc))
        raise
