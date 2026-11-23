"""Докуда сторож входов и прав прочитал журнал: курсор и сказанное в окне сверки.

Курсор — номер записи журнала, до которого о событиях доступа сказано всё (`access_events.read`
читает выше него и в окне сверки). Сказанное — номера записей, о которых уже ушло сообщение: окно
сверки читает их снова, а второй раз они не приходят. Аудит 20.11.2026: процесс разбора, где живёт
сторож, перезапускается каждой выкаткой, и без памяти событие либо пропало бы в зазоре, либо
пришло бы второй раз. Поэтому память — в Redis, как сказанное лентами тревог (`alarm_feed.py`), и
так же по каналу: строка журнала — не сообщение в Telegram. События входа с кодом (23.11.2026)
читаются той же пачкой (`access_events.read`), и память у них общая с событиями доступа: номер
записи журнала один на всех.

- **Telegram не принял** — курсор встаёт перед первым несказанным событием, следующий проход
  скажет его ещё раз. Сказанное другим сообщением того же прохода второй раз не придёт: его номера
  уже в памяти.
- **Бот не настроен** — громкая строка «ТРЕВОГА НЕ ОТПРАВЛЕНА» (`send_alert`) и есть сказанное.
- **Redis не ответил** — память процесса; прочитать прежнее и записать своё — следующим
  проходом. Пока прежнее не прочитано, своё не пишется (затёрло бы его) и не забывается: прежний
  курсор может оказаться ниже, и перечитанное между ними не должно прийти второй раз.
- **Прежнего нет** (первый запуск, новый канал, Redis потерял данные) — курсора нет, и журнал
  читается с последней записи и окна сверки: о последних минутах сторож скажет, историю — нет.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from redis.exceptions import RedisError

from backend.config import alerts as alerts_cfg
from backend.features.ops import alarm_feed
from backend.features.ops.access_event_words import Notice, notices
from backend.features.ops.access_events import LOOKBACK, Batch
from backend.features.ops.alarms import marked
from backend.shared.alerts import send_alert

logger = logging.getLogger(__name__)

#: Где память ждёт следующий процесс: хэш «cursor» и «told» — `outreach:watch:access:<канал>`,
#: канал — чат Telegram или `journal`, пока бот не задан (как у лент, `alarm_feed.told_key`).
KEY = "outreach:watch:access"


@dataclass
class Mark:
    """Курсор журнала и сказанное в окне сверки; в Redis — зеркало для перезапуска."""

    #: Номер записи, до которого сказано всё; `None` — не знаем (читать с последней записи).
    cursor: int | None = None
    #: Сказанные записи: номер → время записи (по нему они уходят из окна сверки).
    told: dict[int, datetime] = field(default_factory=dict)
    #: Прочитано ли прежнее (Redis ответил). До того зеркало не пишется: затёрло бы его.
    recalled: bool = False
    #: Память разошлась с Redis — дописать.
    unsaved: bool = False

    def recall(self) -> None:
        """Прежнее — один раз, когда Redis ответит. Курсор — меньший из двух: перечитать лишнее
        безопаснее, чем пропустить, а сказанное обоими процессами в памяти — оно не повторится.
        Своё, накопленное без Redis, запишет конец прохода (`unsaved` стоит до удачной записи):
        одна запись на проход — и один таймаут, если Redis завис."""
        if self.recalled:
            return
        stored = _stored()
        if stored is None:
            return
        cursor, told = stored
        if cursor is not None:
            self.cursor = cursor if self.cursor is None else min(cursor, self.cursor)
        self.told, self.recalled = {**told, **self.told}, True

    async def tell(self, batch: Batch, at: datetime) -> None:
        """Сказать о событиях прохода. Сказанное — в память сразу, сообщением: другое сообщение
        прохода, которое Telegram не принял, этого не тянет. Курсор — до `batch.reach`, если сказано
        всё (`_moved`)."""
        unsaid: list[int] = []
        for notice in notices(batch.events, at):
            if await _said(notice):
                self.told.update({event.id: event.at for event in notice.events})
                self.unsaved = True
            else:
                unsaid += [event.id for event in notice.events]
        moved = self._moved(batch.reach, unsaid)
        if moved is not None and moved != self.cursor:
            self.cursor, self.unsaved = moved, True
        self._forget(at)
        if self.unsaved:
            self._save()

    def _moved(self, reach: int | None, unsaid: list[int]) -> int | None:
        """Куда курсору после прохода. Сказано всё — до `reach`, хоть и назад: курсор дальше
        журнала значит базу из копии (`access_events.read`). Сказано не всё — перед первым
        несказанным: оно прочитается снова и тогда, когда выйдет из окна сверки. Но не назад от
        прежнего курсора: сказанное под ним уже забыто по окну и пришло бы второй раз."""
        if not unsaid:
            return reach
        below = min(unsaid) - 1
        return below if self.cursor is None else max(self.cursor, below)

    def _forget(self, at: datetime) -> None:
        """Сказанное, которое курсор прошёл и окно сверки больше не читает, — забыть. Пока прежнее
        не прочитано — помнить всё (`recall`)."""
        if not self.recalled or self.cursor is None:
            return
        edge, cursor = at - LOOKBACK, self.cursor
        kept = {
            number: when for number, when in self.told.items() if number > cursor or when >= edge
        }
        if len(kept) != len(self.told):
            self.told, self.unsaved = kept, True

    def _save(self) -> None:
        """Зеркало — в Redis. Не записалось — дописать следующим проходом."""
        if self.recalled:
            self.unsaved = not _stored_as(self.cursor, self.told)


async def _said(notice: Notice) -> bool:
    """Ушло ли сообщение: в Telegram — или громкой строкой журнала, если бот не настроен."""
    sent = await send_alert(marked(notice.text, notice.concern))
    return sent or not _bot_set()


def _bot_set() -> bool:
    return bool(alerts_cfg.TELEGRAM_BOT_TOKEN and alerts_cfg.TELEGRAM_CHAT_ID)


def key() -> str:
    """Хэш памяти для канала этого процесса: смена бота или чата идёт перезапуском, и новый
    канал начинает с пустой памяти."""
    return f"{KEY}:{alerts_cfg.TELEGRAM_CHAT_ID if _bot_set() else alarm_feed.JOURNAL}"


def _stored() -> tuple[int | None, dict[int, datetime]] | None:
    """Курсор и сказанное из Redis. `None` — Redis не ответил (строка журнала)."""
    try:
        with alarm_feed.connection() as redis:
            raw = redis.hgetall(key())
    except RedisError as exc:
        logger.warning(
            "сторож входов и прав: память из Redis не прочитана (%s) — помню только этот процесс, "
            "повтор следующим проходом",
            exc,
            extra={"key": key()},
        )
        return None
    fields = {_text(name): _text(value) for name, value in _pairs(raw)}
    return _cursor(fields.get("cursor")), _told(fields.get("told"))


def _stored_as(cursor: int | None, told: dict[int, datetime]) -> bool:
    """Записать память целиком (одной транзакцией Redis). `False` — не записалась."""
    mapping = {"told": json.dumps({str(number): when.isoformat() for number, when in told.items()})}
    if cursor is not None:
        mapping["cursor"] = str(cursor)
    try:
        with alarm_feed.connection() as redis, redis.pipeline() as pipe:
            pipe.delete(key())
            pipe.hset(key(), mapping=mapping)
            pipe.execute()
    except RedisError as exc:
        logger.warning(
            "сторож входов и прав: память в Redis не записана (%s) — повтор следующим проходом",
            exc,
            extra={"key": key()},
        )
        return False
    return True


def _pairs(raw: object) -> list[tuple[object, object]]:
    """Пары ответа `hgetall`; не словарь — ни одной."""
    return list(raw.items()) if isinstance(raw, dict) else []


def _text(raw: object) -> str:
    """Строка из ответа Redis; чужие байты — со знаками замены, а не падение прохода."""
    return raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)


def _cursor(raw: str | None) -> int | None:
    """Курсор из хэша; нет его или он битый — не знаем (строка журнала о битом)."""
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "сторож входов и прав: курсор в Redis не число (%s) — читаю с конца", raw[:40]
        )
        return None


def _told(raw: str | None) -> dict[int, datetime]:
    """Сказанное из хэша; битое — пусто и строка журнала: окно сверки скажет последнее ещё раз,
    а проход не упадёт."""
    if not raw:
        return {}
    try:
        found = json.loads(raw)
        return {int(number): _aware(datetime.fromisoformat(when)) for number, when in found.items()}
    except (ValueError, TypeError, AttributeError) as exc:
        logger.warning("сторож входов и прав: сказанное в Redis не разобрано (%s) — пусто", exc)
        return {}


def _aware(moment: datetime) -> datetime:
    """Время без пояса — UTC: сравнивается с временем записей журнала, а оно с поясом."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
