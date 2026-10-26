"""План рассылки по дням — чистый счёт прогноза (`letters/forecast.py`): сколько первых писем
уйдёт в каждый день и когда уйдёт последнее письмо очереди.

**День — по UTC**, как дневной лимит ящиков (`outreach/capacity.py`). День отправки — по окну
получателя из политики этапа (`stages.MailPolicy.window`) правилом мощности (`capacity.span_of`): у
этапа без окна — каждый день; с окном — дни его недели, а часы — в самом частом поясе получателей
очереди (`Clock.zone`, `capacity.queue_zone`). Это приближение:
окно местного дня считается в UTC-дне той же даты, а письма в другие пояса уходят в свои часы. Пояс
неизвестен — дни отправки по дням недели, а часов нет.

**Сколько уйдёт** — мощность дня (`capacity.Rooms`), но не больше, чем есть что отправить: сначала
очередь — без писем, которые не уйдут, пока их не поправят (`Supply.stuck`), — затем запас сборки
(`Supply.extra`); сегодня — остаток дня. День без окна — ноль с причиной, а не мощность ящиков,
которую некому потратить.

**Добивки дня** — по UTC-дню срока, а срок в день без окна получателя ждёт его открытия: окно судит
и добивки (`sending._check_window`), и проход откладывает такую до окна (`followups._deliver`). Здесь
она — в следующем дне отправки (`_followups`).

**Темп автоотправки** (`window.Pace`): с ящика — письмо за проход и пауза между его первыми
письмами, внутри окна. За день ящик успевает не больше, чем пауз умещается в окно (сегодня — от
сейчас), а последнее письмо очереди уходит через столько пауз от начала окна (сегодня — от сейчас),
сколько писем в тот день отдаст самый занятый ящик (`Rooms.busiest`). Пауза — средняя: разброс у
каждого письма свой, а n-е письмо дня ящик отдаёт через n пауз — с запасом в одну. Без темпа часов
нет: вручную пачка выбирает дневной лимит за секунды, и срок в днях — если нажимать «Отправить
очередь» каждый день отправки.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from backend.features.core.window import WEEKDAYS, Pace, SendWindow
from backend.features.letters.batch import BATCH_MAX
from backend.features.outreach.capacity import Rooms, Span, span_of

DAY = timedelta(days=1)
HOUR = timedelta(hours=1)
#: Дальше года срок очереди не ищется: год счёт проходит за миллисекунды, а очереди, которой года
#: не хватает, нужен не срок, а другие ящики — это и сказано словами.
REACH = 366


@dataclass(frozen=True, slots=True)
class Clock:
    """Когда в день уходят первые письма этапа: окно получателя (`None` — в любой час), самый
    частый пояс получателей очереди (`None` — неизвестен) и темп автоотправки (`None` — вручную)."""

    window: SendWindow | None = None
    zone: ZoneInfo | None = None
    pace: Pace | None = None


@dataclass(frozen=True, slots=True)
class Supply:
    """Что есть отправить: первых писем в очереди, сколько из них не уйдёт, пока их не поправят, и
    сколько ещё напишет сборка (`None` — не узнать, или сборки, которая пишет сама, у этапа нет)."""

    queued: int
    extra: int | None = None
    stuck: int = 0

    @property
    def going(self) -> int:
        """Писем очереди, которые уйдут, — без застрявших."""
        return max(0, self.queued - self.stuck)


@dataclass(frozen=True, slots=True)
class Mailboxes:
    """Ящики этапа для плана (`outreach/capacity.py`): остаток сегодня, мощность сегодня целиком,
    мощность будущего дня, первых писем ушло сегодня и слова причин."""

    left: Rooms
    capacity: int
    later: Callable[[date], Rooms]
    sent: int = 0
    #: Почему у ящиков нет мощности вовсе: ящиков нет, лимит направления не прочитан.
    idle: str = "ящиков, которые пишут, нет"
    #: Почему ящики сегодня не берут первых писем — словами фильтра лимитов.
    refused: str | None = None
    #: Почему письма этапа не уходят вовсе (продажи не подключены); пусто — уходят.
    blocked: str | None = None


@dataclass(frozen=True, slots=True)
class PlanDay:
    """День плана."""

    day: date
    #: День отправки: окно получателя в этот день есть, а сегодня — ещё не закрылось.
    sending: bool
    #: Мощность ящиков в этот день; день без окна — ноль.
    capacity: int
    #: Сколько первых писем уйдёт: мощность, но не больше, чем есть что отправить.
    planned: int
    #: Добивок, чей срок подходит в этот день (просроченные — сегодня).
    followups: int = 0
    #: Почему писем нет или меньше мощности — словами; пусто — уходят во всю мощность.
    why_not: str | None = None


@dataclass(frozen=True, slots=True)
class Eta:
    """Когда уйдёт последнее письмо очереди: день, дней отправки до него и часов от сейчас (только
    у автоотправки) — или почему срока нет, словами (`words` есть всегда)."""

    day: date | None = None
    sending_days: int | None = None
    hours: float | None = None
    words: str = ""


@dataclass(slots=True)
class _Left:
    """Сколько осталось отправить: письма очереди уходят первыми — номер письма и есть порядок."""

    queued: int
    extra: int
    #: Запас сборки известен: «отправлять нечего» тогда значит, что кончился и он.
    known: bool = False

    @classmethod
    def of(cls, supply: Supply) -> _Left:
        return cls(supply.going, supply.extra or 0, supply.extra is not None)

    @property
    def total(self) -> int:
        return self.queued + self.extra

    def take(self, letters: int) -> None:
        first = min(letters, self.queued)
        self.queued -= first
        self.extra -= letters - first


def plan(
    now: datetime,
    horizon: int,
    clock: Clock,
    boxes: Mailboxes,
    supply: Supply,
    followups: Mapping[date, int],
) -> tuple[list[PlanDay], Eta]:
    """План на `horizon` дней от сегодня и срок очереди — день за днём, до года вперёд."""
    run = _Run(now, clock, boxes, _Left.of(supply), eta=_early(boxes, supply), stuck=supply.stuck)
    days = [run.day(offset) for offset in range(horizon)]
    for offset in range(horizon, REACH):
        if run.eta is not None:
            break
        run.day(offset)
    return _followups(days, followups), run.eta or _never(boxes, run.went)


def _followups(days: list[PlanDay], due: Mapping[date, int]) -> list[PlanDay]:
    """Добивки по дням: в день без окна они ждут его открытия — переходят в следующий день отправки."""
    found: list[PlanDay] = []
    waiting = 0
    for day in days:
        waiting += due.get(day.day, 0)
        found.append(replace(day, followups=waiting if day.sending else 0))
        waiting = 0 if day.sending else waiting
    return found


@dataclass(slots=True)
class _Run:
    """Счёт день за днём: что осталось отправить, сколько дней уже уходили письма и срок очереди."""

    now: datetime
    clock: Clock
    boxes: Mailboxes
    left: _Left
    went: int = 0
    eta: Eta | None = None
    #: Писем очереди, которые не уйдут, пока их не поправят: срок — без них, и это сказано.
    stuck: int = 0

    def day(self, offset: int) -> PlanDay:
        """День `offset` от сегодня: что уйдёт — из остатка; кончилась в нём очередь — её срок."""
        day = self.now.date() + offset * DAY
        rooms = self.boxes.left if offset == 0 else self.boxes.later(day)
        span = span_of(self.clock.window, self.clock.zone, day, self.now)
        found = _day(day, offset, rooms, span, self)
        queued = self.left.queued
        self.left.take(found.planned)
        self.went += found.planned > 0
        if self.eta is None and queued and not self.left.queued:
            self.eta = _done(day, self.went, rooms, span, queued, self)
        return found


def _per_box(pace: Pace | None, span: Span) -> int | None:
    """Сколько писем ящик успеет за часы дня при темпе автоотправки — по паузе на письмо; `None` —
    темпа нет или часов не узнать."""
    if pace is None or span.start is None or span.end is None:
        return None
    return max(0, int((span.end - span.start) / pace.gap))


def _day(day: date, offset: int, rooms: Rooms, span: Span, run: _Run) -> PlanDay:
    """Один день: мощность, сколько уйдёт и почему меньше мощности."""
    boxes = run.boxes
    capacity = boxes.capacity if offset == 0 else rooms.total()
    if span.closed is not None:
        return PlanDay(day, False, 0, 0, why_not=span.closed)
    if boxes.blocked is not None:
        return PlanDay(day, True, capacity, 0, why_not=boxes.blocked)
    per_box = _per_box(run.clock.pace, span)
    room = rooms.total(per_box)
    planned = min(room, run.left.total)
    why = _why(_Day(offset, capacity, rooms, room, per_box, planned), span, run)
    return PlanDay(day, True, capacity, planned, why_not=why)


@dataclass(frozen=True, slots=True)
class _Day:
    """Числа дня для слов: мощность, остаток ящиков, сколько пустит темп и сколько уйдёт."""

    offset: int
    capacity: int
    rooms: Rooms
    room: int
    per_box: int | None
    planned: int


def _why(found: _Day, span: Span, run: _Run) -> str | None:
    """Почему в день уйдёт меньше мощности — словами; `None` — уйдёт во всю мощность."""
    if found.capacity == 0:
        return _idle(found.offset, run.boxes)
    if found.planned < found.room:
        return _exhausted(run.left)
    if found.per_box is not None and found.room < found.rooms.total():
        return _paced(run.clock, span, found.per_box)
    if found.offset == 0 and found.planned < found.capacity:
        return _today(run.boxes, found.rooms)
    return None


def _exhausted(left: _Left) -> str:
    """Отправлять больше нечего — словами: кончилась очередь, а с известным запасом — и он."""
    if left.known:
        return "отправлять больше нечего: кончились и очередь, и лиды, которым сборка напишет"
    return "отправлять больше нечего: очередь кончилась — новые письма ставит сборка очереди"


def _paced(clock: Clock, span: Span, per_box: int) -> str:
    pace, end = clock.pace, span.end
    gap = 0 if pace is None else round(pace.gap / timedelta(minutes=1))
    until = "" if end is None else f" — до {end:%H:%M} UTC"
    return f"темп автоотправки: письмо с ящика раз в {gap} мин{until} успеет не больше {per_box} с ящика"


def _idle(offset: int, boxes: Mailboxes) -> str:
    """У ящиков нет мощности: сегодня — словами фильтра, почему они не берут писем, если он сказал."""
    return boxes.refused if offset == 0 and boxes.refused else boxes.idle


def _today(boxes: Mailboxes, rooms: Rooms) -> str:
    """Сегодня уйдёт меньше мощности дня: часть уже ушла, ящики сегодня не берут писем (слова
    фильтра) — или лимит домена выбрали ящики другого направления на нём."""
    left = f"остаток дня {rooms.total()}"
    if boxes.sent:
        said = f"сегодня уже ушло {boxes.sent} первых писем — {left}"
        return f"{said}; {boxes.refused}" if boxes.refused else said
    return boxes.refused or f"{left}: часть лимита домена выбрали ящики другого направления на нём"


def _early(boxes: Mailboxes, supply: Supply) -> Eta | None:
    """Срок, который известен без счёта: очередь пуста, письма этапа не уходят вовсе или в очереди
    только письма, которые не уйдут, пока их не поправят."""
    if supply.queued == 0:
        return Eta(words="очередь пуста — отправлять нечего")
    if boxes.blocked is not None:
        return Eta(words=f"очередь не уходит: {boxes.blocked}")
    if supply.going == 0:
        return Eta(
            words=f"в очереди только письма, которые не уйдут, пока их не поправят ({supply.stuck})"
        )
    return None


def _done(day: date, went: int, rooms: Rooms, span: Span, letters: int, run: _Run) -> Eta:
    """Очередь кончилась в день `day`: в него ушло `letters` её писем."""
    pace, hours = run.clock.pace, None
    if pace is not None and span.start is not None:
        moment = span.start + pace.gap * rooms.busiest(letters)
        hours = round(max(timedelta(0), moment - run.now) / HOUR, 1)
    when = f"последнее письмо очереди уйдёт {WEEKDAYS[day.weekday()]} {day:%d.%m}, дней отправки: {went}"
    if run.stuck:
        when += f" (без {run.stuck} писем, которые не уйдут, пока их не поправят)"
    return Eta(day, went, hours, _how(when, hours, run.clock))


def _how(when: str, hours: float | None, clock: Clock) -> str:
    """Срок словами: часы у автоотправки, а вручную — при каком условии срок верен."""
    if clock.pace is None:
        inside = " в часы окна получателя" if clock.window is not None else ""
        return (
            f"{when} — вручную: пачка выбирает дневной лимит за секунды (до {BATCH_MAX} писем за "
            f"нажатие), срок верен, если нажимать «Отправить очередь» каждый день отправки{inside}; "
            "часов здесь нет"
        )
    if hours is None:
        return f"{when}; часов не посчитать: пояс получателей очереди неизвестен"
    gap = round(clock.pace.gap / timedelta(minutes=1))
    zone = "" if clock.zone is None else f", окно — по часам {clock.zone.key}"
    return (
        f"{when}, ≈ через {hours} ч: автоотправка, письмо с ящика раз в {gap} мин "
        f"(±{clock.pace.spread:.0%}){zone}"
    )


def _never(boxes: Mailboxes, went: int) -> Eta:
    """Очередь не кончилась и за год: ящики не пишут вовсе — или пишут, но мало."""
    if not went:
        return Eta(words=f"очередь не уйдёт: {boxes.idle}")
    return Eta(words=f"очередь не уйдёт и за {REACH} дней при нынешней мощности ящиков")
