"""Окно получателя — чистые функции (Ф4, срез 4.3): пояса, переходы часов, случайный сдвиг.

Окно — местное время получателя, а сутки и переходы часов — его пояса. Ошибка здесь
невидима на тестах в UTC: открытие «понедельник 09:00» в Берлине — 07:00 UTC летом
и 08:00 зимой. Поэтому примеры — на разных поясах и на самих неделях перевода часов.
"""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from backend.features.core import window
from backend.features.core.window import SendWindow

WEEKDAYS_9_17 = SendWindow(days=frozenset(range(5)), start=time(9), end=time(17))
NIGHT = SendWindow(days=frozenset(range(7)), start=time(2, 30), end=time(5))
BERLIN = ZoneInfo("Europe/Berlin")


class Fixed(random.Random):
    """Генератор с одним и тем же числом: сдвиг 0 — ровно открытие окна."""

    def __init__(self, value: float = 0.0) -> None:
        super().__init__()
        self.value = value

    def random(self) -> float:
        return self.value


def _at(zone: str, *parts: int) -> datetime:
    """Минута по часам пояса `zone` — в UTC, как её видит отправка."""
    return datetime(*parts, tzinfo=ZoneInfo(zone)).astimezone(UTC)


def test_a1_saturday_moves_to_monday_nine_to_half_past_by_the_generator() -> None:
    saturday = _at("Europe/Berlin", 2026, 10, 10, 10, 0)
    monday_nine = _at("Europe/Berlin", 2026, 10, 12, 9, 0)

    shifts = [
        window.defer_until(WEEKDAYS_9_17, BERLIN, saturday, random.Random(s)) for s in range(40)
    ]

    assert shifts[11] == monday_nine + timedelta(minutes=30) * random.Random(11).random()
    assert len(set(shifts)) > 1
    assert all(
        s is not None and monday_nine <= s < monday_nine + timedelta(minutes=30) for s in shifts
    )


def test_the_shift_never_runs_past_a_short_window() -> None:
    short = SendWindow(days=frozenset(range(7)), start=time(9), end=time(9, 10))

    until = window.defer_until(
        short, BERLIN, _at("Europe/Berlin", 2026, 10, 7, 8, 0), Fixed(0.9999)
    )

    assert until is not None
    assert window.is_open(short, BERLIN, until)


@pytest.mark.parametrize(
    ("local", "expected"),
    [
        ((2026, 10, 6, 12, 0), None),  # вторник, полдень — уходит сейчас
        ((2026, 10, 6, 7, 59), (2026, 10, 6, 9, 0)),  # до открытия — сегодня в девять
        ((2026, 10, 6, 17, 0), (2026, 10, 7, 9, 0)),  # закрылось — завтра
        ((2026, 10, 9, 17, 30), (2026, 10, 12, 9, 0)),  # пятница вечером — понедельник
        ((2026, 10, 11, 23, 59), (2026, 10, 12, 9, 0)),
    ],
)
def test_opening_is_counted_by_the_local_day(
    local: tuple[int, ...], expected: tuple[int, ...] | None
) -> None:
    until = window.defer_until(WEEKDAYS_9_17, BERLIN, _at("Europe/Berlin", *local), Fixed())

    assert until == (_at("Europe/Berlin", *expected) if expected else None)


@pytest.mark.parametrize(
    ("zone", "moment", "open_now"),
    [
        # В Окленде уже понедельник 09:30, в UTC ещё воскресенье; 08:59 — закрыто.
        ("Pacific/Auckland", datetime(2026, 10, 11, 20, 30, tzinfo=UTC), True),
        ("Pacific/Auckland", datetime(2026, 10, 11, 19, 59, tzinfo=UTC), False),
        # Полчаса сдвига: в Калькутте 08:59 — закрыто, 09:00 — открыто.
        ("Asia/Kolkata", datetime(2026, 10, 12, 3, 29, tzinfo=UTC), False),
        ("Asia/Kolkata", datetime(2026, 10, 12, 3, 30, tzinfo=UTC), True),
        # В Гонолулу пятница 16:00 — открыто, хотя в UTC уже суббота; 17:00 — закрыто.
        ("Pacific/Honolulu", datetime(2026, 10, 10, 2, 0, tzinfo=UTC), True),
        ("Pacific/Honolulu", datetime(2026, 10, 10, 3, 0, tzinfo=UTC), False),
    ],
)
def test_the_window_lives_in_the_recipient_zone(
    zone: str, moment: datetime, open_now: bool
) -> None:
    assert window.is_open(WEEKDAYS_9_17, ZoneInfo(zone), moment) is open_now


@pytest.mark.parametrize(
    ("zone", "saturday", "monday_utc"),
    [
        # Берлин: часы вперёд 29.03 — понедельник 09:00 = 07:00 UTC; назад 25.10 — 08:00 UTC.
        ("Europe/Berlin", (2026, 3, 28, 10, 0), datetime(2026, 3, 30, 7, 0, tzinfo=UTC)),
        ("Europe/Berlin", (2026, 10, 24, 10, 0), datetime(2026, 10, 26, 8, 0, tzinfo=UTC)),
        # Нью-Йорк: часы вперёд 08.03 — с пятничного вечера на понедельник 09:00 EDT.
        ("America/New_York", (2026, 3, 6, 18, 0), datetime(2026, 3, 9, 13, 0, tzinfo=UTC)),
        # Сидней, южное полушарие: часы назад 05.04, вперёд 04.10.
        ("Australia/Sydney", (2026, 4, 4, 10, 0), datetime(2026, 4, 5, 23, 0, tzinfo=UTC)),
        ("Australia/Sydney", (2026, 10, 3, 10, 0), datetime(2026, 10, 4, 22, 0, tzinfo=UTC)),
    ],
)
def test_the_week_of_a_clock_change_opens_at_local_nine(
    zone: str, saturday: tuple[int, ...], monday_utc: datetime
) -> None:
    until = window.defer_until(WEEKDAYS_9_17, ZoneInfo(zone), _at(zone, *saturday), Fixed())

    assert until == monday_utc


@pytest.mark.parametrize(
    ("zone", "night", "frame", "opens"),
    [
        # Берлин 29.03: 02:30 не наступает (02:00 → 03:00) — первая минута 03:00.
        ("Europe/Berlin", datetime(2026, 3, 29, 0, 0, tzinfo=UTC), NIGHT, (1, 0)),
        # Берлин 25.10: 02:30 бывает дважды — первое, ещё летнее.
        ("Europe/Berlin", datetime(2026, 10, 24, 23, 0, tzinfo=UTC), NIGHT, (0, 30)),
        # Гавана переводит часы в полночь 08.03: 00:30 не бывает, первая минута — 01:00.
        (
            "America/Havana",
            datetime(2026, 3, 8, 4, 50, tzinfo=UTC),
            SendWindow(days=frozenset(range(7)), start=time(0, 30), end=time(5)),
            (5, 0),
        ),
    ],
)
def test_a_start_inside_a_clock_change(
    zone: str, night: datetime, frame: SendWindow, opens: tuple[int, int]
) -> None:
    until = window.defer_until(frame, ZoneInfo(zone), night, Fixed())

    assert until is not None
    assert (until.hour, until.minute, until.tzinfo) == (*opens, UTC)


def test_a2_the_first_known_zone_decides_and_an_unknown_name_gives_way(
    caplog: pytest.LogCaptureFixture,
) -> None:
    tokyo, new_york = ZoneInfo("Asia/Tokyo"), ZoneInfo("America/New_York")

    assert window.zone_of(["Asia/Tokyo", "Europe/Berlin", "America/New_York"]) == tokyo
    assert window.zone_of([None, "Europe/Berlin", "America/New_York"]) == BERLIN
    assert window.zone_of([None, None, "America/New_York"]) == new_york
    assert window.zone_of([None, "", None]) is None
    with caplog.at_level(logging.WARNING, logger=window.__name__):
        assert window.zone_of(["Mars/Olympus_Mons", "Europe/Berlin"]) == BERLIN
    assert "Mars/Olympus_Mons" in caplog.text


def test_check_says_why_and_how_long(monkeypatch: pytest.MonkeyPatch) -> None:
    saturday = _at("Europe/Berlin", 2026, 10, 10, 10, 0)
    monkeypatch.setattr(window, "JITTER", random.Random(3))

    late = window.check(WEEKDAYS_9_17, ["Europe/Berlin"], saturday)

    assert late is not None
    assert late.delay is not None
    assert "пн–пт 09:00–17:00" in late.words
    assert window.local_words(saturday + late.delay, BERLIN) in late.words
    assert window.local_words(saturday + late.delay, BERLIN).startswith("пн 12.10 09:")
    # Пояса нет — отказ без срока; окна нет (доноры) — письмо уходит в любой час.
    assert window.check(WEEKDAYS_9_17, [None, None], saturday) == window.Late(window.NO_ZONE, None)
    assert window.check(None, [], saturday) is None


@pytest.mark.parametrize(
    ("days", "words"),
    [
        (frozenset(range(5)), "пн–пт 09:00–17:00"),
        (frozenset({0, 2, 4}), "пн,ср,пт 09:00–17:00"),
        (frozenset({6}), "вс 09:00–17:00"),
    ],
)
def test_the_window_says_itself_in_words(days: frozenset[int], words: str) -> None:
    assert SendWindow(days=days, start=time(9), end=time(17)).words == words


@pytest.mark.parametrize(
    ("days", "start", "end"),
    [
        (frozenset(), time(9), time(17)),
        (frozenset({7}), time(9), time(17)),
        (frozenset({0}), time(17), time(9)),
    ],
)
def test_a_window_that_never_opens_is_refused(days: frozenset[int], start: time, end: time) -> None:
    with pytest.raises(ValueError, match="окно"):
        SendWindow(days=days, start=start, end=end)


def test_postpone_takes_the_term_of_the_window_and_the_usual_pause_otherwise() -> None:
    hour, term = timedelta(hours=1), timedelta(hours=46)

    assert window.postpone(window.DeferredError("ждёт окна", delay=term), hour) == term
    assert window.postpone(window.DeferredError("пояса нет"), hour) == hour
    assert window.postpone(RuntimeError("ящик на паузе"), hour) == hour
