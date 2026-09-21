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

Третье: **письмо отдаётся ящику с наибольшим остатком на сегодня.**
Остаток считается от потолка разгона, а не от дневного капа: ящик
на третьем дне может десять писем, а не двадцать. И считается он
по отправленным письмам — хранимого счётчика тут нет намеренно, его
некому было бы обнулять.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from backend.config import outreach as cfg
from backend.features.core.domain import SenderStatus, Stage
from backend.features.core.models.outreach import SenderModel

#: Ступени дневного капа и сколько дней держится каждая — из настроек
#: (`OUTREACH_WARMUP_CAPS`, `OUTREACH_WARMUP_STEP_DAYS`). Числа скромные
#: намеренно: почтовые платформы смотрят на скорость роста, а не
#: на абсолютное число.
#:
#: Раньше ступени были заданы дважды — здесь константами и в настройках, —
#: и совпадали случайно. Правка одной из копий молча меняла бы разгон
#: в одном месте и оставляла прежним в другом.
WARMUP_STEPS: tuple[int, ...] = cfg.WARMUP_DAILY_CAPS
WARMUP_STEP_DAYS: int = cfg.WARMUP_STEP_DAYS

#: Сколько можно в первый день. Имя оставлено: по нему читается смысл
#: первой ступени.
WARMUP_FIRST_DAY = WARMUP_STEPS[0]


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
    step = min((day - 1) // WARMUP_STEP_DAYS, len(WARMUP_STEPS) - 1)
    allowance = min(sender.daily_cap, WARMUP_STEPS[step])
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
    sender.paused_at = None
    sender.pause_reason = None


def disable(sender: SenderModel, reason: str, *, now: datetime | None = None) -> None:
    """Выключить домен. Начатые цепочки не рвутся — это забота отправки:
    выключенный домен просто не получает новых писем."""
    sender.enabled = False
    sender.status = SenderStatus.PAUSED
    sender.paused_at = now or datetime.now(UTC)
    sender.pause_reason = reason[:128]


@dataclass(frozen=True, slots=True)
class Availability:
    """Отправитель и сколько ему осталось на сегодня."""

    sender: SenderModel
    allowance: int
    sent: int

    @property
    def remaining(self) -> int:
        return max(0, self.allowance - self.sent)


def _spot(
    sender: SenderModel, *, sent: int, stage: Stage, now: datetime
) -> Availability | None:
    """Место под письмо у одного ящика. `None` — сегодня он не пишет."""
    if not sender.enabled or sender.stage is not stage:
        return None
    state = warmup_state(sender, now=now)
    found = Availability(sender=sender, allowance=state.allowance, sent=sent)
    return found if found.remaining > 0 else None


def available(
    senders: Sequence[SenderModel],
    *,
    sent_today: Mapping[int, int],
    stage: Stage,
    now: datetime | None = None,
) -> list[Availability]:
    """Кто сегодня ещё может писать.

    Выключенные не попадают сюда вовсе: выключенный домен не получает
    новых писем, но начатые цепочки не рвутся — это забота отправки,
    а не этого списка.
    """
    moment = now or datetime.now(UTC)
    found = (
        _spot(sender, sent=sent_today.get(sender.id, 0), stage=stage, now=moment)
        for sender in senders
    )
    return [spot for spot in found if spot is not None]


def pick(
    senders: Sequence[SenderModel],
    *,
    sent_today: Mapping[int, int],
    stage: Stage,
    now: datetime | None = None,
) -> Availability | None:
    """Кому отдать следующее письмо. `None` — сегодня писать некому.

    Тому, у кого больше остаток, при равенстве — меньший номер. Не по
    кругу: круг раздаёт поровну, только если все ящики одинаковы и заведены
    одновременно, а в жизни один добавлен вчера, другой стоял на паузе,
    у третьего кап правили руками.
    """
    ready = available(senders, sent_today=sent_today, stage=stage, now=now)
    if not ready:
        return None
    return max(ready, key=lambda spot: (spot.remaining, -spot.sender.id))
