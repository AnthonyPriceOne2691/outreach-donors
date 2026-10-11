"""Сообщение о лиде в Telegram: телемаркетологу и копия в группу, повтор по расписанию (5.3).

**Когда уходит.** Телемаркетолог получает сообщение, когда у передачи появилась ссылка,
которой он ещё не получал (`notified_link`): первая — всегда, ссылка на сделку после ссылки
на диалог — тоже, повтор той же — нет. Копия в группу — после личного, если включена (A5).

**Новое письмо лида, уже переданного, — своё сообщение** (`news`): «Лид написал снова / Эмейл
Рассылка / Ссылка…» — формат «Новый лид» только для нового лида. Ключ — номер ответа, о котором
телемаркетолог уже получил личное сообщение (`told_reply_id`): письмо новее — сообщение, то же
письмо — нет, сколько бы задач за ним ни пришло. Ключ свой, а не `noted_reply_id` Kommo: Kommo
молчит — сообщение всё равно уходит (ссылкой на сделку, а без неё — на диалог), а примечание ждёт
повтора. Копия в группу, которую повторяет проход, — тем же текстом, что личное (`told_again`).

**Не ушло — повтор проходом по расписанию.** Бот сам пробует трижды за задачу
(`telegram.py`): это мигнувшая сеть. Не прошло из-за сети, 5xx или 429 — передача ждёт
повтора прохода (`telegram_due_at`, `handoff.message_due`). Серия — паузы, попытки, итог
и постоянный отказ — одно правило с сообщением о черновике агента: `telegram_series.py`.

**Что ждёт повтора — видно по состоянию.** `undelivered` со сроком — личное сообщение,
`sent` со сроком — копия в группу: личное уже ушло. Раньше срока сообщение не уходит, даже
если задачу привело другое (новый ответ, повтор Kommo): пауза 429 — просьба Telegram.
Счёт попыток — у серии: срок стоит, пока она идёт; ушло или итог сказан — ноль и без срока.

**Итог сказан — дальше молча.** `undelivered` без срока — попытки кончились или отказ
постоянный, тревога ушла. Задача, пришедшая по другому поводу (повтор Kommo, новый ответ
лида), пробует сообщение ещё раз, но при отказе тревоги не повторяет и новой серии повторов
не заводит (`telegram_series.after_failure`, `told`).

**Двойная отправка возможна в одном окне:** Telegram принял сообщение, а строка передачи
не записалась (задача умерла до коммита) — повтор пошлёт его ещё раз. Дубль сообщения
безвреднее потерянного лида.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.config import sales as cfg
from backend.features.sales import handoff_text as wording
from backend.features.sales.handoff_kommo import headline
from backend.features.sales.handoff_text import Card
from backend.features.sales.models import HandoffTelegram, SalesHandoffModel
from backend.features.sales.telegram import TelegramError
from backend.features.sales.telegram_series import MESSAGE_TRIES, after_failure

if TYPE_CHECKING:
    from backend.features.sales.handoff import Deps

logger = logging.getLogger(__name__)

#: Что не ушло — словами тревоги.
PERSONAL = "сообщение телемаркетологу не доставлено"
GROUP = "копия в группу продаж не доставлена"


def news(row: SalesHandoffModel, reply_id: int | None) -> bool:
    """Пришло ли письмо новее того, о котором телемаркетолог уже получил сообщение. Пока не
    получил ни одного, новостей нет: первое сообщение — «Новый лид» по ссылке."""
    told = row.told_reply_id
    return reply_id is not None and told is not None and reply_id != told


async def step(row: SalesHandoffModel, card: Card, deps: Deps) -> None:
    """Сообщение телемаркетологу — когда появилась ссылка, которой он ещё не получал, или
    письмо новее того, о котором он знает; копия в группу — после него или повтором, если
    личное уже ушло. Раньше срока повтора — ничего: пауза растёт, а у 429 её назвал сам
    Telegram."""
    if row.telegram_due_at is not None and row.telegram_due_at > deps.now():
        return
    deal = _deal_url(row, deps)
    link = deal or wording.dialog_link(card.thread_id)
    again = news(row, card.reply_id)
    if row.notified_link != link or again:
        words = wording.message(deal, card.thread_id, row.kommo, again=again)
        if await _personal(row, link, words, deps, told=(card.reply_id, again)):
            await _group_copy(row, words, deps)
    elif row.telegram_due_at is not None:
        words = wording.message(deal, card.thread_id, row.kommo, again=row.told_again)
        await _group_copy(row, words, deps)


def _deal_url(row: SalesHandoffModel, deps: Deps) -> str | None:
    """Ссылка на сделку, если она заведена и Kommo подключён; иначе — `None`: ссылка на диалог."""
    if deps.kommo is None or row.kommo_lead_id is None:
        return None
    return deps.kommo.lead_url(row.kommo_lead_id)


async def _personal(
    row: SalesHandoffModel,
    link: str,
    words: str,
    deps: Deps,
    *,
    told: tuple[int | None, bool],
) -> bool:
    """Личное сообщение. Ушло — `sent` со ссылкой и ключом (`told`: о каком ответе и было ли
    оно «Лид написал снова»); нет — «не доставлено» и повтор или тревога."""
    said = row.telegram is HandoffTelegram.UNDELIVERED and row.telegram_due_at is None
    if row.telegram is not HandoffTelegram.UNDELIVERED:
        _settled(row)  # новое личное сообщение — своя серия, даже если копия ждала повтора
    try:
        await deps.bot.send(cfg.TELEGRAM_CHAT_ID, words)
    except TelegramError as exc:
        row.telegram = HandoffTelegram.UNDELIVERED
        then = await _not_delivered(row, exc, deps, PERSONAL, prefix="Telegram", told=said)
        logger.warning(
            "продажи: сообщение телемаркетологу не доставлено",
            extra={"handoff_id": row.id, "error": str(exc), "then": then},
        )
        return False
    row.telegram, row.notified_link, row.notified_at = HandoffTelegram.SENT, link, deps.now()
    row.told_reply_id, row.told_again = told
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
        then = await _not_delivered(
            row, exc, deps, GROUP, prefix="Telegram, копия в группу", told=False
        )
        logger.warning(
            "продажи: копия в группу не доставлена",
            extra={"handoff_id": row.id, "error": str(exc), "then": then},
        )
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
) -> str:
    """Не ушло — серия повторов (`telegram_series.after_failure`): временный отказ — повтор
    проходом, а после последней попытки — тревога; постоянный — тревога сразу и без повтора.
    `told` — итог об этом сообщении уже сказан: молча и без новой серии повторов. Возвращает,
    что решено, — для строки журнала."""
    failed = after_failure(row.telegram_tries, exc, deps.now(), told=told)
    row.telegram_tries, row.telegram_due_at = failed.tries, failed.due_at
    row.last_error = f"{prefix}: {failed.error(exc)}"
    if failed.due_at is not None:
        then = failed.due_at.isoformat()
        return f"повтор не раньше {then}, попытка {failed.tries} из {MESSAGE_TRIES}"
    if failed.spent is not None:
        await deps.alert(
            f"{headline(row)}: {what} за {failed.spent} попыток — повторов больше нет, передать "
            f"лида телемаркетологу руками: {exc}"
        )
        return f"попытки кончились ({failed.spent})"
    if failed.alarm:
        await deps.alert(f"{headline(row)}: {what} — {exc}")
    return "без повтора"


def _settled(row: SalesHandoffModel) -> None:
    """Повторять нечего: ушло, отказ постоянный или попытки кончились."""
    row.telegram_tries, row.telegram_due_at = 0, None
