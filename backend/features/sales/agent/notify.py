"""Сообщение о черновике агента продаж в группу продаж в Telegram (срез 3.5).

**Когда.** Шов зовёт крючок этапа (`AgentStage.on_draft`), когда черновик записан и
ждёт человека: `drafted` — готов, `escalated` — отдан человеку и как есть не уйдёт.
Пропуск («ответ не нужен») шов не объявляет (`drafting.announce`).

**Задачей очереди, а не в запросе.** Крючок только ставит задачу (`queue_notice`):
«написать заново» зовёт его из запроса, а Telegram отвечает секундами и бывает
недоступен. Очередь — продаж (`sales`, свой воркер `worker-sales`): в общей весть
о черновике ждала бы часовой прогон доноров, как ждал бы ответ лида. Очередь
недоступна — строка в журнал сервиса: черновик цел и виден в переписке.

**Что в сообщении** — только то, что уже лежит в базе: кому и о чём (адрес и домен
собеседника, тема, ситуация и его вопрос), ход и вердикт судьи, ссылка на переписку
в сервисе (`SALES_APP_URL`). Текста черновика нет: его читают и решают в переписке.

**Бот продаж, три попытки** (`features/sales/telegram.py`, срез 5.3). Не доставлено —
строка «не доставлено» в журнале отправки (`sales_draft_notices`) и тревога
эксплуатации другим ботом (`shared/alerts.send_alert`), как у передачи лида.
Черновик от исхода не зависит: он уже в базе, сообщение о нём — только весть.

**Бот без токена — одна сводная тревога, а не тревога на каждый черновик** (решение
владельца по ревью стыков): без токена не уходит ни одно сообщение, и чат эксплуатации
утонул бы в одинаковых строках. Тревога — в общей ленте (`ops/alarm_feed.py`) проходом
продаж процесса разбора (`handoff_jobs.retry_pass`): одна, с числом черновиков, которые ждут
человека без сообщения; «прошло» — когда токен задан (`watch_token`).

**Одна версия — одно сообщение.** Строка журнала — версия черновика (`written_at`):
повтор задачи о той же версии второй раз не пишет, «написать заново» — новая версия
и новое сообщение. Решённый черновик не объявляется: человек им уже занялся.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from redis.exceptions import RedisError
from rq import get_current_job
from sqlalchemy import exists, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import sales as cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import DraftStatus, Stage
from backend.features.core.models.agent import AgentDraftModel, AgentSettingsModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import ReplyModel, ThreadModel
from backend.features.ops.alarm_feed import Feed
from backend.features.ops.alarms import Alarm
from backend.features.runs.failures import described
from backend.features.sales.models import NoticeStatus, SalesDraftNoticeModel
from backend.features.sales.telegram import SalesBot, TelegramError
from backend.shared.alerts import send_alert
from backend.shared.logs import setup_logging
from backend.shared.queue import remember_job_error, sales_queue, with_retries

logger = logging.getLogger(__name__)

#: Путь задачи строкой: очередь импортирует её в воркере (`shared/queue.py`).
NOTICE_JOB = "backend.features.sales.agent.notify.notify_draft"

#: О каких черновиках сообщаем: оба ждут человека.
WAITING = (DraftStatus.DRAFTED, DraftStatus.ESCALATED)

#: Знаков вопроса собеседника и причины в сообщении: дальше — читать в переписке.
MAX_QUOTE = 300

#: Вердикт судьи словами — по последней попытке петли правки (`meta["attempts"]`).
_SAID = {"allow": "пропустил", "block": "не пропустил", "escalate": "отдал человеку"}

Alert = Callable[[str], Awaitable[object]]

#: Сводная тревога «бот продаж без токена» — код в ленте тревог.
BOT_ALARM = "sales-bot-unset"
#: Что о ней уже сказано человеку — лента процесса разбора, по смене состояния.
BOT_FEED = Feed()


@dataclass(frozen=True, slots=True)
class Noticed:
    """Чем кончилось: исход строки журнала — или почему сообщать не о чем."""

    draft_id: int
    status: NoticeStatus | None = None
    skipped: str | None = None
    error: str | None = None

    def report(self) -> dict[str, Any]:
        """Итог задачи очереди — его показывает экран задач."""
        return {
            "draft": self.draft_id,
            "status": None if self.status is None else self.status.value,
            "skipped": self.skipped,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class _Found:
    draft: AgentDraftModel
    stage: Stage
    reply: ReplyModel
    thread_id: int
    host: str


def queue_notice(draft_id: int) -> None:
    """Поставить сообщение о черновике в очередь. Очередь недоступна — строка в журнал:
    черновик цел и виден в переписке, а крючок шва не роняет того, кто его позвал."""
    try:
        sales_queue().enqueue(NOTICE_JOB, draft_id, **with_retries())
    except RedisError as exc:
        logger.error(  # noqa: TRY400 — трассировка Redis ничего не добавит к причине
            "продажи: сообщение о черновике не поставлено — очередь недоступна; черновик цел",
            extra={"draft_id": draft_id, "error": str(exc)},
        )


async def notify(session: AsyncSession, draft_id: int, bot: SalesBot, *, alert: Alert) -> Noticed:
    """Сообщить группе продаж о черновике — одной строкой журнала на версию черновика.

    Окно двойной отправки принято владельцем: Telegram принял, а журнал не записался — повтор
    задачи пошлёт сообщение ещё раз. Без токена бота — без своей тревоги: сводная (`watch_token`).
    """
    found = await _found(session, draft_id)
    if found is None:
        return Noticed(draft_id, skipped="черновика нет — сообщать не о чем")
    why = _silent(found)
    if why is None and await _sent_before(session, found):
        why = "об этой версии черновика группе уже сообщено"
    if why is not None:
        return Noticed(draft_id, skipped=why)
    text = message(found)
    error = await _deliver(bot, draft_id, text)
    status = NoticeStatus.SENT if error is None else NoticeStatus.UNDELIVERED
    await _journal(session, found, status, text, error)
    await session.commit()
    if error is not None and cfg.TELEGRAM_BOT_TOKEN:
        await alert(
            f"продажи: сообщение о черновике №{draft_id} не доставлено в группу продаж — "
            f"{error}. Черновик цел: {thread_link(found.thread_id)}"
        )
    return Noticed(draft_id, status=status, error=error)


def message(found: _Found) -> str:
    """Сообщение группе: кому и о чём, ход и судья, ссылка на переписку."""
    draft = found.draft
    meta: Mapping[str, Any] = draft.meta or {}
    head = (
        "Продажи: черновик ответа готов — проверьте и отправьте"
        if draft.status is DraftStatus.DRAFTED
        else "Продажи: ответ ждёт человека — как есть агент его не отправит"
    )
    return "\n".join(
        (
            head,
            f"Кому: {found.reply.from_email or 'адрес не известен'} ({found.host})",
            f"О чём: {found.reply.subject or 'без темы'}",
            f"Ситуация: {_situation(meta)}",
            f"Ход: {_move(meta)}",
            f"Судья: {_verdict(draft, meta)}",
            f"Переписка: {thread_link(found.thread_id)}",
        )
    )


def thread_link(thread_id: int) -> str:
    """Ссылка на переписку в сервисе; без адреса сервиса — номер и имя настройки."""
    if cfg.APP_URL:
        return f"{cfg.APP_URL}/threads/{thread_id}"
    return f"переписка №{thread_id} (SALES_APP_URL не задан)"


def _cut(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= MAX_QUOTE else text[: MAX_QUOTE - 1] + "…"


def _situation(meta: Mapping[str, Any]) -> str:
    label = meta.get("situation")
    if not label:
        return "не разобрана"
    question = meta.get("question")
    return f"{label} · вопрос: «{_cut(str(question))}»" if question else str(label)


def _move(meta: Mapping[str, Any]) -> str:
    turn = meta.get("turn")
    where = f" · письмо собеседника №{turn}" if turn else ""
    return f"{meta.get('move') or 'не выбран'}{where}"


def _verdict(draft: AgentDraftModel, meta: Mapping[str, Any]) -> str:
    """Вердикт судьи по последней попытке; отданному человеку — и почему."""
    raw = meta.get("attempts")
    attempts = [one for one in raw if isinstance(one, Mapping)] if isinstance(raw, list) else []
    said = _SAID.get(str(attempts[-1].get("verdict")), "не звался") if attempts else "не звался"
    rewrites = f", правок: {len(attempts) - 1}" if len(attempts) > 1 else ""
    if draft.status is DraftStatus.DRAFTED:
        return f"{said}{rewrites}"
    return f"{said}{rewrites} — человеку: {_cut(draft.reason or 'причина не названа')}"


def _silent(found: _Found) -> str | None:
    """Почему о черновике не сообщаем. `None` — сообщаем."""
    if found.stage is not Stage.SALES:
        return f"черновик этапа «{found.stage.value}» — группа продаж о нём не знает"
    if found.draft.status not in WAITING:
        return f"черновик в состоянии «{found.draft.status.value}» — человека он не ждёт"
    return None


async def _found(session: AsyncSession, draft_id: int) -> _Found | None:
    row = (
        await session.execute(
            select(
                AgentDraftModel,
                AgentSettingsModel.stage,
                ReplyModel,
                ThreadModel.id,
                DomainModel.host,
            )
            .join(AgentSettingsModel, AgentSettingsModel.id == AgentDraftModel.settings_id)
            .join(ReplyModel, ReplyModel.id == AgentDraftModel.reply_id)
            .join(ThreadModel, ThreadModel.id == ReplyModel.thread_id)
            .join(DomainModel, DomainModel.id == ThreadModel.domain_id)
            .where(AgentDraftModel.id == draft_id)
        )
    ).first()
    if row is None:
        return None
    draft, stage, reply, thread_id, host = row
    return _Found(draft=draft, stage=stage, reply=reply, thread_id=thread_id, host=host)


async def _sent_before(session: AsyncSession, found: _Found) -> bool:
    sent = await session.scalar(
        select(SalesDraftNoticeModel.id).where(
            SalesDraftNoticeModel.draft_id == found.draft.id,
            SalesDraftNoticeModel.written_at == found.draft.updated_at,
            SalesDraftNoticeModel.status == NoticeStatus.SENT.value,
        )
    )
    return sent is not None


async def _deliver(bot: SalesBot, draft_id: int, text: str) -> str | None:
    """Отправить в группу продаж. `None` — доставлено, иначе — почему нет, словами."""
    if not cfg.TELEGRAM_GROUP_CHAT_ID:
        logger.warning("продажи: чат группы продаж не задан", extra={"draft_id": draft_id})
        return (
            "чат группы не задан — заполнить SALES_TELEGRAM_GROUP_CHAT_ID (номер печатает "
            "`outreach sales-telegram-chat-id`)"
        )
    try:
        await bot.send(cfg.TELEGRAM_GROUP_CHAT_ID, text)
    except TelegramError as exc:
        logger.warning(
            "продажи: сообщение о черновике не доставлено",
            extra={"draft_id": draft_id, "error": str(exc)},
        )
        return str(exc)
    return None


async def _journal(
    session: AsyncSession, found: _Found, status: NoticeStatus, text: str, error: str | None
) -> None:
    """Строка журнала отправки — одна на версию: повтор «не доставлено» её переписывает."""
    values = {"status": status.value, "text": text, "error": error}
    await session.execute(
        insert(SalesDraftNoticeModel)
        .values(draft_id=found.draft.id, written_at=found.draft.updated_at, **values)
        .on_conflict_do_update(
            constraint="uq_sales_draft_notices_version",
            set_={**values, "updated_at": datetime.now(UTC)},
        )
    )


async def token_alarm(session: AsyncSession, *, keep: bool = False) -> Alarm | None:
    """Бот продаж без токена — тревога с числом черновиков, которые ждут человека без сообщения.

    Токен задан — тревоги нет. Без токена и без ждущих черновиков — нет, если о ней ещё не
    сказано (`keep`): сказанная держится, пока токен не задан, а не пока ждут черновики."""
    if cfg.TELEGRAM_BOT_TOKEN:
        return None
    waiting = await unannounced(session)
    if not waiting and not keep:
        return None
    return Alarm(
        code=BOT_ALARM,
        title="Бот продаж без токена",
        detail=(
            f"Черновиков агента продаж ждут человека без сообщения в группе продаж: {waiting}. "
            "Задать SALES_TELEGRAM_BOT_TOKEN — о новых черновиках сообщения пойдут, прежние "
            "видны в переписке"
        ),
    )


async def watch_token(session: AsyncSession, feed: Feed | None = None) -> Alarm | None:
    """Проход сторожа бота продаж: тревога «без токена» — в ленту, по смене состояния."""
    told = BOT_FEED if feed is None else feed
    found = await token_alarm(session, keep=BOT_ALARM in told.told)
    await told.tell([found] if found is not None else [])
    return found


async def unannounced(session: AsyncSession) -> int:
    """Черновики продаж, которые ждут человека, а о нынешней их версии группе не сообщено."""
    announced = exists().where(
        SalesDraftNoticeModel.draft_id == AgentDraftModel.id,
        SalesDraftNoticeModel.written_at == AgentDraftModel.updated_at,
        SalesDraftNoticeModel.status == NoticeStatus.SENT.value,
    )
    found = await session.scalar(
        select(func.count(AgentDraftModel.id))
        .join(AgentSettingsModel, AgentSettingsModel.id == AgentDraftModel.settings_id)
        .where(
            AgentSettingsModel.stage == Stage.SALES,
            AgentDraftModel.status.in_(WAITING),
            ~announced,
        )
    )
    return int(found or 0)


def _http() -> httpx.AsyncClient:
    """Клиент на одну задачу. Отдельной функцией — её подменяет тест: сети в тестах нет."""
    return httpx.AsyncClient()


async def run_notice(draft_id: int) -> dict[str, Any]:
    """Одно сообщение: своя сессия и свой клиент httpx на задачу."""
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with _http() as http, factory() as session:
            noticed = await notify(session, draft_id, SalesBot(http), alert=send_alert)
    finally:
        await engine.dispose()
    return noticed.report()


def notify_draft(draft_id: int) -> dict[str, Any]:
    """Задача очереди: сообщение о черновике агента продаж в группу продаж.

    Недоставка в Telegram — исход задачи («не доставлено» и тревога), а не падение:
    три попытки бот уже сделал, а повтор очереди слал бы тревогу заново. Падает
    задача на базе — её повторит очередь, а причина ляжет рядом с задачей.
    """
    setup_logging()
    check_storage()
    try:
        return asyncio.run(run_notice(draft_id))
    except Exception as exc:
        job = get_current_job()
        if job is not None:
            remember_job_error(job.id, described(exc))
        raise
