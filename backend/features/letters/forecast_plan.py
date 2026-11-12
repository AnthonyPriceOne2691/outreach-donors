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

**Причина сегодняшнего дня — про день целиком** (`_why`, `_today`; замечание проверки 12.11.2026).
«Уйдёт» у сегодня — остаток дня, а мощность — день целиком, поэтому с первого же ушедшего письма
остаток меньше мощности. До 12.11.2026 это и называлось причиной: «сегодня уже ушло N первых писем
— остаток дня M» — хотя день шёл во всю мощность, и строка «почему меньше мощности» говорила о дне,
которому ничего не мешает. Теперь день судится целиком: ушедшее (`Mailboxes.sent`) вместе с тем,
что ещё уйдёт, не меньше мощности — причины нет; меньше — причина называет только то, что мешает:
слова фильтра лимитов или лимит домена, часть которого выбрали ящики другого направления. Сколько
ушло, экран показывает своим числом рядом с планом дня.

**Добивки дня** — по UTC-дню срока, а срок в день без окна получателя ждёт его открытия: окно судит
и добивки (`sending._check_window`), и проход откладывает такую до окна (`followups._deliver`). Здесь
она — в следующем дне отправки (`_followups`). **С ящика — не больше суточного потолка добивок**
(`Chains.cap`, `OUTREACH_FOLLOWUP_PER_SENDER_PER_DAY`): проход откладывает лишнее до завтра
(`followups._waits`), и здесь оно — в следующем дне отправки, у каждого ящика свой перенос: добивка
уходит только с ящика своей переписки. Без этого план обещал бы понедельнику весь завал выходных,
а ушла бы из него треть.

**Темп автоотправки** (`window.Pace`): с ящика — письмо за проход и пауза между его первыми
письмами, внутри окна. За день ящик успевает не больше, чем пауз умещается в окно (сегодня — от
сейчас), а последнее письмо очереди уходит через столько пауз от начала окна (сегодня — от сейчас),
сколько писем в тот день отдаст самый занятый ящик (`Rooms.busiest`). Пауза — средняя: разброс у
каждого письма свой, а n-е письмо дня ящик отдаёт через n пауз — с запасом в одну. Без темпа часов
нет: вручную пачка выбирает дневной лимит за секунды, и срок в днях — если нажимать «Отправить
очередь» каждый день отправки.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
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
class Chains:
    """Добивки к сроку по дням и ящикам — и суточный потолок добивок ящика, которым их судит проход
    (`followups._waits`)."""

    #: День срока (UTC) → ящик переписки → добивок; просроченные — сегодня. Ящик `None` — без
    #: разбивки по ящикам (`flat`) или ящик переписки удалён.
    due: Mapping[date, Mapping[int | None, int]] = field(default_factory=dict)
    #: Суточный потолок добивок ящика; `None` — потолка нет.
    cap: int | None = None
    #: Сколько добивок ящик уже отдал сегодня: место в сегодняшнем потолке занято.
    sent: Mapping[int, int] = field(default_factory=dict)

    @classmethod
    def flat(cls, due: Mapping[date, int]) -> Chains:
        """Добивки по дням без разбивки по ящикам и без потолка."""
        return cls({day: {None: count} for day, count in due.items()})

    def room(self, box: int | None, *, today: bool) -> int | None:
        """Сколько добивок ящик ещё отдаст за день; `None` — сколько есть."""
        if self.cap is None:
            return None
        used = self.sent.get(box, 0) if today and box is not None else 0
        return max(0, self.cap - used)


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
    #: Первых писем ушло сегодня — счётом дневного лимита: вместе с тем, что ещё уйдёт, это день
    #: целиком, и по нему судится, выберет ли сегодня мощность (`_why`).
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
    #: Добивок уйдёт в этот день: срок подошёл (просроченные — сегодня), и ящик переписки не
    #: выбрал суточный потолок добивок; остальные — в следующем дне отправки.
    followups: int = 0
    #: Почему писем нет или меньше мощности — словами; пусто — уходят во всю мощность. У сегодня —
    #: про день целиком: ушедшее вместе с `planned` (остатком дня) против мощности дня.
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
    followups: Chains,
) -> tuple[list[PlanDay], Eta]:
    """План на `horizon` дней от сегодня и срок очереди — день за днём, до года вперёд."""
    run = _Run(now, clock, boxes, _Left.of(supply), eta=_early(boxes, supply), stuck=supply.stuck)
    days = [run.day(offset) for offset in range(horizon)]
    for offset in range(horizon, REACH):
        if run.eta is not None:
            break
        run.day(offset)
    return _followups(days, followups), run.eta or _never(boxes, run.went)


def _followups(days: list[PlanDay], chains: Chains) -> list[PlanDay]:
    """Добивки по дням: в день без окна они ждут его открытия — переходят в следующий день отправки;
    сверх суточного потолка ящика — тоже, у каждого ящика свой остаток."""
    found: list[PlanDay] = []
    waiting: Counter[int | None] = Counter()
    for index, day in enumerate(days):
        waiting.update(chains.due.get(day.day, {}))
        going = _going(waiting, chains, today=index == 0) if day.sending else Counter()
        found.append(replace(day, followups=going.total()))
        waiting -= going
    return found


def _going(waiting: Counter[int | None], chains: Chains, *, today: bool) -> Counter[int | None]:
    """Сколько ждущих добивок каждый ящик отдаст за день отправки — не больше своего потолка."""
    going: Counter[int | None] = Counter()
    for box, count in waiting.items():
        room = chains.room(box, today=today)
        going[box] = count if room is None else min(count, room)
    return going


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
    """Почему в день уйдёт меньше мощности — словами; `None` — уйдёт во всю мощность.

    Сегодня — день целиком: `planned` у него — остаток дня, и с ушедшим (`Mailboxes.sent`) он
    сравнивается с мощностью дня. Остаток меньше мощности на ушедшее — не причина: день идёт во
    всю мощность (см. модуль, 12.11.2026)."""
    if found.capacity == 0:
        return _idle(found.offset, run.boxes)
    if found.planned < found.room:
        return _exhausted(run.left)
    if found.per_box is not None and found.room < found.rooms.total():
        return _paced(run.clock, span, found.per_box)
    if found.offset == 0 and run.boxes.sent + found.planned < found.capacity:
        return _today(run.boxes)
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


def _today(boxes: Mailboxes) -> str:
    """Сегодня день не выберет мощность, хотя отправить есть что и темп пускает: что мешает —
    словами фильтра лимитов (ящик или домен сегодня не берёт писем), а он промолчал — лимит домена,
    часть которого выбрали ящики другого направления на нём: больше остатку дня уменьшиться не от
    чего. Сколько ушло и сколько осталось, здесь не сказано — это числа, а не причина: экран
    показывает их своими колонками (12.11.2026)."""
    return boxes.refused or "часть лимита домена выбрали ящики другого направления на нём"


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
    # Часы — с запятой, как на плитке «Часов»; темп — паузой между письмами ящика, как в строке
    # режима продаж (`sales.auto_lines.pace`): «раз в 15 мин (±50%)» рядом с «пауза 8–22 мин» читалось
    # двумя разными темпами (разведка экранов 10.11.2026).
    gap = clock.pace.gap / timedelta(minutes=1)
    shortest, longest = round(gap * (1 - clock.pace.spread)), round(gap * (1 + clock.pace.spread))
    zone = "" if clock.zone is None else f", окно — по часам {clock.zone.key}"
    said = f"{hours:.1f}".replace(".", ",")
    return (
        f"{when}, ≈ через {said}\u00a0ч: автоотправка, пауза {shortest}–{longest}\u00a0мин между "
        f"письмами ящика{zone}"
    )


def _never(boxes: Mailboxes, went: int) -> Eta:
    """Очередь не кончилась и за год: ящики не пишут вовсе — или пишут, но мало."""
    if not went:
        return Eta(words=f"очередь не уйдёт: {boxes.idle}")
    return Eta(words=f"очередь не уйдёт и за {REACH} дней при нынешней мощности ящиков")
