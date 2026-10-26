"""Мощность ящиков этапа по дням: сколько первых писем они могут отправить — правилом экрана
«Домены рассылки» (прогноз рассылки, `letters/forecast.py`).

**Правило одно**: потолок разгона включённого ящика этапа (`senders.warmup_state` в полдень дня),
сложенный по домену и урезанный его лимитом (`sending_domains.daily_limit`); закрытый домен —
пауза, выдержка, чужое направление (`limits.domain_shut`) — ноль; строки домена нет — сколько могут
ящики. Итог урезает дневной лимит направления (`OUTREACH_<ЭТАП>_DAILY_LIMIT`); его настройка не
прочиталась — лимит ноль, и это сказано словами (`unread`). До прогноза правило жило копией в «Плане»
недельного отчёта продаж (`sales/weekly_counts.runway`): подпись `capacity` повторяет его, чтобы
отчёт перешёл на эту функцию и план недели совпадал с прогнозом на те же дни.

**День — по UTC**, как дневной счёт писем (`OutreachRepository.sent_today`). Ступень разгона — от
первого письма ящика: ящик, ещё не писавший, по правилу экрана стоит на первой ступени на все дни
вперёд. Прогноз рассылки просит предположить первое письмо (`project_first_letter`): включённый ящик
без него напишет его в первый день, когда первые письма ещё уходят (`first_letter_day`), и разгон
идёт оттуда. Правило дня одно — у прогноза и у мощности по дням, кто бы её ни спрашивал: от минуты
вопроса (`now`), а не от первого из спрошенных дней, — иначе план недели и прогноз на те же даты
расходились бы. «План» недельного отчёта первого письма не предполагает и считает, как экран.

**Дни и часы отправки** — окно получателя из политики этапа (`stages.MailPolicy.window`) в самом
частом поясе получателей очереди (`queue_zone`; у продаж — ответ моста `core/stage_forecast.py`):
день окна, а сегодня — пока окно не закрылось (`span_of`). Пояс неизвестен — по дням недели, без
часов; окна нет — каждый день целиком.

**Сегодня — глазами выбора ящика первого письма** (`today`, как `letters/mailbox._free_box`): тот же
фильтр лимитов (`limits.screen`) и остаток ящика от потолка разгона (`senders.available`). Лимит
ящика, сниженный на сутки мягкими отказами (`health.cuts`, ×0,5), действует только в пути отправки —
здесь он потолок ящика на сегодня, и прогноз говорит об этом словами (`Today.cut`). Остаток дня — по
ящику, домену и направлению за вычетом первых писем, ушедших сегодня (`sent_today(first_only=True)`):
в обычный день это мощность дня минус ушедшее. Добивки в счёт не входят: у них свой часовой лейн
(решение 21.09.2026).
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import assert_never
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import outreach as cfg
from backend.features.core import stage_forecast, stages, window
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import SenderModel, SendingDomainModel
from backend.features.core.window import SendWindow
from backend.features.outreach import limits
from backend.features.outreach import senders as rules
from backend.features.outreach.repository import OutreachRepository

logger = logging.getLogger(__name__)

#: Полдень: им будущий день спрашивает разгон ящика и закрытость домена — как «План» отчёта.
NOON = time(12, 0)


def noon(day: date) -> datetime:
    """Полдень дня `day` по UTC — минута, которой спрашивается будущий день."""
    return datetime.combine(day, NOON, tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class Rooms:
    """Сколько первых писем ящики могут отправить за день: остаток каждого ящика — по доменам,
    остаток домена (`None` — своего лимита у домена нет) и направления (`None` — лимита нет)."""

    boxes: Mapping[str, tuple[int, ...]]
    domains: Mapping[str, int | None]
    direction: int | None = None

    def total(self, per_box: int | None = None) -> int:
        """Писем за день через лимиты домена и направления; `per_box` — не больше стольких с ящика
        (темп автоотправки в окне)."""
        total = 0
        for name, rooms in self.boxes.items():
            letters = sum(room if per_box is None else min(room, per_box) for room in rooms)
            room = self.domains.get(name)
            total += letters if room is None else min(letters, room)
        return total if self.direction is None else min(total, self.direction)

    def busiest(self, letters: int) -> int:
        """Сколько писем отдаст самый занятый ящик, пока уйдут `letters`: выбор ящика отдаёт письмо
        ящику с наибольшим остатком (`senders.pick`), и ящики пишут вровень — по письму за круг."""
        top = max((room for rooms in self.boxes.values() for room in rooms), default=0)
        return next((n for n in range(1, top + 1) if self.total(n) >= letters), top)


def gather(
    rooms: Iterable[tuple[SenderModel, int]],
    domains: Mapping[str, SendingDomainModel],
    stage: Stage,
    moment: datetime,
    limit: int | None,
    *,
    used: Mapping[str, int] | None = None,
    spent: int = 0,
) -> Rooms:
    """Остатки ящиков `rooms` по доменам — с остатком домена и направления в минуту `moment`:
    закрытый домен — ноль, его лимит — за вычетом `used` (первые письма домена, ушедшие сегодня со
    всех его ящиков, как считает фильтр), лимит направления `limit` — за вычетом `spent`."""
    boxes: dict[str, list[int]] = {}
    for box, room in rooms:
        boxes.setdefault(box.domain, []).append(room)
    taken = used or {}
    return Rooms(
        {name: tuple(found) for name, found in boxes.items()},
        {
            name: _domain_room(domains.get(name), stage, moment, taken.get(name, 0))
            for name in boxes
        },
        None if limit is None else max(0, limit - spent),
    )


def _domain_room(
    row: SendingDomainModel | None, stage: Stage, moment: datetime, used: int
) -> int | None:
    """Сколько ещё пропустит домен: без строки домена — сколько могут ящики (`None`); закрыт —
    ничего; иначе — его лимит за вычетом ушедшего."""
    if row is None:
        return None
    if limits.domain_shut(row, stage, moment) is not None:
        return 0
    return max(0, row.daily_limit - used)


def rooms_on(
    boxes: Sequence[SenderModel],
    domains: Mapping[str, SendingDomainModel],
    stage: Stage,
    limit: int | None,
    day: date,
    *,
    first: date | None = None,
) -> Rooms:
    """Мощность дня `day` по правилу экрана: потолок разгона каждого включённого ящика этапа в
    полдень дня — по доменам, с лимитами домена и направления. `first` — день, когда ящик, ещё не
    писавший, напишет первое письмо (`project_first_letter`); пусто — он на первой ступени."""
    moment, assumed = noon(day), None if first is None else noon(first)
    own = (box for box in boxes if box.enabled and box.stage is stage)
    allowances = (
        (box, rules.warmup_state(box, now=moment, assumed=assumed).allowance) for box in own
    )
    return gather(allowances, domains, stage, moment, limit)


def day_capacity(
    boxes: Sequence[SenderModel],
    domains: Mapping[str, SendingDomainModel],
    stage: Stage,
    limit: int | None,
    day: date,
    *,
    first: date | None = None,
) -> int:
    """Первых писем за день `day` — правилом экрана «Домены рассылки» (`rooms_on`)."""
    return rooms_on(boxes, domains, stage, limit, day, first=first).total()


def first_sending(start: date, window: SendWindow | None) -> date:
    """Первый день отправки не раньше `start`: день окна получателя этапа (у этапа без окна —
    сам `start`). В неделе день окна есть всегда (`window.SendWindow`)."""
    if window is None:
        return start
    return next(
        day for day in (start + timedelta(days=n) for n in range(7)) if day.weekday() in window.days
    )


@dataclass(frozen=True, slots=True)
class DayCapacity:
    """День и сколько первых писем ящики этапа могут в него отправить."""

    day: date
    letters: int


@dataclass(frozen=True, slots=True)
class Capacity:
    """Мощность ящиков этапа по дням: включённых ящиков, первых писем по дням и — словами — что
    лимит направления не прочитан из настроек (`unread`; тогда писем ноль)."""

    boxes: int
    days: list[DayCapacity]
    unread: str | None = None


async def capacity(
    session: AsyncSession,
    stage: Stage,
    days: Sequence[date],
    *,
    project_first_letter: bool = False,
    now: datetime | None = None,
) -> Capacity:
    """Мощность ящиков этапа на дни `days` — правилом экрана «Домены рассылки» (см. модуль).
    `project_first_letter` — ящик без первого письма напишет его в день `first_letter_day` от минуты
    `now`, и разгон пойдёт оттуда; без него — как экран и «План» отчёта, и `now` не нужен."""
    first = None
    if project_first_letter:
        if now is None:
            raise ValueError(
                "мощность с предположенным первым письмом считается от минуты вопроса: передайте "
                "now — своих часов у правила нет, иначе план и прогноз на те же дни разойдутся"
            )
        first = await first_letter_day(session, stage, now)
    found = await OutreachRepository(session).senders(stages=(stage,))
    boxes = [box for box in found if box.enabled]
    domains = await limits.sending_domains(session)
    limit = cfg.direction_limit(stage.value)
    return Capacity(
        len(boxes),
        [
            DayCapacity(day, day_capacity(boxes, domains, stage, limit, day, first=first))
            for day in days
        ],
        unread(stage),
    )


async def first_letter_day(session: AsyncSession, stage: Stage, now: datetime) -> date:
    """День, в который ящик этапа без первого письма, по предположению прогноза, его напишет:
    первый день, когда первые письма ещё уходят (`opening_day`), — окно получателя из политики
    этапа, часы — в самом частом поясе очереди (`queue_zone`). Одно правило у прогноза рассылки и у
    мощности по дням (`capacity(project_first_letter=True)`)."""
    frame = await _window(session, stage)
    zone = None if frame is None else await queue_zone(session, stage)
    return opening_day(frame, zone, now)


async def queue_zone(session: AsyncSession, stage: Stage) -> ZoneInfo | None:
    """Самый частый пояс получателей первых писем этапа в очереди: у продаж — ответом моста
    (`stage_forecast.sales_zones`); у доноров и рекламодателей окна нет — и пояса не нужно."""
    match stage:
        case Stage.DONORS | Stage.ADVERTISERS:
            return None
        case Stage.SALES:
            return common_zone(await stage_forecast.sales_zones(session) or {})
        case _:
            assert_never(stage)


def common_zone(zones: Mapping[str | None, int]) -> ZoneInfo | None:
    """Самый частый пояс из `zones` (пояс → писем); равные — по имени, чтобы ответ не скакал; имени
    нет в базе поясов — следующий (`window.zone_of`). `None` — поясов не знает никто."""
    ranked = sorted(((count, name) for name, count in zones.items() if name), reverse=True)
    return window.zone_of(name for _, name in ranked)


@dataclass(frozen=True, slots=True)
class Span:
    """Часы отправки первых писем в UTC-день: с какой минуты и до какой (пусто — часов не узнать:
    пояс неизвестен); `closed` — почему в этот день они не уходят вовсе."""

    start: datetime | None = None
    end: datetime | None = None
    closed: str | None = None


def span_of(frame: SendWindow | None, zone: ZoneInfo | None, day: date, now: datetime) -> Span:
    """Часы отправки дня `day` от минуты `now`: без окна — весь UTC-день; с окном — его часы в поясе
    `zone`; день не окна или окно на сегодня закрылось — `closed` словами."""
    if frame is None:
        start = datetime.combine(day, time.min, tzinfo=UTC)
        return Span(max(start, now), start + timedelta(days=1))
    if day.weekday() not in frame.days:
        return Span(closed=f"не день отправки: окно получателя — {frame.words} по его часам")
    hours = None if zone is None else window.hours_of(frame, zone, day)
    if zone is None or hours is None:
        return Span()
    opens, closes = hours
    if now >= closes:
        return Span(
            closed=f"окно получателя на сегодня закрылось в {closes:%H:%M} UTC (пояс {zone.key})"
        )
    return Span(max(opens, now), closes)


def opening_day(frame: SendWindow | None, zone: ZoneInfo | None, now: datetime) -> date:
    """Первый день, когда первые письма ещё уйдут: сегодня, если окно сегодня есть и не закрылось
    (`span_of`), иначе — следующий день окна (`first_sending`)."""
    today = now.date()
    if span_of(frame, zone, today, now).closed is None:
        return today
    return first_sending(today + timedelta(days=1), frame)


async def _window(session: AsyncSession, stage: Stage) -> SendWindow | None:
    """Окно получателя этапа — для первого дня отправки. Модуль продаж не ответил о политике — окна
    не узнать: первый день — первый из дней (строка журнала)."""
    try:
        return (await stages.mail_policy(session, stage, "Мощность ящиков этапа")).window
    except stages.SalesNotConnectedError as exc:
        logger.info("мощность ящиков: окна этапа не узнать — %s", exc, extra={"stage": stage.value})
        return None


def unread(stage: Stage) -> str | None:
    """Лимит направления этапа не прочитан из настроек — что поправить, словами; `None` — прочитан."""
    name = cfg.direction_unread(stage.value)
    return None if name is None else limits.unread_words(name)


@dataclass(frozen=True, slots=True)
class BoxToday:
    """Ящик этапа сегодня — глазами выбора ящика первого письма."""

    sender: SenderModel
    #: Потолок дня: ступень разгона, а при мягких отказах — сниженный на сутки лимит (`cut`).
    allowance: int
    #: Первых писем ушло сегодня — счётом капа: первые письма и перезапросы цены.
    sent: int
    cut: bool = False
    #: Почему первых писем ящик сегодня не берёт — словами фильтра (`limits.screen`); пусто —
    #: берёт или выключен (`sender.enabled`).
    refused: str | None = None


@dataclass(frozen=True, slots=True)
class Today:
    """Сегодня у ящиков этапа: каждый ящик, мощность дня целиком и остаток дня."""

    boxes: tuple[BoxToday, ...]
    #: Мощность дня целиком — правилом экрана, с лимитами, сниженными мягкими отказами.
    capacity: int
    #: Остаток дня: сколько первых писем ещё пустит выбор ящика — по ящику, домену и направлению.
    left: Rooms

    @property
    def sent(self) -> int:
        return sum(box.sent for box in self.boxes)

    @property
    def cut(self) -> str | None:
        """Сниженные на сутки лимиты — словами; `None` — не снижен ни один."""
        boxes = [box.sender.email for box in self.boxes if box.cut]
        if not boxes:
            return None
        return (
            f"мягкие отказы: лимит на сутки снижен вдвое у {', '.join(boxes)} — сегодняшняя "
            "мощность считает сниженный, как выбор ящика"
        )


def today(
    senders: Sequence[SenderModel],
    *,
    stage: Stage,
    sent: Mapping[int, int],
    domains: Mapping[str, SendingDomainModel],
    cuts: Mapping[int, int],
    now: datetime,
) -> Today:
    """Сегодня — фильтром и остатком выбора ящика первого письма (`letters/mailbox._free_box`).
    `senders` — все ящики, как их видит фильтр: домен считает письма всех своих ящиков; `cuts` —
    сниженные на сутки лимиты (`health.cuts`)."""
    limit = cfg.direction_limit(stage.value)
    screened = limits.screen(
        senders,
        stage=stage,
        sent_today=sent,
        domains=domains,
        direction_limit=limit,
        now=now,
        cuts=cuts,
        direction_unread=cfg.direction_unread(stage.value),
    )
    spots = rules.available(screened.fit, sent_today=sent, stage=stage, now=now)
    writing = {spot.sender.id for spot in spots}
    own = tuple(
        _box(box, sent, cuts, screened.refused, now) for box in senders if box.stage is stage
    )
    used: Counter[str] = Counter()
    for box in senders:
        used[box.domain] += sent.get(box.id, 0)
    rooms = ((b.sender, max(0, b.allowance - b.sent)) for b in own if b.sender.id in writing)
    full = ((b.sender, b.allowance) for b in own if b.sender.enabled)
    return Today(
        own,
        gather(full, domains, stage, now, limit).total(),
        gather(rooms, domains, stage, now, limit, used=used, spent=sum(b.sent for b in own)),
    )


def _box(
    box: SenderModel,
    sent: Mapping[int, int],
    cuts: Mapping[int, int],
    refused: Mapping[int, str],
    now: datetime,
) -> BoxToday:
    allowance = rules.warmup_state(box, now=now).allowance
    cut = cuts.get(box.id)
    return BoxToday(
        box,
        allowance if cut is None else min(allowance, cut),
        sent.get(box.id, 0),
        cut is not None,
        refused.get(box.id),
    )


def next_step(box: SenderModel, day: date, *, first: date | None = None) -> tuple[date, int] | None:
    """Когда после дня `day` ящик поднимется на следующую ступень разгона и до скольких писем в
    день; `None` — разгон кончился, ещё не начат или выше ступень не поднимет (кап ящика). Ступень —
    от первого письма: у ящика, ещё не писавшего, — от предположенного `first` (`rooms_on`)."""
    assumed = None if first is None else noon(first)
    now = rules.warmup_state(box, now=noon(day), assumed=assumed).allowance
    for ahead in range(1, len(rules.WARMUP_STEPS) * rules.WARMUP_STEP_DAYS + 1):
        later = day + timedelta(days=ahead)
        allowance = rules.warmup_state(box, now=noon(later), assumed=assumed).allowance
        if allowance > now:
            return later, allowance
    return None
