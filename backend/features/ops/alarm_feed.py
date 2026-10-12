"""Тревоги сторожа — человеку в Telegram, по смене состояния (Ф4, срез 4.5b).

Тревоги сторожа тишины шли в журнал и на экран (`GET /api/watchdog`), но не в Telegram.
Теперь каждая новая и каждая прошедшая — одно сообщение общим каналом (`shared/alerts.py`,
`ALERT_TELEGRAM_*`), как у `scripts/healthwatch.sh`: о разнице с прошлым проходом, а не на
каждом — иначе не читают ни одного. **Бот не настроен** — громкая строка «ТРЕВОГА НЕ
ОТПРАВЛЕНА» в журнале, тоже по смене состояния. **Telegram не принял** — следующий проход
скажет ещё раз.

**Дребезг.** «Тревога» — сразу, «прошло» — когда тревоги нет `QUIET_PASSES` проходов подряд:
тревога, которая мигает через проход (ящик на часовом потолке добивок), — одно «тревога», а не
сообщение на каждом проходе. Вернулась раньше — счёт сначала, и ни одного сообщения.

**Сказанное переживает перезапуск.** Код тревоги и её заголовок лежат хэшем в Redis
(`TOLD_KEY`; reaper и так ходит в Redis за живостью задач): тревога, кончившаяся за выкаткой
процесса сторожа, получает своё «прошло», а действующая не приходит второй раз. Redis не
ответил — строка журнала и память процесса, как до хранилища: прочитать сказанное прежним
процессом лента попробует следующим проходом, записать своё — тоже.

**Сказанное — по ленте и по каналу.** Строка журнала — не сообщение в Telegram: у журнала и у
каждого чата свой хэш (`told_key`). Владелец задал бота или сменил чат и перезапустил процесс —
новый канал пуст, и действующие тревоги приходят туда один раз: включение бота — сводка «что
сломано сейчас». И у каждой ленты свой хэш, по её имени (`Feed.name`): в процессе разбора говорят
две — сторож (`workers/reaper.FEED`) и бот продаж (`sales/agent/notify.BOT_FEED`). С общим хэшем
одна брала тревоги другой за свои и через `QUIET_PASSES` проходов говорила «прошло» о тревоге,
которая идёт, а запись каждой стирала сказанное другой. Лента без имени (`UNNAMED`) в сервисе
одна, имена не повторяются — держит тест (`tests/test_alarm_feed.py`).

**Прежний общий хэш не читается** (`outreach:watch:told:<канал>`; решение Anthony 12.10.2026:
сервис ещё не работал в боевом режиме): ленты начинают со своего пустого хэша, и на первой
выкатке действующие тревоги придут один раз заново.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from redis import Redis
from redis.exceptions import RedisError

from backend.config import alerts as alerts_cfg
from backend.config import storage
from backend.features.ops.alarms import Alarm
from backend.shared.alerts import send_alert

logger = logging.getLogger(__name__)

#: Сколько проходов подряд тревоги нет, прежде чем сказать «прошло». Проход сторожа — раз
#: в десять минут (`workers/reaper.WATCHDOG_INTERVAL_SEC`): «прошло» — после 10–20 минут тишины.
QUIET_PASSES = 2

#: Где сказанное ждёт следующий процесс: хэш «код тревоги → заголовок» на ленту и канал —
#: `outreach:watch:told:<лента>:<чат Telegram>`, пока бот не задан — `…:<лента>:journal`
#: (`told_key`). Ключ без имени ленты — прежний общий хэш: его не читает никто.
TOLD_KEY = "outreach:watch:told"

#: Канал, пока бот не задан: слово ленты — громкая строка журнала (`shared/alerts.py`).
JOURNAL = "journal"

#: Имя ленты, которой его не дали (`Feed()`). Такая лента в сервисе одна: вторая без имени
#: делила бы с ней сказанное.
UNNAMED = "unnamed"

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


def told_key(name: str = UNNAMED) -> str:
    """Хэш сказанного ленты `name` для канала этого процесса: чат Telegram — или журнал, пока
    бот не задан. Канал — из настроек процесса: смена бота или чата идёт перезапуском, и новый
    канал начинает с пустого хэша."""
    return f"{TOLD_KEY}:{name}:{alerts_cfg.TELEGRAM_CHAT_ID if _bot_set() else JOURNAL}"


@dataclass
class Feed:
    """Тревоги, о которых уже сказано: код → заголовок; в Redis — зеркало для перезапуска.

    Своё имя — своё сказанное (`told_key`): две ленты с одним именем делили бы хэш, и одна
    говорила бы «прошло» о тревогах другой, которые идут."""

    #: Назначение ленты — часть ключа её хэша. Без имени — `UNNAMED`.
    name: str = UNNAMED
    told: dict[str, str] = field(default_factory=dict)
    #: Сколько проходов подряд сказанной тревоги нет (в памяти: перезапуск начинает счёт заново).
    quiet: dict[str, int] = field(default_factory=dict)
    #: Прочитано ли сказанное прежним процессом. До того зеркало не пишется: затёрло бы его.
    recalled: bool = False
    #: Память разошлась с Redis (запись не удалась или ещё не читали) — дописать.
    unsaved: bool = False
    #: Запись в этом проходе не удалась — до его конца Redis не ждём: дописывает следующий проход.
    stalled: bool = False

    async def tell(self, found: Sequence[Alarm]) -> None:
        """Сказать о новых тревогах сразу и о прошедших — после `QUIET_PASSES` проходов без них."""
        self.stalled = False
        self._recall()
        now = {alarm.code: alarm for alarm in found}
        await self._new(now)
        await self._gone(now)

    async def _new(self, now: Mapping[str, Alarm]) -> None:
        """Новая тревога — сразу; действующая начинает счёт тишины заново."""
        for code, alarm in now.items():
            self.quiet.pop(code, None)
            if code not in self.told and await _said(f"тревога: {alarm.title}. {alarm.detail}"):
                self.told[code] = alarm.title
                self._save()

    async def _gone(self, now: Mapping[str, Alarm]) -> None:
        """Прошедшая — когда её нет `QUIET_PASSES` проходов подряд."""
        for code in [code for code in self.told if code not in now]:
            self.quiet[code] = self.quiet.get(code, 0) + 1
            if self.quiet[code] >= QUIET_PASSES and await _said(f"прошло: {self.told[code]}"):
                del self.told[code], self.quiet[code]
                self._save()

    def _recall(self) -> None:
        """Сказанное прежним процессом — один раз, когда Redis ответит; сказанное этим
        процессом до того — поверх (оно новее). Разошлось — дописать зеркало."""
        if not self.recalled:
            stored = _stored(told_key(self.name))
            if stored is None:
                return
            self.told = {**stored, **self.told}
            self.recalled = True
        if self.unsaved:
            self._save()

    def _save(self) -> None:
        """Зеркало памяти — в Redis. Не записалось — до конца прохода больше не пробовать
        (зависший Redis держал бы по таймауту каждое слово), дописать в начале следующего."""
        self.unsaved = True
        if self.recalled and not self.stalled:
            self.unsaved = self.stalled = not _stored_as(told_key(self.name), self.told)


def _stored(key: str) -> dict[str, str] | None:
    """Сказанное из хэша `key` в Redis. `None` — Redis не ответил (строка журнала)."""
    try:
        with connection() as redis:
            raw = redis.hgetall(key)
    except RedisError as exc:
        logger.warning(
            "лента тревог: сказанное из Redis не прочитано (%s) — помню только этот процесс, "
            "повтор следующим проходом",
            exc,
            extra={"key": key},
        )
        return None
    pairs = raw.items() if isinstance(raw, dict) else ()
    return {_text(code): _text(title) for code, title in pairs}


def _stored_as(key: str, told: dict[str, str]) -> bool:
    """Записать сказанное в хэш `key` целиком (одной транзакцией Redis). `False` — не записалось."""
    try:
        with connection() as redis, redis.pipeline() as pipe:
            pipe.delete(key)
            if told:
                pipe.hset(key, mapping=told)
            pipe.execute()
    except RedisError as exc:
        logger.warning(
            "лента тревог: сказанное в Redis не записано (%s) — повтор следующим проходом",
            exc,
            extra={"key": key},
        )
        return False
    return True


def _text(raw: object) -> str:
    """Строка из ответа Redis. Чужое значение не UTF-8 — со знаками замены: `UnicodeDecodeError`
    не `RedisError` и ронял бы каждый проход сторожа."""
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


async def _said(text: str) -> bool:
    """Ушло ли слово: в Telegram — или громкой строкой журнала, если бот не настроен."""
    sent = await send_alert(text)
    return sent or not _bot_set()


def _bot_set() -> bool:
    return bool(alerts_cfg.TELEGRAM_BOT_TOKEN and alerts_cfg.TELEGRAM_CHAT_ID)
