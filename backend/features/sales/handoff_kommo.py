"""Запись передачи в Kommo: сделка или примечание, и что делать с отказом (срез 5.3).

Порядок и исходы — в шапке `handoff.py`; здесь — сам шаг. Номер сделки пишется в
базу сразу после ответа Kommo, до следующего запроса: потерять его значит на повторе
завести вторую. Отказ Kommo — не исключение, а состояние передачи и тревога владельцу
при смене состояния: тот же отказ на каждом круге прохода тревогу не повторяет.

**Чего клиент не умеет — по `KommoClient.can`, а не по его имени.** Шлюз агентства
(`kommo_gateway.py`) умеет одно — завести сделку:
- первое примечание (последнее письмо лида и сводка) едет в самой сделке (`NewLead.note`),
  ответ сразу отмечен записанным — второго запроса за примечанием нет;
- поиска контакта нет — потерянный ответ не проверить: сразу `unconfirmed`, решает человек;
- примечания к заведённой сделке нет — следующий ответ лида уходит телемаркетологу
  сообщением со ссылкой на ту же сделку: ссылка снова «не получена» (`notified_link`), и шаг
  Telegram пошлёт её; в журнал передачи — «примечание в Kommo не поддержано шлюзом».
  Второй сделки нет, передача не падает.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import replace

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

#: Что пишется в журнал передачи, когда новый ответ не лёг примечанием: метода нет у шлюза.
NOT_NOTED = (
    "примечание в Kommo не поддержано шлюзом — ответ лида уходит телемаркетологу сообщением "
    "со ссылкой на ту же сделку"
)


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
            await _new_deal(kommo, row, card)
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


async def _new_deal(kommo: KommoClient, row: SalesHandoffModel, card: Card) -> None:
    """Сделка с контактом и компанией — номер в строку передачи. Клиент, который везёт
    первое примечание в самой сделке, получает его в `NewLead.note`, и ответ отмечается
    записанным вместе с номером. Ответ потерян — поиск решает, повторять ли."""
    carried = kommo.can.note_in_lead and card.reply_id is not None
    lead = replace(card.new_lead, note=wording.note(card)) if carried else card.new_lead
    known = await kommo.find_contact(card.email)
    try:
        row.kommo_lead_id = (await kommo.create_complex_lead(lead)).id
    except (KommoUnconfirmedError, KommoFormatError) as exc:
        raise await _after_lost_answer(kommo, card.email, known, exc) from None
    if carried:
        row.noted_reply_id = card.reply_id


async def _after_lost_answer(
    kommo: KommoClient, email: str, known: KommoContact | None, exc: KommoError
) -> KommoError:
    """Запись могла дойти. Контакта с этой почтой не было ни до записи, ни после —
    не дошла: `retry`. Был до записи или есть после — не узнать, создалась ли
    сделка: `unconfirmed`, решает человек. Поиск Kommo может отставать от записи на
    секунды; этот риск назван в решениях среза. Поиска у клиента нет (шлюз) — проверить
    нечем: `unconfirmed`."""
    if not kommo.can.search:
        return KommoUnconfirmedError(f"{exc}; поиском не проверить — контакты этот клиент не ищет")
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
    if not kommo.can.add_notes:
        _told_instead(row, card)
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


def _told_instead(row: SalesHandoffModel, card: Card) -> None:
    """Примечаний к заведённой сделке клиент не пишет (шлюз): ответ отмечен — задача его
    не повторит, — а ссылка на ту же сделку снова «не получена», и шаг Telegram пошлёт
    её телемаркетологу. Слова — в журнал передачи и в строку передачи."""
    row.noted_reply_id, row.notified_link, row.last_error = card.reply_id, None, NOT_NOTED
    logger.warning(
        "продажи: примечание в Kommo не поддержано шлюзом — ответ лида уходит сообщением",
        extra={"handoff_id": row.id, "kommo_lead_id": row.kommo_lead_id, "reply_id": card.reply_id},
    )


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
