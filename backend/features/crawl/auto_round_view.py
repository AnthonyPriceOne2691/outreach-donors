"""Автокруг рекламодателей на экране: выключатель, итог последнего круга и когда пойдёт первый.

**На виду — одна строка** (`docs/UI_RULES.md`, как у ночного шага судьи): «Автокруг 17.11, 08:12:
переведено 24 · адресов найдено 20 (платных 9) · собрано 20 из 40 · очередь уходит сама» — или что
круг делает сейчас, или почему встал. Всё остальное — строками для «i»: что отсеял перевод, почему
поиск не дождались, во что упёрлась сборка, чего не хватает для отправки. Слова считает сервер,
экран их только называет; время — экран, по часам смотрящего.

Тревогу о круге, который встал, говорит сторож (`auto_round_alarm.py`), а не этот экран.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import auto_round as cfg
from backend.features.core.models.advertiser import AutoRoundModel
from backend.features.crawl import auto_round, auto_round_switch
from backend.features.crawl.auto_round_report import (
    CATCH_UP_UNTIL,
    GOING,
    JUDGE_WAIT_UNTIL,
    OVER,
    SEARCH_WAIT,
    Phase,
    RoundReport,
)
from backend.features.crawl.auto_round_switch import Switch
from backend.features.crawl.night import MOSCOW
from backend.shared.numbers import spaced


@dataclass(frozen=True, slots=True)
class RoundSummary:
    """Последний круг для строки у выключателя и её «i»."""

    day: date
    status: str
    #: Когда кончился — или начался, если ещё идёт; у пропущенной даты времени нет.
    at: datetime | None
    #: Строка после «Автокруг 17.11, 08:12:» — что сделано, что делается или почему встал.
    words: str
    #: Круг ещё идёт.
    going: bool = False
    lines: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class RoundView:
    """Автокруг на экране: можно ли, как стоит выключатель, что было и когда пойдёт первый."""

    #: Рубильник сервиса включён: без него выключателя нет.
    available: bool
    switch: Switch
    last: RoundSummary | None
    #: Круг этой даты уже был: включённый сейчас пойдёт завтра.
    ran_today: bool
    #: Когда пойдёт первый круг, если включить сейчас, — словами для вопроса у кнопки.
    first: str


def _build_part(report: RoundReport) -> str:
    """Сборка частью строки: собрано из скольких ждавших; потолок модели — словами, без числа
    ждавших: до них сборка не дошла."""
    if not report.campaign:
        return "сборки не было — потолок модели выбран" if report.stopped else ""
    if report.stopped:
        return f"собрано {spaced(report.prepared)} — упёрлось в потолок модели"
    return f"собрано {spaced(report.prepared)} из {spaced(report.waiting)}"


def _parts(report: RoundReport) -> list[str]:
    """Что круг сделал — частями строки. Поиска не было — о нём ни слова: «найдено 0» читалось бы
    как поиск впустую."""
    parts = [f"переведено {spaced(report.promoted)}"]
    if report.found or report.searched:
        paid = f" (платных {spaced(report.paid)})" if report.paid else ""
        parts.append(f"адресов найдено {spaced(report.found)}{paid}")
    parts += [_build_part(report), report.queue]
    return [part for part in parts if part]


def _lines(row: AutoRoundModel, report: RoundReport) -> list[str]:
    """Строки «i»: что за числами строки и почему круг сделал меньше, чем мог."""
    extra = (
        ("обновлено переведённых раньше", report.updated),
        ("отсеяно стоп-листами", report.dropped),
        ("ждут проверки DR", report.waiting_dr),
    )
    lines = [f"перевод: {words} — {spaced(count)}" for words, count in extra if count]
    hours = int(SEARCH_WAIT.total_seconds() // 3600)
    said = (
        (
            report.search_cut,
            f"поиск адресов не кончился за {hours} ч — собрано тем, у кого адрес был",
        ),
        (report.search_error, f"поиск адресов: {report.search_error}"),
        (report.campaign, f"рассылка «{report.campaign}», писем в сутки — до {report.letters}"),
        (report.stopped, f"потолок модели: {report.stopped}"),
        (report.unchecked, f"проверка адреса перед письмом: {report.unchecked}"),
        (report.blocked_by, f"отправить нельзя, пока не заполнено: {', '.join(report.blocked_by)}"),
        (row.note, row.note or ""),
    )
    return lines + [words for flag, words in said if flag]


def _words(row: AutoRoundModel, report: RoundReport) -> str:
    """Строка после даты: идёт — что делает; встал или не начался — почему; кончился — итог."""
    going = GOING.get(row.status)
    if going is not None:
        return going
    if row.status == Phase.MISSED.value:
        return f"не начался — {row.note}"
    if row.status == Phase.FAILED.value:
        return f"встал — {row.note}"
    parts = _parts(report)
    if row.status == Phase.STOPPED.value:
        parts.insert(0, "выключен посреди круга")
    return " · ".join(parts)


def summary_of(row: AutoRoundModel) -> RoundSummary:
    """Строка круга → итог для экрана."""
    report = RoundReport.of(row.report)
    # У пропущенной даты времени нет: минута, когда её записали пропущенной, — уже чужой день.
    missed = row.status == Phase.MISSED.value
    return RoundSummary(
        day=row.day,
        status=row.status,
        at=None if missed else row.finished_at or row.started_at,
        words=_words(row, report),
        going=row.status not in OVER,
        lines=_lines(row, report),
    )


async def _last(session: AsyncSession) -> AutoRoundModel | None:
    found: AutoRoundModel | None = await session.scalar(
        select(AutoRoundModel).order_by(AutoRoundModel.day.desc()).limit(1)
    )
    return found


async def _first(session: AsyncSession, moment: datetime, *, ran_today: bool) -> str:
    """Когда пойдёт первый круг, если включить сейчас."""
    if ran_today or moment.astimezone(MOSCOW).time() >= CATCH_UP_UNTIL:
        return "Первый круг — завтра утром, после ночного шага судьи."
    if await auto_round.ready(session, moment):
        return "Сегодняшнего круга ещё не было: он начнётся в ближайшие минуты."
    return (
        f"Первый круг — сегодня, после ночного шага судьи, не позже {JUDGE_WAIT_UNTIL:%H:%M} "
        "по Москве."
    )


async def view(session: AsyncSession, *, now: datetime) -> RoundView:
    """Выключатель, последний круг и когда пойдёт первый — одним ответом для экрана."""
    last = await _last(session)
    ran_today = last is not None and last.day >= now.astimezone(MOSCOW).date()
    return RoundView(
        available=cfg.ENABLED,
        switch=await auto_round_switch.state(session),
        last=None if last is None else summary_of(last),
        ran_today=ran_today,
        first=await _first(session, now, ran_today=ran_today),
    )
