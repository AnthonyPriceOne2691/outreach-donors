"""Повтор недоставленного сообщения бота продаж проходом по расписанию — одно правило у
сообщения о лиде (`handoff_telegram.py`) и сообщения о черновике агента (`agent/notify.py`).

**Серия.** Бот сам пробует трижды за задачу (`telegram.py`): это мигнувшая сеть. Не прошло из-за
сети, 5xx или 429 — сообщение ждёт повтора прохода по расписанию: пауза растёт вдвое от
`FIRST_PAUSE_SEC` (5, 10, 20, 40 мин), у 429 — не меньше паузы, которую назвал Telegram.
Попыток — задача и повторы — `MESSAGE_TRIES`; после последней — тревога словами, повторов
больше нет.

**Постоянный отказ** (нет токена или чата, чат не найден, бот заблокирован, непечатный знак в
токене) не повторяется: повтор его не изменит, тревога — сразу.

**Итог сказан — дальше молча** (`told`): о сообщении, по которому тревога уже ушла, задача по
другому поводу пробует ещё раз, но при отказе тревоги не повторяет и новой серии не заводит:
попыток по расписанию — конечное число.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from backend.features.sales.telegram import TelegramError

#: Сколько раз одно сообщение пробуют доставить: задача, затем повторы прохода.
MESSAGE_TRIES = 5
#: Пауза перед первым повтором прохода; каждая следующая вдвое дольше: 5, 10, 20, 40 мин.
FIRST_PAUSE_SEC = 5 * 60


def pause_after(tries: int, asked: float | None) -> float:
    """Пауза перед повтором после `tries` неудач: растёт вдвое; у 429 — не меньше паузы Telegram."""
    return max(float(FIRST_PAUSE_SEC << (tries - 1)), asked or 0.0)


@dataclass(frozen=True, slots=True)
class Failed:
    """Что после недоставки: счёт серии, срок повтора и итог."""

    #: Неудач подряд в серии после этой; ноль — серии нет: итог сказан или отказ постоянный.
    tries: int
    #: Не раньше чего повторить; `None` — повторов нет.
    due_at: datetime | None
    #: Сколько попыток сделано, когда они кончились; `None` — не кончились.
    spent: int | None
    #: Сказать тревогу словами: попытки кончились или отказ постоянный, а итог ещё не сказан.
    alarm: bool

    def error(self, exc: TelegramError) -> str:
        """Почему не ушло и что дальше — словами для строки журнала."""
        if self.due_at is not None:
            return (
                f"{exc}; повтор не раньше {self.due_at:%d.%m %H:%M} UTC "
                f"(попытка {self.tries} из {MESSAGE_TRIES})"
            )
        if self.spent is not None:
            return f"{exc}; попыток — {self.spent}, повторов больше нет"
        return str(exc)


def after_failure(tries: int, exc: TelegramError, now: datetime, *, told: bool) -> Failed:
    """Не ушло после `tries` неудач серии. Временный отказ — повтор через растущую паузу, пока
    не кончились попытки; постоянный или итог уже сказан (`told`) — без повтора."""
    if exc.permanent or told:
        return Failed(tries=0, due_at=None, spent=None, alarm=not told)
    tries += 1
    if tries < MESSAGE_TRIES:
        due = now + timedelta(seconds=pause_after(tries, exc.retry_after))
        return Failed(tries=tries, due_at=due, spent=None, alarm=False)
    return Failed(tries=0, due_at=None, spent=tries, alarm=True)
