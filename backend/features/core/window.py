"""Окно получателя: письмо уходит в его рабочие часы, а не в наши (Ф4, срез 4.3).

Холодное письмо в три часа ночи по времени получателя читают утром среди ночной
почты — чаще всего как спам. У этапа, чья политика задаёт окно (`stages.MailPolicy`),
письмо вне окна не уходит и не теряется: первое ждёт в очереди, добивка — открытия
окна плюс случайный сдвиг, чтобы отложенное за выходные не ушло одной минутой.

**Окно — местное время получателя**, пояс — первый известный: лида, страны, гипотезы
(`zone_of`, данные — `stages.Recipient.zones`). Пояса нет — письмо ждёт: окно в
неизвестном поясе было бы окном наугад. Сутки и переходы часов — по базе поясов IANA:
времени, которого нет, окно ждёт до первой существующей минуты; время, которое
бывает дважды, — первое. Сдвиг — внедряемым генератором (`JITTER`): тест ставит свой.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

logger = logging.getLogger(__name__)

#: Дни недели словами, с понедельника — как `date.weekday()`.
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")

#: Генератор сдвига по умолчанию. Тест подменяет его своим, с зерном.
JITTER = random.Random()  # noqa: S311 — сдвиг времени отправки, не секрет

#: Почему письмо не уходит, если пояса получателя не знает никто.
NO_ZONE = (
    "пояс получателя неизвестен — ни у лида, ни у его страны, ни у гипотезы; "
    "окно не посчитать, письмо остаётся ждать"
)

_MINUTE = timedelta(minutes=1)


@dataclass(frozen=True, slots=True)
class SendWindow:
    """Дни и часы местного времени получателя, когда письмо может уйти."""

    #: Дни недели с нуля (0 — понедельник), как `date.weekday()`.
    days: frozenset[int]
    start: time
    end: time
    #: Отложенные письма расходятся по этому отрезку от открытия окна.
    spread: timedelta = timedelta(minutes=30)

    def __post_init__(self) -> None:
        if not self.days or not self.days <= set(range(7)):
            raise ValueError(f"окно без дней недели или с чужими днями: {sorted(self.days)}")
        if self.start >= self.end:
            raise ValueError(f"окно закрывается не позже, чем открывается: {self.start}–{self.end}")

    @property
    def words(self) -> str:
        """«пн–пт 09:00–17:00» — для отказа и экрана."""
        days = sorted(self.days)
        named = ",".join(WEEKDAYS[day] for day in days)
        if len(days) > 1 and days == list(range(days[0], days[-1] + 1)):
            named = f"{WEEKDAYS[days[0]]}–{WEEKDAYS[days[-1]]}"
        return f"{named} {self.start:%H:%M}–{self.end:%H:%M}"


class DeferredError(Exception):
    """Отказ «не сейчас»: письмо уйдёт само через `delay` (пусто — обычной паузой)."""

    def __init__(self, text: str, *, delay: timedelta | None = None) -> None:
        super().__init__(text)
        self.delay = delay


def postpone(exc: BaseException, usual: timedelta) -> timedelta:
    """На сколько отложить добивку после отказа `exc`: до окна или обычной паузой."""
    if isinstance(exc, DeferredError) and exc.delay is not None:
        return exc.delay
    return usual


def zone_of(candidates: Iterable[str | None]) -> ZoneInfo | None:
    """Пояс получателя: первый известный базе поясов по порядку — лида, страны, гипотезы."""
    for name in candidates:
        if not name:
            continue
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("окно: пояса «%s» нет в базе поясов — берётся следующий", name)
    return None


def _first_at_or_after(day: date, wall: time, zone: ZoneInfo) -> datetime:
    """Первая минута дня `day`, когда на часах получателя не раньше `wall` (в UTC):
    у времени, которое бывает дважды, — первое (`fold=0`)."""
    naive = datetime.combine(day, wall)
    moment = naive.replace(tzinfo=zone).astimezone(UTC)
    if moment.astimezone(zone).replace(tzinfo=None) == naive:
        return moment
    # Часы переведены вперёд, `wall` не наступает: от более раннего прочтения
    # (смещение после перевода) — по минуте до самого перевода.
    moment = naive.replace(tzinfo=zone, fold=1).astimezone(UTC)
    while moment.astimezone(zone).replace(tzinfo=None) < naive:
        moment += _MINUTE
    return moment


def _next_open(window: SendWindow, zone: ZoneInfo, at: datetime) -> tuple[datetime, datetime]:
    """Ближайшее окно, которое ещё не закрылось к `at`: открытие и закрытие в UTC."""
    today = at.astimezone(zone).date()
    for ahead in range(8):  # неделя вперёд и день: день окна найдётся всегда
        day = today + timedelta(days=ahead)
        if day.weekday() not in window.days:
            continue
        closes = _first_at_or_after(day, window.end, zone)
        if at < closes:
            return _first_at_or_after(day, window.start, zone), closes
    raise AssertionError(f"у окна {window.words} нет дня на неделю вперёд")  # pragma: no cover


def is_open(window: SendWindow, zone: ZoneInfo, at: datetime) -> bool:
    """Открыто ли окно получателя в минуту `at`."""
    opens, _ = _next_open(window, zone, at)
    return opens <= at


def defer_until(
    window: SendWindow, zone: ZoneInfo, at: datetime, jitter: random.Random | None = None
) -> datetime | None:
    """Когда уйти письму при закрытом окне: открытие плюс сдвиг, не дальше закрытия
    окна. `None` — окно открыто, письмо уходит сейчас."""
    opens, closes = _next_open(window, zone, at)
    if opens <= at:
        return None
    spread = min(window.spread, closes - opens)
    return opens + spread * (jitter or JITTER).random()


def local_words(moment: datetime, zone: ZoneInfo) -> str:
    """«пн 12.10 09:07 (Europe/Berlin)» — минута по часам получателя."""
    local = moment.astimezone(zone)
    return f"{WEEKDAYS[local.weekday()]} {local:%d.%m %H:%M} ({zone.key})"


@dataclass(frozen=True, slots=True)
class Late:
    """Письмо не уходит сейчас: почему — словами, и через сколько уйдёт само."""

    words: str
    delay: timedelta | None  # пусто — пояса нет, окна не посчитать


def check(frame: SendWindow | None, zones: Iterable[str | None], at: datetime) -> Late | None:
    """Может ли письмо уйти в минуту `at`: `None` — может (окна нет или оно открыто)."""
    if frame is None:
        return None
    zone = zone_of(zones)
    if zone is None:
        return Late(words=NO_ZONE, delay=None)
    until = defer_until(frame, zone, at)
    if until is None:
        return None
    return Late(
        words=(
            f"вне окна получателя ({frame.words} по его часам) — уйдёт не раньше "
            f"{local_words(until, zone)}; письмо не потеряно, оно ждёт"
        ),
        delay=until - at,
    )
