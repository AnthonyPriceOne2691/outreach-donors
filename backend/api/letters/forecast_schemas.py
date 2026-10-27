"""Что отдаёт прогноз рассылки (`GET /api/letters/forecast`) — экран «Рассылка».

Плоско и с объяснением каждого поля: вкладку строят поверх этого ответа, а правила за числами живут
в ядре (`letters/forecast.py`), не во фронте — вторая копия правила во фронте разошлась бы с ядром
при первой правке. Дни — по UTC, как дневной лимит ящиков; «первые письма» — счётом этого лимита
(первое письмо и перезапрос цены), добивки — отдельно: в лимит они не входят.

Имена схем — с приставкой: клиент собирает типы по именам, и две одноимённые схемы в одном описании
API разъехались бы молча (как `QueuedLetterCard` рядом с `LetterCard`).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from pydantic import BaseModel

from backend.features.core.domain import Stage
from backend.features.core.window import Pace
from backend.features.letters.forecast import Forecast, Mailbox
from backend.features.letters.forecast_plan import Eta, PlanDay
from backend.features.outreach.senders import first_letter


class ForecastPace(BaseModel):
    """Темп автоотправки первых писем: с ящика — письмо за проход и пауза между его письмами."""

    #: Пауза между первыми письмами ящика, минут.
    gap_minutes: int
    #: Разброс паузы в обе стороны — доля: 0.5 — ±50%.
    spread: float

    @classmethod
    def of(cls, pace: Pace) -> ForecastPace:
        return cls(gap_minutes=round(pace.gap / timedelta(minutes=1)), spread=pace.spread)


class ForecastToday(BaseModel):
    """Сегодня у этапа: ушло, мощность дня и сколько ещё пустит выбор ящика."""

    #: Первых писем ушло сегодня — счётом дневного лимита.
    sent_first: int
    #: Добивок ушло сегодня; в дневной лимит они не входят.
    sent_followups: int
    #: Мощность дня целиком — сколько первых писем пускают сегодня ящики, домены и направление,
    #: с лимитами, сниженными мягкими отказами (`cut`).
    allowance: int
    #: Сколько первых писем ещё пустит выбор ящика сегодня — без окна получателя и темпа (их
    #: учитывает план дня: `days[0].planned_first`).
    remaining_first: int
    #: У каких ящиков лимит снижен на сутки мягкими отказами — словами; пусто — ни у каких.
    cut: str | None


class ForecastDay(BaseModel):
    """День плана: сегодня и дни вперёд."""

    day: date
    #: День отправки: окно получателя в этот день есть, а сегодня — ещё не закрылось.
    sending_day: bool
    #: Почему писем нет или меньше мощности — словами; пусто — уходят во всю мощность.
    why_not: str | None
    #: Мощность ящиков этапа: сколько первых писем они могут отправить (растёт с разгоном; ящик,
    #: ещё не писавший, начинает разгон в первый день отправки); в день без окна — ноль. Сегодня —
    #: мощность дня целиком.
    capacity_first: int
    #: Сколько первых писем уйдёт: мощность, но не больше, чем есть что отправить; сегодня — из
    #: остатка дня.
    planned_first: int
    #: Добивок, чей срок подходит в этот день; просроченные — сегодня. Срок в день без окна
    #: получателя ждёт его открытия — такие здесь в следующем дне отправки, а в дне без окна ноль.
    followups_due: int

    @classmethod
    def of(cls, found: PlanDay) -> ForecastDay:
        return cls(
            day=found.day,
            sending_day=found.sending,
            why_not=found.why_not,
            capacity_first=found.capacity,
            planned_first=found.planned,
            followups_due=found.followups,
        )


class ForecastStuckReason(BaseModel):
    """Почему письма очереди не уйдут, пока их не поправят, — словами, и сколько их."""

    words: str
    letters: int


class ForecastStuck(BaseModel):
    """Письма очереди, которые не уйдут, пока что-то не поправят (у продаж: адрес лида держит
    проверка, пояс получателя неизвестен). Срок очереди и план дней их не считают."""

    letters: int
    #: По причинам — их единицы, не больше, чем причин у модуля этапа.
    reasons: list[ForecastStuckReason]

    @classmethod
    def of(cls, found: dict[str, int]) -> ForecastStuck:
        return cls(
            letters=sum(found.values()),
            reasons=[ForecastStuckReason(words=words, letters=n) for words, n in found.items()],
        )


class ForecastPastDay(BaseModel):
    """День назад: сколько ушло на самом деле."""

    day: date
    sent_first: int
    sent_followups: int


class ForecastEta(BaseModel):
    """Когда уйдёт последнее письмо, стоящее в очереди сейчас (без писем, которые сборка ещё
    напишет)."""

    #: День ухода последнего письма очереди; пусто — срока нет (почему — `words`).
    day: date | None
    #: Сколько дней отправки на это уйдёт, считая сегодня, если сегодня письма ещё уходят.
    sending_days: int | None
    #: Часов от сейчас до ухода последнего письма — только у автоотправки (`pace`): вручную
    #: письма уходят пачкой по нажатию, и часов нет.
    hours: float | None
    #: Как посчитано или почему срока нет — словами; есть всегда.
    words: str

    @classmethod
    def of(cls, found: Eta) -> ForecastEta:
        return cls(
            day=found.day, sending_days=found.sending_days, hours=found.hours, words=found.words
        )


class ForecastSender(BaseModel):
    """Ящик этапа: разгон, сегодняшний лимит и следующая ступень."""

    id: int
    email: str
    domain: str
    enabled: bool
    #: День разгона: ступень считается от первого письма ящика; 0 — ящик без разгона.
    warmup_day: int
    #: Разгон кончился: потолок — кап ящика.
    warmup_finished: bool
    #: Первое письмо этого разгона; пусто — ещё не писал, и ступень пойдёт с первого письма.
    first_letter_at: datetime | None
    #: Кап ящика — потолок, к которому идёт разгон.
    daily_cap: int
    #: Потолок сегодня: ступень разгона, а при мягких отказах — сниженный на сутки лимит.
    allowance_today: int
    #: Первых писем ушло сегодня с ящика — счётом дневного лимита.
    sent_today: int
    #: Лимит на сутки снижен мягкими отказами.
    cut: bool
    #: Почему первых писем ящик сегодня не берёт — словами фильтра лимитов; пусто — берёт (или
    #: выключен: `enabled`).
    refused: str | None
    #: Когда ящик поднимется на следующую ступень разгона; пусто — не поднимется: разгон кончился
    #: или ящик выключен.
    #: У ящика, ещё не писавшего, — от первого письма, которое прогноз ему предполагает в первый
    #: день отправки (`first_letter_at` тогда пусто).
    next_step_on: date | None
    #: До скольких писем в день — с той ступени.
    next_step_allowance: int | None

    @classmethod
    def of(cls, found: Mailbox) -> ForecastSender:
        box, step = found.today, found.next_step
        return cls(
            id=box.sender.id,
            email=box.sender.email,
            domain=box.sender.domain,
            enabled=box.sender.enabled,
            warmup_day=found.warmup.day,
            warmup_finished=found.warmup.finished,
            first_letter_at=first_letter(box.sender),
            daily_cap=box.sender.daily_cap,
            allowance_today=box.allowance,
            sent_today=box.sent,
            cut=box.cut,
            refused=box.refused,
            next_step_on=None if step is None else step[0],
            next_step_allowance=None if step is None else step[1],
        )


class MailingForecastView(BaseModel):
    """Прогноз рассылки этапа целиком: очередь, сегодня, план по дням, факт, срок очереди, ящики.

    Числа честные: день без окна получателя — ноль с причиной (`why_not`), а не мощность ящиков,
    которую некому потратить; чего прогноз не знает — пусто, а не ноль (`supply_extra`, `hours`).
    """

    stage: Stage
    #: Минута, на которую посчитан прогноз (UTC).
    now: datetime
    #: Первых писем этапа ждёт в очереди — тот же счёт, что у пачки «Отправить очередь».
    queued_first: int
    #: Из них не уйдут, пока что-то не поправят (у продаж); срок очереди и план дней — без них.
    #: Пусто — у этапа такого счёта нет или модуль этапа не ответил.
    queued_stuck: ForecastStuck | None
    #: Сколько первых писем ещё напишет сборка (у продаж — лиды «готов» без письма, которым она
    #: напишет, её же правилом); пусто — не узнать или у этапа сборка не пишет сама.
    supply_extra: int | None
    #: Окно получателя по его часам, «пн–пт 09:00–17:00»; пусто — письма уходят в любой час.
    window: str | None
    #: Пояс, в котором посчитаны часы окна, — самый частый у получателей очереди; пусто — неизвестен.
    zone: str | None
    #: Темп автоотправки; пусто — автоотправки нет, письма уходят пачкой по нажатию.
    pace: ForecastPace | None
    #: Почему первые письма этапа сейчас не уходят вовсе — словами; пусто — уходят.
    blocked: str | None
    #: Почему автоотправка продаж сейчас стоит — словами её строки («исход письма №38 неизвестен»);
    #: пусто — идёт, выключена или стоит по тому, что прогноз считает сам (очередь пуста, вне окна,
    #: дневные лимиты). План и срок очереди — при условии, что остановку снимут.
    paused: str | None
    #: Лимит направления не прочитан из настроек — что поправить; пусто — прочитан.
    unread_limit: str | None
    today: ForecastToday
    #: Сегодня и дни вперёд — не больше месяца (`days` запроса).
    days: list[ForecastDay]
    #: Дни назад, от давних к вчерашнему, — не больше месяца (`back` запроса).
    past: list[ForecastPastDay]
    queue_eta: ForecastEta
    #: Ящики этапа — включённые и выключенные: новые появляются здесь сами.
    senders: list[ForecastSender]

    @classmethod
    def of(cls, found: Forecast) -> MailingForecastView:
        clock = found.clock
        return cls(
            stage=found.stage,
            now=found.now,
            queued_first=found.queued,
            queued_stuck=None if found.stuck is None else ForecastStuck.of(found.stuck),
            supply_extra=found.extra,
            window=None if clock.window is None else clock.window.words,
            zone=None if clock.zone is None else clock.zone.key,
            pace=None if clock.pace is None else ForecastPace.of(clock.pace),
            blocked=found.blocked,
            paused=found.paused,
            unread_limit=found.unread,
            today=ForecastToday(
                sent_first=found.sent_today.first,
                sent_followups=found.sent_today.followups,
                allowance=found.today.capacity,
                remaining_first=found.today.left.total(),
                cut=found.today.cut,
            ),
            days=[ForecastDay.of(day) for day in found.days],
            past=[
                ForecastPastDay(day=day, sent_first=sent.first, sent_followups=sent.followups)
                for day, sent in found.past
            ],
            queue_eta=ForecastEta.of(found.eta),
            senders=[ForecastSender.of(box) for box in found.mailboxes],
        )
