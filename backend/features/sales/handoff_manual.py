"""«Передать телемаркетологу» — решение человека у ответа продаж, который ждёт человека.

Ответ, который модель не разобрала уверенно, ждёт человека (`reply_routes`: ниже порога — человек),
а передачу заводит только вид «хочет говорить» (`reply_handoff`). Кнопка — тот же путь:
`handoff.start` — одна передача на диалог, задача Kommo и Telegram своей очередью. **Решение — по
диалогу, а не по ответу:** передаётся лид диалога, и телемаркетолог получает последнее письмо лида
(`handoff_card.latest_reply`), какой бы ответ ни был открыт на экране.

**Ответы диалога, которые ждут человека, снимаются с ожидания словами решения** — тем же снимком,
по которому их ждут (`outcome.sales_review`): «передан телемаркетологу вручную: <кто>», — и полями
решения человека (`reviewed_by`, `reviewed_at`): задача разбора, пришедшая позже, такой ответ не
берёт («решён человеком», `replies._not_ours`) и в ожидание его не вернёт. Снимок ложится до передачи
и уходит в базу её коммитом, вместе со строкой передачи, как у `reply_handoff.pass_on`: лид у
телемаркетолога, а ответ ждёт человека — так не бывает. Кто передал — в журнале действий записью
передачи (`handoff.start`, `author_id`).

**Отказы — словами и до записи**: диалога нет; диалог не продаж; лид уже передан — повтор ничего не
меняет и второй передачи не заводит; продажи выключены; в диалоге нет ответа человека; у диалога нет
лида — словами `handoff.lead_of`.

**Задача — в очередь из потока**, после коммита: `start` зовёт очередь синхронно, а маршрут —
в цикле событий, и лежащий Redis держал бы его секундами. Не встала — не отказ: строка
закоммичена, срок прохода стоит (`handoff.start`), её возьмёт проход по расписанию.

**Состояние передачи словами** (`kommo_said`, `telegram_said`) — для строки «передан
телемаркетологу …» на экране диалога: задача асинхронная, и что с Kommo и Telegram сейчас, знает
строка `sales_handoffs`. Сбой — словами строки передачи, без ключей (`handoff_console.clean`).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.features.core.domain import ReplyKind, Stage
from backend.features.core.models.outreach import ReplyModel
from backend.features.outreach import stage_of
from backend.features.outreach.threads import review_of
from backend.features.runs.failures import described
from backend.features.sales import handoff
from backend.features.sales.handoff_console import clean
from backend.features.sales.models import HandoffKommo, HandoffTelegram, SalesHandoffModel

logger = logging.getLogger(__name__)

#: Чем ответ снят с ожидания — словами решения; дальше — кто передал.
MANUAL = "передан телемаркетологу вручную"

Enqueue = Callable[[int], object]


class ManualHandoffError(RuntimeError):
    """Передать лида нельзя — словами, почему и что делать."""


class UnknownThreadError(ManualHandoffError):
    """Диалога с таким номером нет."""


class HandoffRefusedError(ManualHandoffError):
    """Диалог есть, но передать его лида нельзя или уже незачем."""


@dataclass(frozen=True, slots=True)
class HandedOver:
    """Чем кончилось нажатие: передача, встала ли задача сейчас и какие ответы сняты с ожидания."""

    row: SalesHandoffModel
    queued: bool
    released: tuple[int, ...]


async def hand_over(
    session: AsyncSession,
    thread_id: int,
    *,
    by: str,
    author_id: int | None,
    enqueue: Enqueue = handoff.enqueue_handoff,
    now: datetime | None = None,
) -> HandedOver:
    """Передать лида диалога телемаркетологу и снять ответы диалога с ожидания. Коммитит сессию."""
    replies = await _ready(session, thread_id)
    at = now or datetime.now(UTC)
    released = tuple(reply.id for reply in replies if _release(reply, by, at))
    asked: list[int] = []
    row = await handoff.start(
        session, thread_id, enqueue=asked.append, now=lambda: at, author_id=author_id
    )
    await session.commit()  # передача без новой работы (`handoff._reopen`) сама не коммитит
    queued = bool(asked) and await _queued(enqueue, row)
    logger.info(
        "продажи: лид передан телемаркетологу вручную",
        extra={"thread_id": thread_id, "handoff_id": row.id, "released": released},
    )
    return HandedOver(row, queued, released)


async def of_thread(session: AsyncSession, thread_id: int) -> SalesHandoffModel | None:
    """Передача диалога, если она есть: ключ передачи — диалог."""
    found: SalesHandoffModel | None = await session.scalar(
        select(SalesHandoffModel).where(SalesHandoffModel.thread_id == thread_id)
    )
    return found


async def _ready(session: AsyncSession, thread_id: int) -> Sequence[ReplyModel]:
    """Ответы диалога, если его лида можно передать сейчас; иначе отказ словами — до записи."""
    stage = await stage_of.of_thread(session, thread_id)  # номер больше столбца — тоже «нет»
    if stage is None:
        raise UnknownThreadError(f"диалога №{thread_id} нет — передавать некого")
    if stage is not Stage.SALES:
        raise HandoffRefusedError(
            f"диалог №{thread_id} — не продаж: телемаркетологу передают только лида продаж"
        )
    existing = await of_thread(session, thread_id)
    if existing is not None:
        raise HandoffRefusedError(already(existing))
    if not cfg.ENABLED:  # те же слова, что у `handoff.start`: до записи, а не после
        raise handoff.HandoffError(
            f"передача диалога №{thread_id} не начата: {handoff.SWITCHED_OFF}"
        )
    replies = (
        await session.scalars(
            select(ReplyModel)
            .where(ReplyModel.thread_id == thread_id)
            .order_by(ReplyModel.created_at, ReplyModel.id)
        )
    ).all()
    if not any(reply.kind is ReplyKind.HUMAN for reply in replies):
        raise HandoffRefusedError(
            f"в диалоге №{thread_id} нет ответа человека — телемаркетологу передают лида, "
            "который ответил"
        )
    await handoff.lead_of(session, thread_id)  # нет лида или их несколько — словами передачи
    return replies


def said(done: HandedOver) -> str:
    """Итог нажатия словами — строкой под кнопкой и уведомлением."""
    if done.queued:
        then = "сделка в Kommo и сообщение в Telegram уходят задачей очереди продаж"
    else:
        every = cfg.HANDOFF_RETRY_SEC // 60
        then = (
            "задача не встала в очередь — передачу возьмёт проход по расписанию "
            f"в пределах {every} мин"
        )
    count = len(done.released)
    if count == 0:
        waits = ""
    elif count == 1:
        waits = "; ответ больше не ждёт человека"
    else:
        waits = f"; с ожидания человека сняты ответы: {count}"
    return f"Лид передан телемаркетологу: {then}{waits}."


def already(row: SalesHandoffModel) -> str:
    """Отказ повтору: что с передачей сейчас. Когда передан — без времени: пояс человека знает
    экран, и строка состояния над отказом говорит дату его временем (стенд: «09:57 UTC» под
    «12:57» строки состояния читались как две разные передачи)."""
    return (
        f"лид уже передан телемаркетологу: Kommo — {kommo_said(row)}, "
        f"Telegram — {telegram_said(row)}; повтор ничего не меняет"
    )


def _release(reply: ReplyModel, by: str, at: datetime) -> bool:
    """Снять ответ с ожидания словами решения, если он ждёт человека. Отказ прошлой передачи
    (`handoff_error`) снимается: итог — этой."""
    if not review_of(reply, Stage.SALES).waiting:
        return False
    base = reply.model_parse or {"stage": Stage.SALES.value, "kind": None}
    kept = {key: value for key, value in base.items() if key != "handoff_error"}
    reply.model_parse = {**kept, "waits": False, "reason": f"{MANUAL}: {by}"}
    reply.reviewed_by, reply.reviewed_at = by[:128], at
    return True


async def _queued(enqueue: Enqueue, row: SalesHandoffModel) -> bool:
    """Задача передачи — в очередь из потока. Не встала — строка в журнал, а не отказ."""
    try:
        await asyncio.to_thread(enqueue, row.id)
    except Exception as exc:  # noqa: BLE001 — строка закоммичена, её возьмёт проход повторов
        logger.error(  # noqa: TRY400 — трассировка очереди ничего не добавит к причине
            "продажи: задача передачи лида не поставлена — её возьмёт проход по расписанию",
            extra={"handoff_id": row.id, "thread_id": row.thread_id, "error": described(exc)},
        )
        return False
    return True


#: Что с записью в Kommo, пока она не кончилась сделкой или отказом, — словами.
_KOMMO_WAITS = {
    HandoffKommo.PENDING: "запись ждёт задачи",
    HandoffKommo.RETRY: "не ответил — повторяем по расписанию",
    HandoffKommo.UNCONFIRMED: "запись не подтверждена — проверить в Kommo руками",
}


def kommo_said(row: SalesHandoffModel) -> str:
    """Что с Kommo — словами: сделка, только ссылка на диалог или отказ; номер сделки, если она
    есть, — первым."""
    deal = f"сделка №{row.kommo_lead_id}" if row.kommo_lead_id else None
    if row.kommo is HandoffKommo.DONE:
        return deal or "передача закрыта руками, сделки нет"
    if row.kommo is HandoffKommo.OFF:
        return "только ссылка на диалог: Kommo не подключён"
    if row.kommo is HandoffKommo.FAILED:
        state = f"отказ: {_failure(row, 'Kommo') or 'причина — в тревоге эксплуатации'}"
    else:
        state = _KOMMO_WAITS[row.kommo]
    return state if deal is None else f"{deal}; {state}"


def telegram_said(row: SalesHandoffModel) -> str:
    """Что с сообщением телемаркетологу — словами: отправлено или отказ."""
    if row.telegram is HandoffTelegram.PENDING:
        return "ещё не отправлено"
    if row.telegram is HandoffTelegram.SENT:
        return "отправлено"
    then = "повторим по расписанию" if row.telegram_due_at else "повторов больше нет"
    why = _failure(row, "Telegram")
    return f"не доставлено, {then}" + (f": {why}" if why else "")


def _failure(row: SalesHandoffModel, side: str) -> str | None:
    """Последний сбой строки передачи, если он этой стороны («Kommo: …», «Telegram: …»), — без
    ключей. Сбой у строки один на обе стороны: чужой сюда не идёт."""
    said = clean(row.last_error or "")
    head = f"{side}: "
    return said.removeprefix(head) if said.startswith(head) else None
