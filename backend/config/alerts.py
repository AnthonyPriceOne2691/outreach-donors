"""Тревоги человеку: куда слать, когда что-то кончилось плохо.

Канал один — бот Telegram: сообщение доходит до телефона за секунды,
а журнал и экран читают, только когда уже что-то ищут.

Пустые значения — не ошибка конфига: сервис работает и без тревог, но
каждая несостоявшаяся тревога говорит об этом строкой в журнале
(`shared/alerts.py`). Падать при старте из-за бота было бы хуже: прогоны
встали бы из-за канала, который о них только сообщает.

Те же имена читают кроны хоста (`scripts/alert.sh`) — из того же `.env`
рядом с компоузом: одно значение на двоих читателей, а не две копии
токена, которые однажды разойдутся.
"""

from __future__ import annotations

from pydantic import Field

from backend.config._base import DomainSettings


class _Alerts(DomainSettings):
    # Токен бота от @BotFather. Секрет: в `.env`, не в образце.
    telegram_bot_token: str = Field(default="", validation_alias="ALERT_TELEGRAM_BOT_TOKEN")
    # Куда слать: личный чат с ботом или группа, куда бот добавлен.
    telegram_chat_id: str = Field(default="", validation_alias="ALERT_TELEGRAM_CHAT_ID")
    # Адрес Bot API. Меняется только для проверки подставным сервером:
    # из проверки настоящие тревоги не уходят.
    telegram_api: str = Field(
        default="https://api.telegram.org", validation_alias="ALERT_TELEGRAM_API"
    )


_s = _Alerts()

TELEGRAM_BOT_TOKEN: str = _s.telegram_bot_token.strip()
TELEGRAM_CHAT_ID: str = _s.telegram_chat_id.strip()
TELEGRAM_API: str = _s.telegram_api.strip().rstrip("/")

#: Сколько ждать Telegram. Тревогу шлёт тот, у кого уже что-то сломалось
#: (закрытие прогона, разбор мёртвых), и держать его дольше незачем:
#: не дождались — строка в журнал, работа идёт дальше.
TIMEOUT_SEC = 10.0
