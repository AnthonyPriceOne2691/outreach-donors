"""Сообщение о лиде в Telegram: телемаркетологу и копия в группу, повтор по расписанию (5.3).

**Когда уходит.** Телемаркетолог получает сообщение, когда у передачи появилась ссылка,
которой он ещё не получал (`notified_link`): первая — всегда, ссылка на сделку после ссылки
на диалог — тоже, повтор той же — нет. Копия в группу — после личного, если включена (A5).

**Не ушло — повтор проходом по расписанию.** Бот сам пробует трижды за задачу
(`telegram.py`): это мигнувшая сеть. Не прошло из-за сети, 5xx или 429 — передача ждёт
повтора прохода (`telegram_due_at`, `handoff.message_due`): пауза растёт вдвое от
`FIRST_PAUSE_SEC`, у 429 — не меньше паузы, которую назвал Telegram. Попыток — задача
и повторы — `MESSAGE_TRIES`; после последней — тревога словами, повторов больше нет.
Постоянный отказ (нет токена или чата, чат не найден, бот заблокирован, непечатный знак
в токене) не повторяется: повтор его не изменит, тревога — сразу.

**Что ждёт повтора — видно по состоянию.** `undelivered` со сроком — личное сообщение,
`sent` со сроком — копия в группу: личное уже ушло. Раньше срока сообщение не уходит, даже
если задачу привело другое (новый ответ, повтор Kommo): пауза 429 — просьба Telegram.
Счёт попыток — у серии: срок стоит, пока она идёт; ушло или итог сказан — ноль и без срока.

**Итог сказан — дальше молча.** `undelivered` без срока — попытки кончились или отказ
постоянный, тревога ушла. Задача, пришедшая по другому поводу (повтор Kommo, новый ответ
лида), пробует сообщение ещё раз, как и раньше, но при отказе тревоги не повторяет и новой
серии повторов не заводит: попыток по расписанию — конечное число.

**Двойная отправка возможна в одном окне:** Telegram принял сообщение, а строка передачи
не записалась (задача умерла до коммита) — повтор пошлёт его ещё раз. Дубль сообщения
безвреднее потерянного лида.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from backend.config import sales as cfg
from backend.features.sales import handoff_text as wording
from backend.features.sales.handoff_kommo import headline
from backend.features.sales.handoff_text import Card
from backend.features.sales.models import HandoffTelegram, SalesHandoffModel
from backend.features.sales.telegram import TelegramError

if TYPE_CHECKING:
    from backend.features.sales.handoff import Deps

logger = logging.getLogger(__name__)

#: Сколько раз одно сообщение пробуют доставить: задача, затем повторы прохода.
MESSAGE_TRIES = 5
#: Пауза перед первым повтором прохода; каждая следующая вдвое дольше: 5, 10, 20, 40 мин.
FIRST_PAUSE_SEC = 5 * 60

#: Что не ушло — словами тревоги.
PERSONAL = "сообщение телемаркетологу не доставлено"
GROUP = "копия в группу продаж не доставлена"


def pause_after(tries: int, asked: float | None) -> float:
    """Пауза перед повтором после `tries` неудач: растёт вдвое; у 429 — не меньше паузы Telegram."""
    return max(float(FIRST_PAUSE_SEC << (tries - 1)), asked or 0.0)


async def step(row: SalesHandoffModel, card: Card, deps: Deps) -> None:
    """Сообщение телемаркетологу — когда появилась ссылка, которой он ещё не получал;
    копия в группу — после него или повтором, если личное уже ушло. Раньше срока повтора —
    ничего: пауза растёт, а у 429 её назвал сам Telegram."""
    if row.telegram_due_at is not None and row.telegram_due_at > deps.now():
        return
    deal = None
    if deps.kommo is not None and row.kommo_lead_id is not None:
        deal = deps.kommo.lead_url(row.kommo_lead_id)
    link = deal or wording.dialog_link(card.thread_id)
    words = wording.message(deal, card.thread_id, row.kommo)
    if row.notified_link != link:
        if await _personal(row, link, words, deps):
            await _group_copy(row, words, deps)
    elif row.telegram_due_at is not None:
        await _group_copy(row, words, deps)


async def _personal(row: SalesHandoffModel, link: str, words: str, deps: Deps) -> bool:
    """Личное сообщение. Ушло — `sent` со ссылкой; нет — «не доставлено» и повтор или тревога."""
    told = row.telegram is HandoffTelegram.UNDELIVERED and row.telegram_due_at is None
    if row.telegram is not HandoffTelegram.UNDELIVERED:
        _settled(row)  # новое личное сообщение — своя серия, даже если копия ждала повтора
    try:
        await deps.bot.send(cfg.TELEGRAM_CHAT_ID, words)
    except TelegramError as exc:
        row.telegram = HandoffTelegram.UNDELIVERED
        await _not_delivered(row, exc, deps, PERSONAL, prefix="Telegram", told=told)
        return False
    row.telegram, row.notified_link, row.notified_at = HandoffTelegram.SENT, link, deps.now()
    _settled(row)
    return True


async def _group_copy(row: SalesHandoffModel, words: str, deps: Deps) -> None:
    """Копия в группу продаж (A5). Недоставка — тревога, а не «не доставлено»:
    телемаркетолог сообщение получил."""
    if not cfg.TELEGRAM_GROUP_COPY:
        _settled(row)
        return
    if not cfg.TELEGRAM_GROUP_CHAT_ID:
        row.last_error = (
            "копия в группу включена (SALES_TELEGRAM_GROUP_COPY), а "
            "SALES_TELEGRAM_GROUP_CHAT_ID пуст — копия не ушла"
        )
        _settled(row)
        logger.warning("продажи: копия в группу не ушла — чат группы не задан")
        return
    try:
        await deps.bot.send(cfg.TELEGRAM_GROUP_CHAT_ID, words)
    except TelegramError as exc:
        await _not_delivered(row, exc, deps, GROUP, prefix="Telegram, копия в группу", told=False)
        return
    _settled(row)


async def _not_delivered(
    row: SalesHandoffModel,
    exc: TelegramError,
    deps: Deps,
    what: str,
    *,
    prefix: str,
    told: bool,
) -> None:
    """Не ушло. Временный отказ — повтор проходом, а после последней попытки — тревога;
    постоянный — тревога сразу и без повтора. `told` — итог об этом сообщении уже сказан:
    молча и без новой серии повторов."""
    said = {"handoff_id": row.id, "what": what, "error": str(exc)}
    if exc.permanent or told:
        _settled(row)
        row.last_error = f"{prefix}: {exc}"
        logger.warning("продажи: сообщение о лиде не доставлено — без повтора", extra=said)
        if not told:
            await deps.alert(f"{headline(row)}: {what} — {exc}")
        return
    row.telegram_tries += 1
    tries = row.telegram_tries
    if tries < MESSAGE_TRIES:
        due = deps.now() + timedelta(seconds=pause_after(tries, exc.retry_after))
        row.telegram_due_at = due
        row.last_error = (
            f"{prefix}: {exc}; повтор не раньше {due:%d.%m %H:%M} UTC "
            f"(попытка {tries} из {MESSAGE_TRIES})"
        )
        logger.warning(
            "продажи: сообщение о лиде не доставлено — повторим по расписанию",
            extra={**said, "tries": tries, "due_at": due.isoformat()},
        )
        return
    _settled(row)
    row.last_error = f"{prefix}: {exc}; попыток — {tries}, повторов больше нет"
    logger.warning(
        "продажи: сообщение о лиде не доставлено — попытки кончились",
        extra={**said, "tries": tries},
    )
    await deps.alert(
        f"{headline(row)}: {what} за {tries} попыток — повторов больше нет, передать лида "
        f"телемаркетологу руками: {exc}"
    )


def _settled(row: SalesHandoffModel) -> None:
    """Повторять нечего: ушло, отказ постоянный или попытки кончились."""
    row.telegram_tries, row.telegram_due_at = 0, None
