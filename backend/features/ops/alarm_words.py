"""Слова ленты тревог — что приходит человеку в Telegram (`alarm_feed.py`).

**Тревога — с какого времени, что происходит, где смотреть и есть ли повод** (слово владельца
17.11.2026: «нужно, чтобы алерты стали адекватнее и информативнее»). Пятнадцать ящиков продаж
молчали по одной причине: пришло пятнадцать «тревога» подряд, через десять минут — пятнадцать
«прошло», и ни по одному не было видно, когда это началось, сколько длилось и что делать. Теперь
«тревога» — заголовок и «С 03:11 МСК.», что происходит, экран (`Alarm.screen`) и пометка
последней строкой (`alarms.marked`); «прошло» — когда тревога шла и сколько: «03:11–03:21 МСК,
10 минут.» Заголовок — сразу за словом ленты, как раньше: «тревога: <заголовок>.» и
«прошло: <заголовок>» читатели ленты ищут по началу.

**Род — одним сообщением** (`Alarm.kind`): заголовок рода с числом, общие слова рода и строка на
тревогу — имя (`Alarm.item`) и что с ней сейчас (`Alarm.brief`). Одна тревога рода — обычным
сообщением: её подробности полнее строки списка.

**Время — московское**, минутой: по нему тревоги читают; не сегодняшний день — с датой.
Длительность — по тем же минутам, что стоят в словах: «03:11–03:21» — это «10 минут».

**Потолок Telegram** (`alarms.ROOM`): в списке — первые `LISTED` строк, сколько их влезает рядом
с остальным, и «и ещё M»; пометка не режется.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from backend.features.ops.alarms import ROOM, Alarm, Concern, Kind, marked

#: Часы слов ленты: тревоги читают по московскому времени.
MOSCOW = ZoneInfo("Europe/Moscow")

#: Сколько тревог рода сообщение называет поимённо: двадцать строк — экран телефона, дальше —
#: «и ещё M». Строки длинные — меньше: сообщение остаётся в потолке Telegram.
LISTED = 20

#: Формы слов длительности: одна, две — четыре, пять и больше.
MINUTES = ("минута", "минуты", "минут")
HOURS = ("час", "часа", "часов")
DAYS = ("день", "дня", "дней")


@dataclass(frozen=True, slots=True)
class Raised:
    """Тревога, о которой лента говорит, и с какого времени она идёт (`None` — лента не знает)."""

    alarm: Alarm
    since: datetime | None


@dataclass(frozen=True, slots=True)
class Over:
    """Прошедшая тревога: заголовок, когда шла (начало `None` — лента не знает), род и имя в
    списке рода."""

    title: str
    since: datetime | None
    until: datetime
    kind: str = ""
    item: str = ""


def alarmed(raised: Sequence[Raised], at: datetime) -> str:
    """«Тревога» — одна своими словами, род — списком. `at` — минута прохода: от неё дата."""
    first = raised[0]
    kind = first.alarm.kind
    if kind is None or len(raised) == 1:
        return _alone(first, at)
    return _kinded(kind, raised, at)


def passed(gone: Sequence[Over], at: datetime) -> str:
    """«Прошло» — когда тревога шла и сколько; род — числом и списком имён. У всех одно время —
    оно в заголовке, разное — у каждой строки."""
    spans = [_span(one, at) for one in gone]
    first = gone[0]
    if not first.kind or len(gone) == 1:
        return f"прошло: {first.title}{_sentenced(spans[0])}"
    common = spans[0] if len(set(spans)) == 1 else ""
    entries = [
        f"• {one.item or one.title}{_own(span, common)}"
        for one, span in zip(gone, spans, strict=True)
    ]
    return _fitted([f"прошло: {first.kind} — {len(gone)}{_sentenced(common)}"], entries, [], None)


def clock(moment: datetime, at: datetime) -> str:
    """Минута по Москве: «03:11»; не в день `at` — с датой: «16.11 23:50»."""
    local = moment.astimezone(MOSCOW)
    if local.date() == at.astimezone(MOSCOW).date():
        return f"{local:%H:%M}"
    return f"{local:%d.%m %H:%M}"


def lasted(minutes: int) -> str:
    """«10 минут», «2 часа 5 минут», «3 дня 4 часа» — сутки без минут: они там уже шум."""
    days, rest = divmod(minutes, 24 * 60)
    hours, left = divmod(rest, 60)
    parts = [(days, DAYS), (hours, HOURS)] if days else [(hours, HOURS), (left, MINUTES)]
    return " ".join(counted(number, forms) for number, forms in parts if number)


def counted(number: int, forms: tuple[str, str, str]) -> str:
    """Число со словом в нужной форме: «1 минута», «3 минуты», «11 минут», «21 минута»."""
    tail = number % 10
    if 11 <= number % 100 <= 14 or tail == 0 or tail >= 5:
        return f"{number} {forms[2]}"
    return f"{number} {forms[0] if tail == 1 else forms[1]}"


def _alone(raised: Raised, at: datetime) -> str:
    """Тревога своими словами: заголовок и начало, подробности, экран — и пометка последней."""
    alarm = raised.alarm
    lines = [
        f"тревога: {alarm.title}.{_since(raised.since, at)}",
        _sentence(alarm.detail),
        _screens([alarm]),
    ]
    return marked(_joined(lines), alarm.concern)


def _kinded(kind: Kind, raised: Sequence[Raised], at: datetime) -> str:
    """Тревоги рода списком. Пометка у них одна: лента собирает в сообщение тревоги рода с одной
    пометкой (`alarm_feed._kind_of`)."""
    start = min((one.since for one in raised if one.since is not None), default=None)
    alarms = [one.alarm for one in raised]
    concern = alarms[0].concern
    top = [f"тревога: {kind.title} — {len(raised)}.{_since(start, at)}", _sentence(kind.detail)]
    entries = [_entry(one, start, at) for one in raised]
    return marked(_fitted(top, entries, [_screens(alarms)], concern), concern)


def _entry(one: Raised, start: datetime | None, at: datetime) -> str:
    """Строка тревоги в списке рода: имя, что с ней сейчас и — если она моложе прочих — с какого
    времени."""
    alarm = one.alarm
    words = f"• {alarm.item or alarm.title}"
    if alarm.brief:
        words = f"{words} — {alarm.brief}"
    if one.since is not None and start is not None and _minute(one.since) > _minute(start):
        words = f"{words}, с {clock(one.since, at)}"
    return words


def _fitted(
    top: Sequence[str], entries: Sequence[str], bottom: Sequence[str], concern: Concern | None
) -> str:
    """Сообщение со списком в потолке Telegram: строк списка — не больше `LISTED` и сколько
    влезает рядом с остальным и пометкой; не названные — «и ещё M»."""
    room = ROOM - (0 if concern is None else len(concern.words) + 1)
    shown = list(entries[:LISTED])
    text = _joined([*top, *shown, _more(len(entries) - len(shown)), *bottom])
    while len(text) > room and shown:
        shown.pop()
        text = _joined([*top, *shown, _more(len(entries) - len(shown)), *bottom])
    return text


def _span(one: Over, at: datetime) -> str:
    """«03:11–03:21 МСК, 10 минут» — когда тревога шла и сколько. Начало неизвестно — пусто."""
    if one.since is None:
        return ""
    minutes = int((_minute(one.until) - _minute(one.since)).total_seconds()) // 60
    if minutes <= 0:
        return f"{clock(one.since, at)} МСК, меньше минуты"
    return f"{clock(one.since, at)}–{clock(one.until, at)} МСК, {lasted(minutes)}"


def _since(since: datetime | None, at: datetime) -> str:
    """« С 03:11 МСК.» — хвост заголовка «тревоги»; начало неизвестно — пусто."""
    return "" if since is None else f" С {clock(since, at)} МСК."


def _sentenced(span: str) -> str:
    """«. 03:11–03:21 МСК, 10 минут.» — хвост заголовка «прошло»; времени нет — пусто."""
    return f". {span}." if span else ""


def _own(span: str, common: str) -> str:
    """Своё время строки «прошло» — когда у тревог сообщения оно разное."""
    return f": {span}" if span and not common else ""


def _more(rest: int) -> str:
    return f"• и ещё {rest}" if rest else ""


def _screens(alarms: Sequence[Alarm]) -> str:
    """«Где смотреть: экран «Домены рассылки продаж».» — экраны тревог сообщения без повторов."""
    screens = list(dict.fromkeys(alarm.screen for alarm in alarms if alarm.screen))
    if not screens:
        return ""
    named = ", ".join(f"«{screen}»" for screen in screens)
    return f"Где смотреть: {'экраны' if len(screens) > 1 else 'экран'} {named}."


def _sentence(text: str) -> str:
    """Подробности — предложением: с точкой в конце, если её нет."""
    text = text.strip()
    return text if not text or text.endswith((".", "!", "?", "…")) else f"{text}."


def _joined(lines: Sequence[str]) -> str:
    return "\n".join(line for line in lines if line)


def _minute(moment: datetime) -> datetime:
    """Минута без секунд: длительность — по минутам, что стоят в словах."""
    return moment.replace(second=0, microsecond=0)
