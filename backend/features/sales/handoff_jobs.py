"""Задачи продаж: передача лида (`hand_off_lead`) и проход повторов (`retry_pass`).

Тонкая обёртка над ядром `handoff.py`, как `workers/jobs.py` над ядрами доноров:
порядок передачи и её исходы живут там, здесь — сессия, клиент httpx и выбор Kommo.
Живёт в модуле продаж, а не в `workers/`: очередь зовёт задачу путём строкой
(`handoff.HANDOFF_JOB`), а общий код процесса разбора знает о продажах одну строку.

**Kommo для задачи — `connected_kommo`.** `fixture` — Kommo не подключён: передача
идёт тем же путём со ссылкой на диалог (A6). `live` с негодной настройкой — не
падение задачи, а клиент-отказ: передача кончается `failed` словами настройки,
тревога уходит владельцу, телемаркетолог всё равно получает ссылку на диалог.

**Отказы.** Постоянный (`permanent`: нет передачи, нет лида) — итог задачи, а не
падение: повтор очереди не поможет. Остальное (база, занятая передача) — исключение,
и очередь повторит задачу по `shared/queue.RETRY_INTERVALS`; причина запоминается
рядом с задачей, как в `workers/jobs.py`.

**Проход повторов** живёт третьим циклом процесса разбора мёртвых
(`workers/reaper.py`): новый контейнер ради одного запроса раз в пять минут — ещё
один процесс, который однажды не поднимется.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

import httpx
from redis.exceptions import RedisError
from rq import get_current_job
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import ConfigError, check_storage
from backend.features.runs.failures import described, is_permanent
from backend.features.sales.handoff import Deps, due, enqueue_handoff, process
from backend.features.sales.kommo import (
    FIXTURE,
    CreatedLead,
    KommoClient,
    KommoContact,
    KommoRefusedError,
    NewLead,
    build_kommo,
)
from backend.features.sales.telegram import SalesBot
from backend.shared.alerts import send_alert
from backend.shared.logs import setup_logging
from backend.shared.queue import remember_job_error

logger = logging.getLogger(__name__)


def _http() -> httpx.AsyncClient:
    """Клиент на одну задачу. Отдельной функцией — её подменяет тест: сети в тестах нет."""
    return httpx.AsyncClient()


class _Misconfigured:
    """Kommo `live` с негодной настройкой: каждый вызов — отказ словами настройки."""

    name = "misconfigured"

    def __init__(self, reason: str) -> None:
        self._reason = reason

    def _refuse(self, what: str) -> KommoRefusedError:
        return KommoRefusedError(f"{what}: клиент Kommo не собран — {self._reason}")

    async def find_contact(self, email: str) -> KommoContact | None:
        raise self._refuse(f"поиск контакта ({len(email)} знаков адреса)")

    async def create_complex_lead(self, lead: NewLead) -> CreatedLead:
        raise self._refuse(f"сделка «{lead.title}»")

    async def add_note(self, lead_id: int, text: str) -> int:
        raise self._refuse(f"примечание к сделке №{lead_id} ({len(text)} знаков)")

    def lead_url(self, lead_id: int) -> str:  # noqa: ARG002 — сделки не будет, ссылки нет
        return ""


def connected_kommo(http: httpx.AsyncClient) -> KommoClient | None:
    """Kommo задачи: `None` — не подключён (fixture), отказ настройки — клиент-отказ."""
    try:
        client = build_kommo(http)
    except ConfigError as exc:
        logger.error(  # noqa: TRY400 — трассировка сборки ничего не добавит к словам
            "продажи: клиент Kommo не собран — передача кончится отказом",
            extra={"error": str(exc)},
        )
        return _Misconfigured(str(exc))
    return None if client.name == FIXTURE else client


async def run_hand_off(handoff_id: int) -> dict[str, object]:
    """Одна передача: своя сессия и свой клиент httpx на задачу."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with _http() as http, factory() as session:
            deps = Deps(kommo=connected_kommo(http), bot=SalesBot(http), alert=send_alert)
            return await process(session, handoff_id, deps)
    finally:
        await engine.dispose()


def hand_off_lead(handoff_id: int) -> dict[str, Any]:
    """Задача очереди: передать лида телемаркетологу (Kommo, затем Telegram)."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(run_hand_off(handoff_id))
    except Exception as exc:
        if not is_permanent(exc):
            job = get_current_job()
            if job is not None:
                remember_job_error(job.id, described(exc))
            raise
        logger.warning(
            "продажи: передача лида не выполнена",
            extra={"handoff_id": handoff_id, "error": described(exc)},
        )
        return {"error": described(exc), "permanent": True}


async def retry_pass() -> None:
    """Один круг повторов: передачи, которым пора, — в очередь. Очередь не ответила —
    строка в журнал: срок уже сдвинут, передачу возьмёт следующий круг через срок.
    Kommo и Telegram зовёт задача очереди, а не разбор: их сбой — повтор позже, а не мёртвый сервис.
    """
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            ids = await due(session, now=datetime.now(UTC))
    finally:
        await engine.dispose()
    for handoff_id in ids:
        try:
            enqueue_handoff(handoff_id)
        except RedisError as exc:
            logger.error(  # noqa: TRY400 — трассировка Redis ничего не добавит к причине
                "продажи: повтор передачи лида не поставлен — очередь недоступна",
                extra={"handoff_id": handoff_id, "error": str(exc)},
            )
    if ids:
        logger.info("продажи: повтор передачи лидов", extra={"handoffs": ids})
