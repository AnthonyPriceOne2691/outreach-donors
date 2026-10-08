"""Запись передачи в Kommo: сделка или примечание, и что делать с отказом (срез 5.3).

Порядок и исходы — в шапке `handoff.py`; здесь — сам шаг. Номер сделки пишется в
базу сразу после ответа Kommo, до следующего запроса: потерять его значит на повторе
завести вторую. Отказ Kommo — не исключение, а состояние передачи и тревога владельцу
при смене состояния: тот же отказ на каждом круге прохода тревогу не повторяет.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import sales as cfg
from backend.features.sales import handoff_text as wording
from backend.features.sales.handoff_text import Card
from backend.features.sales.kommo import (
    KommoClient,
    KommoContact,
    KommoError,
    KommoFormatError,
    KommoUnavailableError,
    KommoUnconfirmedError,
)
from backend.features.sales.models import HandoffKommo, SalesHandoffModel

logger = logging.getLogger(__name__)

Alert = Callable[[str], Awaitable[bool]]

#: Kommo ждёт записи — задаче есть что делать.
WORK = (HandoffKommo.PENDING, HandoffKommo.RETRY)


def headline(row: SalesHandoffModel) -> str:
    """Начало тревоги: номера, а не адрес лида — чат эксплуатации не место для них."""
    return f"продажи: передача лида №{row.lead_id} (диалог №{row.thread_id})"


async def write(
    session: AsyncSession,
    row: SalesHandoffModel,
    card: Card,
    kommo: KommoClient | None,
    alert: Alert,
) -> None:
    """Сделка или примечание. Отказ Kommo — состояние и тревога при смене состояния."""
    if row.kommo not in WORK:
        return
    if kommo is None:
        row.kommo, row.noted_reply_id = HandoffKommo.OFF, card.reply_id
        return
    before, created = row.kommo, row.kommo_lead_id is None
    row.attempts += 1
    try:
        if row.kommo_lead_id is None:
            row.kommo_lead_id = await _new_deal(kommo, card)
            # Номер сделки — в базу до следующего запроса: потерять его значит
            # на повторе завести вторую.
            await session.commit()
        warning = await _note(kommo, row, card)
    except KommoError as exc:
        row.kommo, row.last_error = _refusal_state(exc), f"Kommo: {exc}"
        logger.warning(
            "продажи: Kommo не принял передачу лида",
            extra={"handoff_id": row.id, "kommo": row.kommo.value, "error": str(exc)},
        )
        if row.kommo is not before:
            await alert(_kommo_alert(row, exc))
        return
    row.kommo = HandoffKommo.DONE
    if warning:
        row.last_error = warning
        await alert(f"{headline(row)}: {warning}")
    if before is HandoffKommo.RETRY:
        done = "сделка в Kommo заведена" if created else "примечание в Kommo записано"
        await alert(f"{headline(row)}: {done} после повтора, попыток — {row.attempts}")


async def _new_deal(kommo: KommoClient, card: Card) -> int:
    """Сделка с контактом и компанией. Ответ потерян — поиск решает, повторять ли."""
    known = await kommo.find_contact(card.email)
    try:
        return (await kommo.create_complex_lead(card.new_lead)).id
    except (KommoUnconfirmedError, KommoFormatError) as exc:
        raise await _after_lost_answer(kommo, card.email, known, exc) from None


async def _after_lost_answer(
    kommo: KommoClient, email: str, known: KommoContact | None, exc: KommoError
) -> KommoError:
    """Запись могла дойти. Контакта с этой почтой не было ни до записи, ни после —
    не дошла: `retry`. Был до записи или есть после — не узнать, создалась ли
    сделка: `unconfirmed`, решает человек. Поиск Kommo может отставать от записи на
    секунды; этот риск назван в решениях среза."""
    try:
        found = await kommo.find_contact(email)
    except KommoError as search:
        logger.warning(
            "продажи: поиск после потерянного ответа Kommo не ответил",
            extra={"error": str(search)},
        )
        return KommoUnconfirmedError(f"{exc}; поиск после записи не ответил ({search})")
    if known is None and found is None:
        return KommoUnavailableError(
            f"{exc}; поиск: контакта с этой почтой нет ни до записи, ни после — "
            "запись не дошла, повторим по расписанию"
        )
    return KommoUnconfirmedError(
        f"{exc}; поиск: контакт с этой почтой есть — сделка могла создаться"
    )


async def _note(kommo: KommoClient, row: SalesHandoffModel, card: Card) -> str | None:
    """Последнее письмо — примечанием, если ещё не легло. Ответ потерян — повтора нет
    (задвоило бы), возвращается предупреждение для тревоги."""
    if row.kommo_lead_id is None or card.reply_id in (None, row.noted_reply_id):
        return None
    try:
        await kommo.add_note(row.kommo_lead_id, wording.note(card))
    except (KommoUnconfirmedError, KommoFormatError) as exc:
        logger.warning(
            "продажи: примечание к сделке не подтверждено — не повторяем",
            extra={"kommo_lead_id": row.kommo_lead_id, "error": str(exc)},
        )
        row.noted_reply_id = card.reply_id
        return f"примечание к сделке №{row.kommo_lead_id} не подтверждено ({exc}) — повтора нет"
    row.noted_reply_id = card.reply_id
    return None


def _refusal_state(exc: KommoError) -> HandoffKommo:
    if isinstance(exc, KommoUnavailableError):
        return HandoffKommo.RETRY
    if isinstance(exc, KommoUnconfirmedError):
        return HandoffKommo.UNCONFIRMED
    return HandoffKommo.FAILED


def _kommo_alert(row: SalesHandoffModel, exc: KommoError) -> str:
    """Тревога владельцу словами: что с Kommo и что получит телемаркетолог."""
    link = "" if row.kommo_lead_id else "; телемаркетологу — ссылка на диалог"
    if row.kommo is HandoffKommo.RETRY:
        every = cfg.HANDOFF_RETRY_SEC // 60
        return f"{headline(row)}: Kommo не ответил — {exc}; повторяем каждые {every} мин{link}"
    if row.kommo is HandoffKommo.UNCONFIRMED:
        return (
            f"{headline(row)}: запись в Kommo не подтверждена — {exc}; проверить в Kommo "
            f"руками, есть ли сделка: автоповтора нет{link}"
        )
    return f"{headline(row)}: Kommo отказал — {exc}{link}"
