"""Тревога человеку в Telegram: одна строка, когда что-то кончилось плохо.

Журнал и экран читают, когда уже что-то ищут. Прогон, остановленный
ночью, стоял бы остановленным до утра, и узнавали бы об этом по пустому
экрану. Тревога — единственный канал, который приходит сам.

**Тревога не роняет того, кто её шлёт.** Её зовут оттуда, где что-то уже
сломалось (закрытие прогона, разбор мёртвых), и исключение отсюда
подменило бы настоящую причину своей. Не настроено, нет сети, Telegram
отказал — строка уровня ERROR с тем, что делать, и `False` в ответ.

**Токен не попадает в журнал.** Bot API принимает его только в адресе
(`/bot<токен>/sendMessage`), а httpx пишет адрес каждого запроса
в журнал на уровне INFO. Фильтр на логгере httpx прячет токен в его
строках, а свои строки мы собираем без адреса и без трассировки: в тексте
исключения тоже бывает адрес.

Кроны хоста шлют то же самое из шелла — `scripts/alert.sh`: тревога
о бэкапе и о контейнерах должна уходить и тогда, когда контейнеры лежат.

Проверить настройку на сервере — одной строкой:
`docker compose exec api python -m backend.shared.alerts "проверка"`.
"""

from __future__ import annotations

import asyncio
import logging
import sys

import httpx

from backend.config import alerts as cfg
from backend.shared.logs import setup_logging

logger = logging.getLogger(__name__)

#: Чем начинается каждая тревога. Бот и чат бывают общими у нескольких
#: сервисов машины, и «прогон №22 остановлен» без имени — чей прогон?
SOURCE = "outreach-donors"

#: Потолок Bot API на длину сообщения. Длиннее — Telegram откажет целиком,
#: и тревога не уйдёт вовсе из-за собственной подробности.
MAX_CHARS = 4096

#: Чем заменяется токен во всём, что уходит в журнал.
HIDDEN = "<токен>"

#: Что делать, когда Telegram отказал, — по коду ответа. Отказ без совета
#: заставляет гадать, а гадать приходится в тот день, когда тревоги нужны.
_ADVICE = {
    400: "проверить ALERT_TELEGRAM_CHAT_ID: чат не найден или бот в него не добавлен",
    401: "токен неверен или отозван — выпустить новый у @BotFather и заменить в .env",
    403: "бот удалён из чата или заблокирован — вернуть его в чат",
    404: "токен неверен — выпустить новый у @BotFather и заменить в .env",
    429: "Telegram просит подождать: тревог слишком много подряд",
}


def hidden(text: str) -> str:
    """Текст без токена. Всё, что уходит в журнал, проходит здесь."""
    token = cfg.TELEGRAM_BOT_TOKEN
    return text.replace(token, HIDDEN) if token else text


class _HideToken(logging.Filter):
    """Прячет токен в строках httpx о запросе.

    Фильтр на логгере, а не на обработчике: обработчики у сервиса, у тестов
    и у pytest свои, а запись логгера `httpx` проходит этот фильтр раньше
    любого из них. Переписываются только аргументы с токеном — остальные
    (код ответа печатается как `%d`) остаются своего типа.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        token = cfg.TELEGRAM_BOT_TOKEN
        if not token:
            return True
        if isinstance(record.msg, str) and token in record.msg:
            record.msg = hidden(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                hidden(str(arg)) if token in str(arg) else arg for arg in record.args
            )
        return True


_HIDE_TOKEN = _HideToken()
logging.getLogger("httpx").addFilter(_HIDE_TOKEN)


def _client() -> httpx.AsyncClient:
    """Клиент на одну тревогу. Отдельной функцией — её подменяет тест:
    сеть в тестах не ходит, а путь до неё проверяется целиком."""
    return httpx.AsyncClient(timeout=cfg.TIMEOUT_SEC)


async def send_alert(text: str) -> bool:
    """Отправить тревогу. `True` — Telegram её принял.

    Не бросает ничего, кроме отмены: всё остальное — строка в журнал
    уровня ERROR с текстом тревоги, чтобы событие не пропало вместе с ней.
    """
    message = f"{SOURCE}: {text}"[:MAX_CHARS]
    if not cfg.TELEGRAM_BOT_TOKEN or not cfg.TELEGRAM_CHAT_ID:
        logger.error(
            "ТРЕВОГА НЕ ОТПРАВЛЕНА — не заданы ALERT_TELEGRAM_BOT_TOKEN и ALERT_TELEGRAM_CHAT_ID "
            "(.env рядом с компоузом, шаги — deploy/README.md): %s",
            message,
        )
        return False

    url = f"{cfg.TELEGRAM_API}/bot{cfg.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        async with _client() as client:
            response = await client.post(
                url, json={"chat_id": cfg.TELEGRAM_CHAT_ID, "text": message}
            )
    except Exception as exc:  # noqa: BLE001 — тревога не роняет того, кто её шлёт
        # Без трассировки намеренно: в тексте исключения httpx бывает адрес,
        # а в адресе — токен.
        logger.error(  # noqa: TRY400
            "ТРЕВОГА НЕ ОТПРАВЛЕНА — Telegram недоступен (%s: %s); повторить проверкой "
            "`python -m backend.shared.alerts`: %s",
            type(exc).__name__,
            hidden(str(exc)),
            message,
        )
        return False
    return _accepted(response, message)


def _accepted(response: httpx.Response, message: str) -> bool:
    """Принял ли Telegram сообщение. Отказ — громко и с советом.

    Верится полю `ok` ответа, а не коду: прокси перед Telegram умеет
    ответить 200 своей страницей, и такая тревога не дошла бы молча.
    """
    try:
        answer = response.json()
    except ValueError:
        # Не JSON — отвечал не Bot API: прокси, портал сети, чужой адрес.
        logger.error(  # noqa: TRY400 — трассировка разбора JSON здесь ничего не добавит
            "ТРЕВОГА НЕ ОТПРАВЛЕНА — ответ не похож на Bot API (%s: %s), "
            "проверить ALERT_TELEGRAM_API и прокси машины: %s",
            response.status_code,
            hidden(response.text[:200]),
            message,
        )
        return False
    if isinstance(answer, dict) and answer.get("ok") is True:
        return True
    why = answer.get("description") if isinstance(answer, dict) else str(answer)[:200]
    logger.error(
        "ТРЕВОГА НЕ ОТПРАВЛЕНА — Telegram отказал (%s: %s), %s: %s",
        response.status_code,
        hidden(str(why)),
        _ADVICE.get(response.status_code, "повторить проверкой `python -m backend.shared.alerts`"),
        message,
    )
    return False


def main() -> None:
    """`python -m backend.shared.alerts "текст"` — проверить бота из контейнера.

    Тот же путь, что у тревоги об остановленном прогоне: настройки из `.env`
    контейнера, тот же клиент. Код выхода 0 — сообщение в чате.
    """
    setup_logging()
    text = " ".join(sys.argv[1:]) or "проверка тревоги"
    sent = asyncio.run(send_alert(text))
    if sent:
        logger.info("тревога ушла: %s", text)
    sys.exit(0 if sent else 1)


if __name__ == "__main__":
    main()
