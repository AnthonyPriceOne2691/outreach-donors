"""Бот продаж в Telegram: сообщение о лиде телемаркетологу и копия в группу (срез 5.3).

**Свой бот, а не бот тревог** (`config/sales.py`): сообщение о лиде — бизнес-событие
для сотрудника, и его недоставка — повод для тревоги владельцу, которая уходит
другим ботом. Общий бот замолчал бы целиком вместе с отозванным токеном.

**Три попытки.** Обрыв, таймаут, 429 и 5xx повторяются с паузами `PAUSES_SEC`;
паузу 429 называет сам Telegram (`parameters.retry_after`), и дольше `MAX_WAIT_SEC`
задача не спит — отказ называет величину. 400, 401, 403, 404 — сразу отказ с советом:
чат не тот, токен отозван, бот заблокирован — повтор этого не изменит. Верится полю
`ok` ответа, а не коду: прокси умеет ответить 200 своей страницей.

**Токен — только в адресе Bot API** (`/bot<токен>/…`), а httpx пишет адрес каждого
запроса в журнал на уровне INFO. Фильтр на логгере httpx прячет токен бота продаж
(токен бота тревог прячет `shared/alerts.py`); свои тексты ошибок собираются без
адреса и без текста исключения httpx — в нём адрес.

**Чат телемаркетолога** узнаётся командой `outreach sales-telegram-chat-id`: бот не
может написать первым, поэтому человек один раз жмёт Start, а команда читает
`getUpdates` один раз и печатает номера чатов.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from backend.config import alerts as alerts_cfg
from backend.config import sales as cfg

logger = logging.getLogger(__name__)

#: Сколько раз пробуем отправить одно сообщение (Spec 5.3, A4).
ATTEMPTS = 3
#: Паузы перед второй и третьей попыткой: мигнувшая сеть и перезапуск прокси.
PAUSES_SEC = (2.0, 5.0)
#: Дольше этого по просьбе Telegram (429) не ждём: задача очереди не спит минутами.
MAX_WAIT_SEC = 30.0
#: Потолок Bot API на длину сообщения.
MAX_CHARS = 4096
#: Чем заменяется токен во всём, что уходит в журнал.
HIDDEN = "<токен бота продаж>"

#: Отдельным именем — тест гасит паузу здесь, а не `asyncio.sleep` всего процесса.
_sleep = asyncio.sleep

#: Что делать человеку, когда Telegram отказал, — по коду ответа.
_ADVICE = {
    400: "проверить chat id в SALES_TELEGRAM_CHAT_ID и SALES_TELEGRAM_GROUP_CHAT_ID — "
    "чат не найден или бот в него не добавлен; номер печатает `outreach sales-telegram-chat-id`",
    401: "токен бота неверен или отозван — выпустить новый у @BotFather "
    "и заменить SALES_TELEGRAM_BOT_TOKEN",
    403: "бот заблокирован или удалён из чата — телемаркетологу нажать Start заново, "
    "бота вернуть в группу",
    404: "токен бота неверен — выпустить новый у @BotFather и заменить SALES_TELEGRAM_BOT_TOKEN",
    409: "у бота включён вебхук, и getUpdates с ним не работает — снять его методом deleteWebhook",
}


class TelegramError(RuntimeError):
    """Telegram не принял запрос. Текст — словами, без адреса и токена.

    `permanent` — повтор не поможет (чат, токен, блокировка); иначе — сеть или
    перегрузка. `retry_after` — сколько просил подождать сам Telegram (429)."""

    def __init__(self, message: str, *, permanent: bool, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


@dataclass(frozen=True, slots=True)
class Chat:
    """Чат, в котором боту нажали Start или куда бота добавили."""

    id: int
    #: `private`, `group`, `supergroup` или `channel` — как называет Telegram.
    kind: str
    #: Имя человека или название группы — как их отдал Telegram.
    title: str


def hidden(text: str) -> str:
    """Текст без токена бота продаж. Всё, что уходит в журнал и в ошибки, — через него."""
    token = cfg.TELEGRAM_BOT_TOKEN
    return text.replace(token, HIDDEN) if token else text


class _HideSalesToken(logging.Filter):
    """Прячет токен бота продаж в строке httpx о запросе. Сообщение собирается
    целиком и заменяется готовой строкой: аргументы других типов не страдают —
    строка с токеном всё равно только одна, адрес."""

    def filter(self, record: logging.LogRecord) -> bool:
        token = cfg.TELEGRAM_BOT_TOKEN
        if token and token in (message := record.getMessage()):
            record.msg, record.args = hidden(message), None
        return True


logging.getLogger("httpx").addFilter(_HideSalesToken())


class SalesBot:
    """Бот продаж поверх общего клиента httpx: клиент закрывает тот, кто его открыл."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self._http = http

    async def send(self, chat_id: str, text: str) -> None:
        """Отправить сообщение: до `ATTEMPTS` попыток, отказ — `TelegramError`."""
        if not chat_id:
            raise TelegramError(
                "чат не задан — заполнить SALES_TELEGRAM_CHAT_ID (номер печатает "
                "`outreach sales-telegram-chat-id`)",
                permanent=True,
            )
        payload = {"chat_id": chat_id, "text": text[:MAX_CHARS], "disable_web_page_preview": True}
        attempt = 1
        while True:
            try:
                await self._call("sendMessage", payload)
            except TelegramError as exc:
                wait = None if attempt == ATTEMPTS else _wait_before_next(exc, attempt)
                if wait is None:
                    raise _final(exc, attempt) from None
                logger.warning(
                    "бот продаж: повтор сообщения",
                    extra={"why": str(exc), "pause_sec": wait, "attempt": attempt},
                )
                await _sleep(wait)
                attempt += 1
            else:
                return

    async def chats(self) -> list[Chat]:
        """Чаты из `getUpdates` — один запрос, без повторов: это команда человека."""
        result = await self._call("getUpdates", {"timeout": 0})
        if not isinstance(result, list):
            raise TelegramError(
                f"getUpdates вернул не список: {hidden(str(result))[:120]!r} — формат "
                "поменялся, читать лог, не гадать",
                permanent=True,
            )
        found: dict[int, Chat] = {}
        for update in result:
            chat = _chat_of(update)
            if chat is not None:
                found.setdefault(chat.id, chat)
        return list(found.values())

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        """Один запрос к Bot API. Ответ `ok` — его `result`; иначе — `TelegramError`."""
        if not cfg.TELEGRAM_BOT_TOKEN:
            raise TelegramError(
                "бот продаж не настроен — не задан SALES_TELEGRAM_BOT_TOKEN (.env рядом "
                "с компоузом)",
                permanent=True,
            )
        url = f"{alerts_cfg.TELEGRAM_API}/bot{cfg.TELEGRAM_BOT_TOKEN}/{method}"
        try:
            response = await self._http.post(url, json=payload, timeout=cfg.TELEGRAM_TIMEOUT_SEC)
        except httpx.InvalidURL:
            # Не `HTTPError`: адрес не собрать — в токене или адресе Bot API непечатный знак.
            # Мимо отказов бота задача падала бы по кругу прохода без «не доставлено» и
            # тревоги. Текст httpx не берём: он о позиции знака в адресе с токеном.
            raise TelegramError(
                "адрес Bot API не собрать — непечатный знак (табуляция, перевод строки) "
                "в SALES_TELEGRAM_BOT_TOKEN или ALERT_TELEGRAM_API: заменить значение",
                permanent=True,
            ) from None
        except httpx.HTTPError as exc:
            # Только тип: в тексте исключения httpx адрес, а в адресе — токен.
            raise TelegramError(
                f"Telegram не ответил ({type(exc).__name__}) — сеть или Bot API недоступны",
                permanent=False,
            ) from None
        return _result(response)


def _result(response: httpx.Response) -> Any:
    """Ответ Bot API → его `result` или `TelegramError` со словами и советом."""
    try:
        answer = response.json()
    except ValueError:
        logger.debug("бот продаж: ответ не JSON", extra={"status": response.status_code})
        answer = None
    code = response.status_code
    if not isinstance(answer, dict):
        raise TelegramError(
            f"ответ не похож на Bot API (HTTP {code}: {hidden(response.text[:120])!r}) — "
            "проверить ALERT_TELEGRAM_API и прокси машины",
            permanent=code < 500,
        )
    if answer.get("ok") is True:
        return answer.get("result")
    said = hidden(str(answer.get("description") or ""))[:200]
    advice = _ADVICE.get(code, "повтор позже")
    temporary = code == httpx.codes.TOO_MANY_REQUESTS or code >= 500
    raise TelegramError(
        f"Telegram отказал (HTTP {code}: {said}) — {advice}",
        permanent=not temporary,
        retry_after=_asked_wait(answer),
    )


def _asked_wait(answer: dict[str, Any]) -> float | None:
    parameters = answer.get("parameters")
    value = parameters.get("retry_after") if isinstance(parameters, dict) else None
    if isinstance(value, int | float) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def _wait_before_next(exc: TelegramError, attempt: int) -> float | None:
    """Пауза перед следующей попыткой; `None` — не повторяем."""
    if exc.permanent:
        return None
    if exc.retry_after is None:
        return PAUSES_SEC[attempt - 1]
    return exc.retry_after if exc.retry_after <= MAX_WAIT_SEC else None


def _final(exc: TelegramError, attempt: int) -> TelegramError:
    """Итоговый отказ: сколько попыток было и почему перестали."""
    if exc.permanent:
        return exc
    if exc.retry_after is not None and exc.retry_after > MAX_WAIT_SEC:
        why = f"Telegram просит ждать {exc.retry_after:g} с — дольше {MAX_WAIT_SEC:g} с не ждём"
        return TelegramError(f"{exc}; {why}", permanent=False, retry_after=exc.retry_after)
    return TelegramError(f"не доставлено за {attempt} попытки: {exc}", permanent=False)


def _chat_of(update: object) -> Chat | None:
    """Чат из обновления: Start в личке, сообщение или добавление бота в группу."""
    for key in ("message", "edited_message", "my_chat_member", "channel_post"):
        part = update.get(key) if isinstance(update, dict) else None
        chat = part.get("chat") if isinstance(part, dict) else None
        number = chat.get("id") if isinstance(chat, dict) else None
        if isinstance(chat, dict) and isinstance(number, int) and not isinstance(number, bool):
            person = " ".join(str(chat[k]) for k in ("first_name", "last_name") if chat.get(k))
            title = chat.get("title") or person or chat.get("username") or ""
            return Chat(number, str(chat.get("type") or ""), str(title))
    return None
