"""Правила для отправителей: разгон, включение, выключение.

Главное правило файла: **включённый заново домен начинает с начала
разгона, а не с полного капа.** Домен выключают обычно потому, что с ним
что-то не так — выросли отказы, пришла жалоба. Вернуть его сразу
на двадцать писем в день значит добить репутацию, которая и так
пошатнулась. Разгон занимает дни, репутация домена не восстанавливается
вовсе — разница в цене ошибки очевидна.

Второе: **выключение последнего домена не запрещено, но предупреждает.**
Отправлять станет нечем, и сказать об этом надо до нажатия, а не после.
Запрещать нельзя: иногда именно этого и хотят — например, когда все
домены под жалобой и рассылку надо остановить целиком.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from backend.features.core.domain import SenderStatus
from backend.features.core.models.outreach import SenderModel

#: На сколько писем в день выходит домен в первый день разгона и на сколько
#: прибавляет каждый следующий. Числа скромные намеренно: почтовые
#: платформы смотрят на скорость роста, а не на абсолютное число.
WARMUP_FIRST_DAY = 5
WARMUP_STEP_PER_DAY = 5


@dataclass(frozen=True, slots=True)
class Warmup:
    """Где домен в разгоне и сколько ему можно сегодня."""

    day: int
    allowance: int
    finished: bool


def warmup_state(sender: SenderModel, *, now: datetime | None = None) -> Warmup:
    """День разгона и сегодняшний потолок.

    Разгон не хранится числом в базе, а считается от даты старта: иначе
    его надо было бы двигать ежедневной задачей, и пропущенный день
    означал бы, что домен навсегда застрял на пятом письме.
    """
    if sender.warmup_started_at is None:
        return Warmup(day=0, allowance=sender.daily_cap, finished=True)

    moment = now or datetime.now(UTC)
    day = max(1, (moment.date() - sender.warmup_started_at.date()).days + 1)
    allowance = min(sender.daily_cap, WARMUP_FIRST_DAY + (day - 1) * WARMUP_STEP_PER_DAY)
    return Warmup(day=day, allowance=allowance, finished=allowance >= sender.daily_cap)


def enable(sender: SenderModel, *, now: datetime | None = None) -> None:
    """Включить домен — с начала разгона.

    Отметка старта ставится заново всегда, даже если домен выключили
    десять минут назад: отличить «выключили по ошибке» от «выключили
    из-за жалобы» здесь нечем, а цена ошибки в одну сторону — потерянный
    день разгона, в другую — сожжённая репутация домена.
    """
    moment = now or datetime.now(UTC)
    sender.enabled = True
    sender.status = SenderStatus.FREE
    sender.warmup_started_at = moment
    sender.sent_today = 0
    sender.paused_at = None
    sender.pause_reason = None


def disable(sender: SenderModel, reason: str, *, now: datetime | None = None) -> None:
    """Выключить домен. Начатые цепочки не рвутся — это забота отправки:
    выключенный домен просто не получает новых писем."""
    sender.enabled = False
    sender.status = SenderStatus.PAUSED
    sender.paused_at = now or datetime.now(UTC)
    sender.pause_reason = reason[:128]
