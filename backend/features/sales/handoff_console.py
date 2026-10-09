"""Передачи, где Kommo решает человек: `unconfirmed` и `failed` — список и два решения.

**Кого показывает.** Запись ушла, а ответ Kommo потерян, и поиск не решил (`unconfirmed`:
сделка могла создаться), или Kommo отказал (`failed`: ключ, права, форма ответа). Сами
они не сдвинутся: повтор вслепую мог бы завести вторую сделку, а отказ повтор не чинит.
Строка — номер, лид, причина словами, номер сделки, если есть, и сколько раз задача
бралась за запись.

**Два решения человека.**
- «Повторить» — человек проверил в Kommo: сделки нет. Запись снова ждёт задачи
  (`pending`), задача ставится в очередь продаж; не встала — её возьмёт проход по
  расписанию (`handoff.due`). Заведённая сделка уйдёт телемаркетологу ссылкой. Продажи
  выключены (`SALES_ENABLED`) — отказ словами, ничего не меняется: задача не пошла бы
  ни в Kommo, ни в Telegram. Список и «закрыть руками» наружу не ходят — работают и так.
- «Закрыть руками» — с обязательной запиской: сделка в Kommo есть (её номер — `deal`,
  и следующий ответ лида ляжет к ней примечанием) или ничего не нужно. Передача —
  `done`, её ответы отмечены: повтор сообщения и другие задачи запись не откроют.
  Без номера следующий ответ лида заведёт сделку заново — это говорится словами.

**Только эти два состояния.** Остальные задача чинит сама (`pending`, `retry`) или
чинить нечего (`done`, `off`) — отказ словами. Передачу держит живая задача — тоже
отказ: её итог перезаписал бы решение человека.

**Журнал действий.** Каждое решение — строка `audit_log` в той же транзакции: кто,
что было и что стало, причина, записка. Отдельного вида события нет — это
`user_updated`, как у решения по письму с неизвестным исходом
(`letters/unknown_outcome.py`); что сделано, говорит поле «действие». Чтение не пишется.

**Ключи — никуда.** Ключ Kommo, ключ шлюза агентства и токен бота продаж не уходят ни
в вывод, ни в журнал, ни в слова отказа: всё, что приходит из строки передачи или от
человека, проходит `clean`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import AuditAction
from backend.features.runs.failures import described
from backend.features.sales import telegram
from backend.features.sales.handoff import enqueue_handoff, latest_reply
from backend.features.sales.models import (
    HandoffKommo,
    HandoffTelegram,
    SalesHandoffModel,
    SalesLeadModel,
)

logger = logging.getLogger(__name__)

#: Где Kommo решает человек.
WAITING = (HandoffKommo.UNCONFIRMED, HandoffKommo.FAILED)

#: Чем заменяется ключ Kommo (и ключ шлюза) во всём, что печатается и пишется в журнал.
HIDDEN_KOMMO = "<ключ Kommo>"

#: Состояние Kommo — словами.
KOMMO_WORDS = {
    HandoffKommo.PENDING: "запись в Kommo ждёт задачи",
    HandoffKommo.RETRY: "Kommo не ответил — запись повторяет проход",
    HandoffKommo.UNCONFIRMED: "запись в Kommo не подтверждена",
    HandoffKommo.FAILED: "Kommo отказал",
    HandoffKommo.DONE: "сделка в Kommo есть или передача закрыта",
    HandoffKommo.OFF: "Kommo не подключён",
}

#: Сообщение телемаркетологу — словами.
TELEGRAM_WORDS = {
    HandoffTelegram.PENDING: "ещё не отправлено",
    HandoffTelegram.SENT: "доставлено",
    HandoffTelegram.UNDELIVERED: "не доставлено",
}

#: «Повторить» при выключенных продажах — отказ этими словами.
SWITCHED_OFF = (
    "продажи выключены (SALES_ENABLED) — передача №{handoff} не поставлена; "
    "включите продажи и повторите"
)

Enqueue = Callable[[int], object]


class ConsoleRefusalError(ValueError):
    """Решение нельзя принять как названо. Слова говорят почему и что делать."""


@dataclass(frozen=True, slots=True)
class Waiting:
    """Строка списка: передача, где Kommo решает человек."""

    id: int
    lead_id: int
    email: str
    company: str
    thread_id: int
    kommo: HandoffKommo
    #: Последний сбой словами, без ключей.
    reason: str
    #: Номер сделки в Kommo; `None` — сделки нет.
    deal: int | None
    #: Сколько раз задача бралась за запись в Kommo.
    attempts: int
    telegram: HandoffTelegram


def clean(text: str) -> str:
    """Текст без ключа Kommo, ключа шлюза и токена бота продаж."""
    text = telegram.hidden(text)
    for key in (cfg.KOMMO_TOKEN, cfg.KOMMO_GATEWAY_KEY):
        text = text.replace(key, HIDDEN_KOMMO) if key else text
    return text


async def waiting(session: AsyncSession) -> list[Waiting]:
    """Передачи в `unconfirmed` и `failed` — по номеру. Ничего не меняет."""
    rows = await session.execute(
        select(SalesHandoffModel, SalesLeadModel.email, SalesLeadModel.company)
        .join(SalesLeadModel, SalesLeadModel.id == SalesHandoffModel.lead_id)
        .where(SalesHandoffModel.kommo.in_(WAITING))
        .order_by(SalesHandoffModel.id)
    )
    return [
        Waiting(
            id=row.id,
            lead_id=row.lead_id,
            email=email,
            company=company or "",
            thread_id=row.thread_id,
            kommo=row.kommo,
            reason=clean(row.last_error or ""),
            deal=row.kommo_lead_id,
            attempts=row.attempts,
            telegram=row.telegram,
        )
        for row, email, company in rows.tuples()
    ]


async def retry(
    session: AsyncSession,
    handoff_id: int,
    *,
    author: str,
    now: datetime,
    enqueue: Enqueue = enqueue_handoff,
) -> bool:
    """«Повторить»: сделки в Kommo нет — запись снова ждёт задачи. Коммитит сессию.

    Возвращает, встала ли задача в очередь. Не встала — не отказ: строка закоммичена,
    и срок прохода стоит, как у `handoff.start`, — её возьмёт проход по расписанию."""
    if not cfg.ENABLED:
        raise ConsoleRefusalError(SWITCHED_OFF.format(handoff=handoff_id))
    row = await _decided(session, handoff_id, now, verb="повторить")
    was = row.kommo
    row.kommo = HandoffKommo.PENDING
    row.due_at = now + timedelta(seconds=cfg.HANDOFF_RETRY_SEC)
    await _journal(session, row, author, "передача лида — повторить запись в Kommo", was, {})
    await session.commit()
    try:
        enqueue(row.id)
    except Exception as exc:  # noqa: BLE001 — строка закоммичена, её возьмёт проход повторов
        logger.error(  # noqa: TRY400 — трассировка очереди ничего не добавит к причине
            "продажи: повтор передачи из консоли не поставлен — её возьмёт проход",
            extra={"handoff_id": row.id, "error": described(exc)},
        )
        return False
    return True


async def close(
    session: AsyncSession,
    handoff_id: int,
    *,
    note: str,
    deal: int | None,
    author: str,
    now: datetime,
) -> SalesHandoffModel:
    """«Закрыть руками» с запиской: сделка есть (`deal` — её номер) или ничего не нужно.
    Ответы диалога отмечены: запись в Kommo не откроет ни одна задача, пока не придёт
    новый ответ лида. Коммитит сессию."""
    said = clean(note.strip())
    if not said:
        raise ConsoleRefusalError(
            "записка обязательна: что проверено в Kommo и почему передача закрыта — "
            "её прочтёт тот, кто откроет журнал"
        )
    if deal is not None and deal <= 0:
        raise ConsoleRefusalError(f"№{deal} — не номер сделки: нужен номер из адреса сделки")
    row = await _decided(session, handoff_id, now, verb="закрыть")
    if deal is not None and row.kommo_lead_id not in (None, deal):
        raise ConsoleRefusalError(
            f"у передачи №{row.id} уже есть сделка №{row.kommo_lead_id} — другой номер "
            "не записываю: проверьте в Kommo, какая сделка у лида"
        )
    was = row.kommo
    row.kommo = HandoffKommo.DONE
    if deal is not None:
        row.kommo_lead_id = deal
    latest = await latest_reply(session, row.thread_id)
    if latest is not None:
        row.noted_reply_id = latest.id
    extra: dict[str, object] = {"записка": said, "сделка указана": deal}
    await _journal(session, row, author, "передача лида — закрыта руками", was, extra)
    await session.commit()
    return row


async def _decided(
    session: AsyncSession, handoff_id: int, now: datetime, *, verb: str
) -> SalesHandoffModel:
    """Передача, о которой человек вправе решать: есть, ждёт его и не занята задачей."""
    row = await session.scalar(
        select(SalesHandoffModel)
        .where(SalesHandoffModel.id == handoff_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise ConsoleRefusalError(
            f"передачи №{handoff_id} нет — номер из списка `outreach sales-handoffs`"
        )
    if row.kommo not in WAITING:
        raise ConsoleRefusalError(
            f"передача №{row.id} — «{KOMMO_WORDS[row.kommo]}»: {verb} можно только "
            "неподтверждённую запись в Kommo или отказ Kommo"
        )
    busy = now - timedelta(seconds=cfg.HANDOFF_CLAIM_SEC)
    if row.claimed_at is not None and row.claimed_at >= busy:
        raise ConsoleRefusalError(
            f"передачу №{row.id} сейчас держит задача — её итог перезаписал бы решение; "
            "повторите команду через минуту"
        )
    return row


async def _journal(
    session: AsyncSession,
    row: SalesHandoffModel,
    author: str,
    action: str,
    was: HandoffKommo,
    extra: dict[str, object],
) -> None:
    """Решение — в журнал действий той же транзакцией, без ключей."""
    await AccessRepository(session).record(
        AuditAction.USER_UPDATED,
        target=f"sales_handoff:{row.id}",
        details={
            "действие": action,
            "кто": author,
            "лид": row.lead_id,
            "диалог": row.thread_id,
            "было": was.value,
            "стало": row.kommo.value,
            "причина": clean(row.last_error or ""),
            "сделка": row.kommo_lead_id,
            **extra,
        },
    )
