"""Тревоги сторожа — человеку в Telegram, по смене состояния (Ф4, срез 4.5b).

Тревоги сторожа тишины шли в журнал и на экран (`GET /api/watchdog`), но не в Telegram.
Теперь каждая новая и каждая прошедшая — одно сообщение общим каналом (`shared/alerts.py`,
`ALERT_TELEGRAM_*`), как у `scripts/healthwatch.sh`: о разнице с прошлым проходом, а не на
каждом — иначе не читают ни одного. **Бот не настроен** — громкая строка «ТРЕВОГА НЕ
ОТПРАВЛЕНА» в журнале, тоже по смене состояния. **Telegram не принял** — следующий проход
скажет ещё раз.

**Сказанное переживает перезапуск.** Код тревоги и её заголовок лежат хэшем в Redis
(`TOLD_KEY`; reaper и так ходит в Redis за живостью задач): тревога, кончившаяся за выкаткой
процесса сторожа, получает своё «прошло», а действующая не приходит второй раз. Redis не
ответил — строка журнала и память процесса, как до хранилища: прочитать сказанное прежним
процессом лента попробует следующим проходом, записать своё — тоже.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from redis import Redis
from redis.exceptions import RedisError

from backend.config import alerts as alerts_cfg
from backend.config import storage
from backend.features.ops.alarms import Alarm
from backend.shared.alerts import send_alert

logger = logging.getLogger(__name__)

#: Где сказанное ждёт следующий процесс сторожа: хэш «код тревоги → заголовок».
TOLD_KEY = "outreach:watch:told"

#: Сколько ждать Redis. Сторож живёт в одном процессе с разбором прогонов, клиент Redis
#: синхронный: зависший Redis не должен держать и их.
REDIS_TIMEOUT_SEC = 5.0


def connection() -> Redis:
    """Клиент Redis на одно обращение, с таймаутами. Отдельной функцией — её подменяют
    тесты (`tests/conftest.py`): сказанное из теста в общий Redis не уходит."""
    return Redis.from_url(
        storage.REDIS_URL,
        socket_connect_timeout=REDIS_TIMEOUT_SEC,
        socket_timeout=REDIS_TIMEOUT_SEC,
    )


@dataclass
class Feed:
    """Тревоги, о которых уже сказано: код → заголовок; в Redis — зеркало для перезапуска."""

    told: dict[str, str] = field(default_factory=dict)
    #: Прочитано ли сказанное прежним процессом. До того зеркало не пишется: затёрло бы его.
    recalled: bool = False
    #: Память разошлась с Redis (запись не удалась или ещё не читали) — дописать.
    unsaved: bool = False

    async def tell(self, found: Sequence[Alarm]) -> None:
        """Сказать о новых тревогах и о прошедших — по разнице с прошлым проходом."""
        self._recall()
        now = {alarm.code: alarm for alarm in found}
        for code, alarm in now.items():
            if code not in self.told and await _said(f"тревога: {alarm.title}. {alarm.detail}"):
                self.told[code] = alarm.title
                self._save()
        for code in [code for code in self.told if code not in now]:
            if await _said(f"прошло: {self.told[code]}"):
                del self.told[code]
                self._save()

    def _recall(self) -> None:
        """Сказанное прежним процессом — один раз, когда Redis ответит; сказанное этим
        процессом до того — поверх (оно новее). Разошлось — дописать зеркало."""
        if not self.recalled:
            stored = _stored()
            if stored is None:
                return
            self.told = {**stored, **self.told}
            self.recalled = True
        if self.unsaved:
            self._save()

    def _save(self) -> None:
        """Зеркало памяти — в Redis. Не записалось — дописать в начале следующего прохода."""
        self.unsaved = not (self.recalled and _stored_as(self.told))


def _stored() -> dict[str, str] | None:
    """Сказанное из Redis. `None` — Redis не ответил (строка журнала)."""
    try:
        with connection() as redis:
            raw = redis.hgetall(TOLD_KEY)
    except RedisError as exc:
        logger.warning(
            "лента тревог: сказанное из Redis не прочитано (%s) — помню только этот процесс, "
            "повтор следующим проходом",
            exc,
        )
        return None
    pairs = raw.items() if isinstance(raw, dict) else ()
    return {_text(code): _text(title) for code, title in pairs}


def _stored_as(told: dict[str, str]) -> bool:
    """Записать сказанное целиком (одной транзакцией Redis). `False` — не записалось."""
    try:
        with connection() as redis, redis.pipeline() as pipe:
            pipe.delete(TOLD_KEY)
            if told:
                pipe.hset(TOLD_KEY, mapping=told)
            pipe.execute()
    except RedisError as exc:
        logger.warning(
            "лента тревог: сказанное в Redis не записано (%s) — повтор следующим проходом", exc
        )
        return False
    return True


def _text(raw: object) -> str:
    return raw.decode("utf-8") if isinstance(raw, bytes) else str(raw)


async def _said(text: str) -> bool:
    """Ушло ли слово: в Telegram — или громкой строкой журнала, если бот не настроен."""
    sent = await send_alert(text)
    return sent or not (alerts_cfg.TELEGRAM_BOT_TOKEN and alerts_cfg.TELEGRAM_CHAT_ID)
