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

**Личное не дошло — группа всё равно узнаёт** (решение владельца 13.10, `_heads_up`): когда
итог сказан — отказ постоянный (чат не задан, бот заблокирован) или попытки кончились, — копия
в группу уходит и без личного, тем же текстом со строкой «Личное сообщение телемаркетологу не
доставлено: <причина>». Тёплый лид не ждёт, пока прочтут чат эксплуатации. Мигнувшую сеть, которую
лечит повтор через 5 минут, группа не видит: на сбой внутри серии копии нет. Одна копия на итог,
серию повторов личного она не трогает; не ушла и она — тревога говорит это словами. Копия в
группу выключена (`SALES_TELEGRAM_GROUP_COPY`) — в группу не пишем: это было бы новое сообщение
наружу; тревога владельцу говорит, что группа о лиде не знает. Личное дошло позже (новая задача
после итога) — копия уходит, как всегда после личного.

**Письмо переданного лида через шлюз Kommo** в сделку не ложится (`KommoClient.can.add_notes`):
«Лид написал снова» несёт и ссылку на диалог, где письмо (`handoff_text.message`, `unnoted`),
и копия в группу, которую повторяет проход, — тот же текст.

**Двойная отправка возможна в одном окне:** Telegram принял сообщение, а строка передачи
не записалась (задача умерла до коммита) — повтор пошлёт его ещё раз. Дубль сообщения
безвреднее потерянного лида.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from backend.config import sales as cfg
from backend.features.ops.alarms import cause, marked, no_cause
from backend.features.sales import handoff_text as wording
from backend.features.sales.handoff_kommo import headline
from backend.features.sales.handoff_text import Card
from backend.features.sales.models import HandoffTelegram, SalesHandoffModel
from backend.features.sales.telegram import TelegramError
from backend.features.sales.telegram_series import MESSAGE_TRIES, Failed, after_failure

if TYPE_CHECKING:
    from backend.features.sales.handoff import Deps

logger = logging.getLogger(__name__)

#: Что не ушло — словами тревоги.
PERSONAL = "сообщение телемаркетологу не доставлено"
GROUP = "копия в группу продаж не доставлена"
#: Пометка тревоги последней строкой (`ops/alarms.py`): копия уходит после личного — значит,
#: телемаркетолог лида получил.
CONCERNS = {
    PERSONAL: cause("телемаркетолог не получил лида", "передать лида телемаркетологу руками"),
    GROUP: no_cause("телемаркетолог получил лида лично, не дошла только копия в группу"),
}

#: Что с группой, когда личное не дошло, — хвост тревоги владельцу (`_heads_up`).
GROUP_TOLD = "группа продаж получила сообщение о лиде с пометкой «не доставлено»"
GROUP_OFF = "копия в группу выключена (SALES_TELEGRAM_GROUP_COPY) — группа о лиде не знает"
GROUP_UNSET = "копия в группу не ушла — SALES_TELEGRAM_GROUP_CHAT_ID пуст, группа о лиде не знает"


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
        words = _words(row, card, deps, deal, again=again)
        if await _personal(row, link, words, deps, told=(card.reply_id, again)):
            await _group_copy(row, words, deps)
    elif row.telegram_due_at is not None:
        await _group_copy(row, _words(row, card, deps, deal, again=row.told_again), deps)


def _deal_url(row: SalesHandoffModel, deps: Deps) -> str | None:
    """Ссылка на сделку, если она заведена и Kommo подключён; иначе — `None`: ссылка на диалог."""
    if deps.kommo is None or row.kommo_lead_id is None:
        return None
    return deps.kommo.lead_url(row.kommo_lead_id)


def _words(row: SalesHandoffModel, card: Card, deps: Deps, deal: str | None, *, again: bool) -> str:
    """Текст сообщения. Новое письмо переданного лида через шлюз Kommo в сделку не легло —
    клиент примечаний не пишет (`can.add_notes`): к ссылке на сделку — ссылка на диалог.
    Человек дал телефон — последней строкой «Телефон: …» (`handoff_text.phone_line`)."""
    unnoted = again and deps.kommo is not None and not deps.kommo.can.add_notes
    return wording.message(
        deal, card.thread_id, row.kommo, again=again, unnoted=unnoted, phones=card.phones
    )


async def _personal(
    row: SalesHandoffModel,
    link: str,
    words: str,
    deps: Deps,
    *,
    told: tuple[int | None, bool],
) -> bool:
    """Личное сообщение. Ушло — `sent` со ссылкой и ключом (`told`: о каком ответе и было ли
    оно «Лид написал снова»); нет — «не доставлено» и повтор или тревога, а когда итог сказан —
    и копия в группу с пометкой (`_heads_up`)."""
    said = row.telegram is HandoffTelegram.UNDELIVERED and row.telegram_due_at is None
    if row.telegram is not HandoffTelegram.UNDELIVERED:
        _settled(row)  # новое личное сообщение — своя серия, даже если копия ждала повтора
    try:
        await deps.bot.send(cfg.TELEGRAM_CHAT_ID, words)
    except TelegramError as exc:
        row.telegram = HandoffTelegram.UNDELIVERED
        failed = after_failure(row.telegram_tries, exc, deps.now(), told=said)
        group = await _heads_up(words, failed.error(exc), deps) if failed.alarm else None
        then = await _not_delivered(
            row, exc, failed, deps, PERSONAL, prefix="Telegram", group=group
        )
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
        failed = after_failure(row.telegram_tries, exc, deps.now(), told=False)
        then = await _not_delivered(
            row, exc, failed, deps, GROUP, prefix="Telegram, копия в группу"
        )
        logger.warning(
            "продажи: копия в группу не доставлена",
            extra={"handoff_id": row.id, "error": str(exc), "then": then},
        )
        return
    _settled(row)


async def _heads_up(words: str, why: str, deps: Deps) -> str:
    """Личное сообщение не дошло, итог сказан — группа продаж узнаёт о лиде тем же текстом с
    пометкой, почему не доставлено (решение владельца 13.10). Копия выключена или чат группы не
    задан — в группу не пишем. Строку передачи не трогает: серия повторов — у личного. Возвращает,
    что с группой, — словами для тревоги владельцу."""
    if not cfg.TELEGRAM_GROUP_COPY:
        return GROUP_OFF
    if not cfg.TELEGRAM_GROUP_CHAT_ID:
        return GROUP_UNSET
    try:
        await deps.bot.send(cfg.TELEGRAM_GROUP_CHAT_ID, wording.undelivered(words, why))
    except TelegramError as exc:
        logger.warning(
            "продажи: копия в группу о недоставленном личном сообщении не ушла",
            extra={"error": str(exc)},
        )
        return f"копия в группу тоже не ушла — группа о лиде не знает: {exc}"
    return GROUP_TOLD


async def _not_delivered(
    row: SalesHandoffModel,
    exc: TelegramError,
    failed: Failed,
    deps: Deps,
    what: str,
    *,
    prefix: str,
    group: str | None = None,
) -> str:
    """Не ушло — серия повторов (`telegram_series.after_failure`): временный отказ — повтор
    проходом, а после последней попытки — тревога; постоянный — тревога сразу и без повтора;
    итог об этом сообщении уже сказан — молча и без новой серии повторов. `group` — что с
    группой, когда личное не дошло (`_heads_up`), — в конце тревоги. Возвращает, что решено, —
    для строки журнала."""
    row.telegram_tries, row.telegram_due_at = failed.tries, failed.due_at
    row.last_error = f"{prefix}: {failed.error(exc)}"
    tail = "" if group is None else f"; {group}"
    if failed.due_at is not None:
        then = failed.due_at.isoformat()
        return f"повтор не раньше {then}, попытка {failed.tries} из {MESSAGE_TRIES}"
    if failed.spent is not None:
        await deps.alert(
            marked(
                f"{headline(row)}: {what} за {failed.spent} попыток — повторов больше нет: "
                f"{exc}{tail}",
                CONCERNS[what],
            )
        )
        return f"попытки кончились ({failed.spent})"
    if failed.alarm:
        await deps.alert(marked(f"{headline(row)}: {what} — {exc}{tail}", CONCERNS[what]))
    return "без повтора"


def _settled(row: SalesHandoffModel) -> None:
    """Повторять нечего: ушло, отказ постоянный или попытки кончились."""
    row.telegram_tries, row.telegram_due_at = 0, None
