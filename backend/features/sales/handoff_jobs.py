"""Задачи продаж: передача лида (`hand_off_lead`), повтор сообщения о лиде
(`resend_lead_message`) и проход повторов (`retry_pass`).

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
один процесс, который однажды не поднимется. Он ставит две задачи: передачу, где ждёт
Kommo (`due`), и повтор сообщения, где ждёт только Telegram (`message_due`). Продажи
выключены (`SALES_ENABLED`) — не ставит ни одной, а обе задачи никуда не ходят. Тем же
кругом, после них, — повтор сообщения о черновике агента продаж (`agent/notify_retry.py`).
Сторож бота продаж — тем же кругом, после постановки повторов: сводная тревога «без токена»
в общую ленту (`agent/notify.watch_token`); его сбой повторов не держит.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

import httpx
from redis.exceptions import RedisError
from rq import get_current_job
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import ConfigError, check_storage
from backend.features.runs.failures import described, is_permanent
from backend.features.sales.agent import notify_retry
from backend.features.sales.agent.notify import queue_resend, watch_token
from backend.features.sales.handoff import (
    Deps,
    due,
    enqueue_handoff,
    enqueue_message,
    message_due,
    process,
)
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

#: Что пишет проход, когда очередь не приняла задачу: срок уже сдвинут, возьмёт следующий круг.
_REFUSED_HANDOFF = "продажи: повтор передачи лида не поставлен — очередь недоступна"
_REFUSED_MESSAGE = "продажи: повтор сообщения о лиде не поставлен — очередь недоступна"
_REFUSED_NOTICE = "продажи: повтор сообщения о черновике не поставлен — очередь недоступна"


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


async def run_hand_off(handoff_id: int, *, kommo: bool = True) -> dict[str, object]:
    """Одна передача: своя сессия и свой клиент httpx на задачу. `kommo=False` — только
    сообщение в Telegram (`handoff.process`)."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with _http() as http, factory() as session:
            deps = Deps(kommo=connected_kommo(http), bot=SalesBot(http), alert=send_alert)
            return await process(session, handoff_id, deps, kommo=kommo)
    finally:
        await engine.dispose()


def hand_off_lead(handoff_id: int) -> dict[str, Any]:
    """Задача очереди: передать лида телемаркетологу (Kommo, затем Telegram)."""
    return _job(handoff_id, lambda: run_hand_off(handoff_id))


def resend_lead_message(handoff_id: int) -> dict[str, Any]:
    """Задача очереди: повторить сообщение о лиде в Telegram, которое не ушло. Kommo не трогает."""
    return _job(handoff_id, lambda: run_hand_off(handoff_id, kommo=False))


def _job(
    handoff_id: int, work: Callable[[], Coroutine[Any, Any, dict[str, Any]]]
) -> dict[str, Any]:
    """Общее у задач передачи: журнал, проверка базы, постоянный отказ — итог, прочее — повтор."""
    setup_logging()
    check_storage()
    try:
        return asyncio.run(work())
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
    """Один круг повторов: передачи, которым пора, — в очередь; где ждёт только сообщение
    в Telegram — его повтор; затем повторы сообщений о черновиках агента продаж. Очередь не
    ответила — строка в журнал: срок уже сдвинут, передачу возьмёт следующий круг через срок.
    Kommo и Telegram зовёт задача очереди, а не разбор: их сбой — повтор позже, а не мёртвый
    сервис. Сторож бота продаж — после очереди: его сбой повторов не держит.
    """
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            now = datetime.now(UTC)
            ids = await due(session, now=now)
            messages = await message_due(session, now=now)
            notices = await notify_retry.due(session, now=now)
        _queue_again(ids, messages, notices)
        async with factory() as session:
            await watch_token(session)
    finally:
        await engine.dispose()


def _queue_again(ids: list[int], messages: list[int], notices: list[tuple[int, int]]) -> None:
    """Взятое проходом — в очередь продаж: передачи, повторы сообщения о лиде, затем повторы
    сообщения о черновике. Очередь не ответила — строка в журнал, срок уже сдвинут."""
    for handoff_id in ids:
        _put(enqueue_handoff, (handoff_id,), _REFUSED_HANDOFF, "handoff_id")
    for handoff_id in messages:
        _put(enqueue_message, (handoff_id,), _REFUSED_MESSAGE, "handoff_id")
    for notice in notices:
        _put(queue_resend, notice, _REFUSED_NOTICE, "notice_id")
    if any((ids, messages, notices)):
        logger.info(
            "продажи: повтор передачи лидов",
            extra={"handoffs": ids, "messages": messages, "notices": notices},
        )


def _put(enqueue: Callable[..., object], args: tuple[int, ...], refused: str, key: str) -> None:
    """Поставить задачу; очередь не ответила — строка `refused` в журнал с номером под `key`,
    срок уже сдвинут."""
    try:
        enqueue(*args)
    except RedisError as exc:
        logger.error(  # noqa: TRY400 — трассировка Redis ничего не добавит к причине
            refused, extra={key: args[0], "error": str(exc)}
        )
