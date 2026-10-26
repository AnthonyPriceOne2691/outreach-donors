"""План рассылки по дням — чистый счёт, без базы (`letters/forecast_plan.py`).

Мощность дня — правило экрана «Домены рассылки» (`outreach/capacity.py`), сюда она приходит
готовой; здесь — что из неё уйдёт: разгон по дням, выходные с окном получателя — ноль с причиной,
запас, который кончается посреди недели, сегодняшний остаток со сниженным лимитом и срок очереди —
когда очередь умещается в сегодня, когда тянется на дни и когда писать нечем. Ящики и домены —
выдуманные, в памяти.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.features.core.domain import SenderStatus, Stage
from backend.features.core.models.outreach import SenderModel, SendingDomainModel
from backend.features.core.window import Pace, SendWindow
from backend.features.letters.forecast_plan import (
    Clock,
    Mailboxes,
    PlanDay,
    Supply,
    first_open,
    plan,
)
from backend.features.outreach import capacity
from backend.features.outreach.capacity import Rooms

#: Среда, 10:00 UTC; окно в Берлине (летнее время до 25.10) — 07:00–15:00 UTC.
NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
BERLIN = ZoneInfo("Europe/Berlin")
WEEKDAYS_9_17 = SendWindow(days=frozenset(range(5)), start=time(9), end=time(17))
PACE = Pace(gap=timedelta(minutes=15), spread=0.5)


def _box(
    number: int, *, domain: str = "mail-a.example.test", cap: int = 20, wrote: int | None = 0
) -> SenderModel:
    """Ящик этапа: `wrote` — сколько дней назад ушло первое письмо разгона; `None` — ещё не писал."""
    started = NOW - timedelta(days=100)
    return SenderModel(
        id=number,
        domain=domain,
        email=f"box{number}@{domain}",
        stage=Stage.SALES,
        daily_cap=cap,
        status=SenderStatus.FREE,
        enabled=True,
        warmup_started_at=started,
        warmup_first_sent_at=None if wrote is None else NOW - timedelta(days=wrote),
    )


def _boxes(*senders: SenderModel, sent: int = 0, blocked: str | None = None) -> Mailboxes:
    """Ящики для плана — правилом экрана: сегодня — остаток дня, дальше — потолок разгона дня."""
    today = capacity.rooms_on(senders, {}, Stage.SALES, None, NOW.date())
    left = Rooms(
        {name: tuple(max(0, room - sent) for room in rooms) for name, rooms in today.boxes.items()},
        today.domains,
    )
    return Mailboxes(
        left=left,
        capacity=today.total(),
        later=lambda day: capacity.rooms_on(senders, {}, Stage.SALES, None, day),
        sent=sent * len(senders),
        blocked=blocked,
    )


def _by_day(days: list[PlanDay]) -> list[tuple[str, int, int]]:
    return [(f"{day.day:%a}", day.capacity, day.planned) for day in days]


# --- разгон и дни отправки ------------------------------------------------------------------


def test_capacity_grows_with_warmup_day_by_day() -> None:
    """Ступень — каждые два дня от первого письма ящика: 5, 5, 10, 10, 15, 15, 20 — и план
    повторяет мощность, пока есть что отправить. У этапа без окна пишут все дни."""
    days, _ = plan(NOW, 7, Clock(), _boxes(_box(1)), Supply(queued=500), {})

    assert [day.capacity for day in days] == [5, 5, 10, 10, 15, 15, 20]
    assert [day.planned for day in days] == [5, 5, 10, 10, 15, 15, 20]
    assert all(day.sending and day.why_not is None for day in days)


def test_a_finished_warmup_stays_at_the_mailbox_cap() -> None:
    days, _ = plan(NOW, 3, Clock(), _boxes(_box(1, cap=12, wrote=40)), Supply(queued=500), {})

    assert [day.capacity for day in days] == [12, 12, 12]


def test_weekend_of_a_weekday_window_is_zero_with_the_reason() -> None:
    """Сб и вс окна пн–пт — ноль с причиной словами, а не мощность ящиков: письма в эти дни не уйдут."""
    clock = Clock(WEEKDAYS_9_17, BERLIN)
    days, _ = plan(NOW, 7, clock, _boxes(_box(1, wrote=40)), Supply(queued=500), {})

    assert _by_day(days) == [
        ("Wed", 20, 20),
        ("Thu", 20, 20),
        ("Fri", 20, 20),
        ("Sat", 0, 0),
        ("Sun", 0, 0),
        ("Mon", 20, 20),
        ("Tue", 20, 20),
    ]
    saturday = days[3]
    assert not saturday.sending
    assert saturday.why_not == "не день отправки: окно получателя — пн–пт 09:00–17:00 по его часам"


def test_today_after_the_window_closed_is_zero_with_the_hour() -> None:
    late = NOW.replace(hour=16)  # окно в Берлине закрылось в 15:00 UTC
    days, _ = plan(late, 2, Clock(WEEKDAYS_9_17, BERLIN), _boxes(_box(1)), Supply(queued=50), {})

    assert (days[0].sending, days[0].planned) == (False, 0)
    assert (
        days[0].why_not == "окно получателя на сегодня закрылось в 15:00 UTC (пояс Europe/Berlin)"
    )
    assert days[1].planned == 5


# --- что есть отправить ---------------------------------------------------------------------


def test_supply_running_out_mid_week_caps_the_plan() -> None:
    """Очередь и запас сборки кончаются в пятницу: дальше мощность есть, а отправлять нечего."""
    boxes = _boxes(_box(1, cap=10, wrote=40))
    days, eta = plan(NOW, 5, Clock(), boxes, Supply(queued=15, extra=10), {})

    assert [day.planned for day in days] == [10, 10, 5, 0, 0]
    assert days[2].why_not == (
        "отправлять больше нечего: кончились и очередь, и лиды, которым сборка напишет"
    )
    assert (eta.day, eta.sending_days) == (date(2026, 10, 22), 2)


def test_without_a_known_supply_only_the_queue_goes() -> None:
    days, _ = plan(NOW, 2, Clock(), _boxes(_box(1, cap=10, wrote=40)), Supply(queued=4), {})

    assert [day.planned for day in days] == [4, 0]
    assert days[1].why_not == (
        "отправлять больше нечего: очередь кончилась — новые письма ставит сборка очереди"
    )


def test_today_uses_what_is_left_of_the_day() -> None:
    """Сегодня уже ушло по три с ящика: уйдёт остаток, а мощность дня — целиком."""
    boxes = _boxes(_box(1, wrote=40), _box(2, wrote=40), sent=3)
    days, _ = plan(NOW, 2, Clock(), boxes, Supply(queued=500), {})

    assert (days[0].capacity, days[0].planned) == (40, 34)
    assert days[0].why_not == "сегодня уже ушло 6 первых писем — остаток дня 34"
    assert (days[1].capacity, days[1].planned) == (40, 40)


def test_a_domain_shared_with_another_direction_is_named_as_the_reason() -> None:
    """Лимит домена делят направления (`capacity.today` считает письма всех ящиков домена): ящик
    доноров выбрал восемь из десяти — продажам сегодня два, и это сказано, а не «ящиков нет»."""
    sales, donors = _box(1, wrote=40), _box(2, wrote=40)
    donors.stage = Stage.DONORS
    domains = {
        "mail-a.example.test": SendingDomainModel(
            domain="mail-a.example.test", stage=Stage.SALES, daily_limit=10
        )
    }
    found = capacity.today(
        [sales, donors], stage=Stage.SALES, sent={2: 8}, domains=domains, cuts={}, now=NOW
    )
    boxes = Mailboxes(
        left=found.left, capacity=found.capacity, later=lambda _day: found.left, sent=found.sent
    )

    days, _ = plan(NOW, 1, Clock(), boxes, Supply(queued=50), {})

    assert (days[0].capacity, days[0].planned) == (10, 2)
    assert days[0].why_not == (
        "остаток дня 2: часть лимита домена выбрали ящики другого направления на нём"
    )


def test_today_without_capacity_says_what_the_filter_said() -> None:
    boxes = Mailboxes(
        left=Rooms({}, {}),
        capacity=0,
        later=lambda _day: Rooms({}, {}),
        idle="ящики этапа не пишут",
        refused="домен на паузе: mail-a.example.test — жалоба",
    )

    days, _ = plan(NOW, 2, Clock(), boxes, Supply(queued=5), {})

    assert [day.why_not for day in days] == [
        "домен на паузе: mail-a.example.test — жалоба",
        "ящики этапа не пишут",
    ]


def test_followups_are_shown_on_their_day_and_not_counted_in_the_cap() -> None:
    due = {NOW.date(): 7, NOW.date() + timedelta(days=2): 3}
    days, _ = plan(NOW, 3, Clock(), _boxes(_box(1, wrote=40)), Supply(queued=500), due)

    assert [day.followups for day in days] == [7, 0, 3]
    assert [day.planned for day in days] == [20, 20, 20]


def test_followups_due_on_a_closed_day_move_to_the_next_sending_day() -> None:
    """Окно судит и добивки: срок субботы и воскресенья ждёт понедельника, а у этапа без окна —
    остаётся на своём дне."""
    saturday, sunday, monday = (NOW.date() + timedelta(days=n) for n in (3, 4, 5))
    due = {saturday: 4, sunday: 2, monday: 1}
    boxes = _boxes(_box(1, wrote=40))

    windowed, _ = plan(NOW, 7, Clock(WEEKDAYS_9_17, BERLIN), boxes, Supply(queued=5), due)
    windowless, _ = plan(NOW, 7, Clock(), boxes, Supply(queued=5), due)

    assert [day.followups for day in windowed] == [0, 0, 0, 0, 0, 7, 0]
    assert [day.followups for day in windowless] == [0, 0, 0, 4, 2, 1, 0]


def test_a_closed_window_today_sends_todays_followups_to_the_next_sending_day() -> None:
    late = NOW.replace(hour=16)
    days, _ = plan(
        late, 2, Clock(WEEKDAYS_9_17, BERLIN), _boxes(_box(1)), Supply(5), {late.date(): 3}
    )

    assert [day.followups for day in days] == [0, 3]


def test_stuck_letters_stay_out_of_the_plan_and_the_queue_date() -> None:
    """Десять писем в очереди, четыре не уйдут, пока их не поправят: план и срок — по шести."""
    boxes = _boxes(_box(1, cap=4, wrote=40))
    days, eta = plan(NOW, 3, Clock(), boxes, Supply(queued=10, stuck=4), {})

    assert [day.planned for day in days] == [4, 2, 0]
    assert (eta.day, eta.sending_days) == (NOW.date() + timedelta(days=1), 2)
    assert "(без 4 писем, которые не уйдут, пока их не поправят)" in eta.words


def test_a_queue_of_stuck_letters_only_has_no_date() -> None:
    _, eta = plan(NOW, 3, Clock(), _boxes(_box(1)), Supply(queued=3, stuck=3), {})

    assert eta.day is None
    assert eta.words == "в очереди только письма, которые не уйдут, пока их не поправят (3)"


def test_the_first_open_day_is_today_unless_the_window_has_closed() -> None:
    """В него прогноз кладёт первое письмо ящика, который ещё не писал."""
    clock = Clock(WEEKDAYS_9_17, BERLIN)
    friday_evening = datetime(2026, 10, 23, 16, 0, tzinfo=UTC)

    assert first_open(clock, NOW) == NOW.date()
    assert first_open(clock, friday_evening) == date(2026, 10, 26)
    assert first_open(Clock(), friday_evening) == date(2026, 10, 23)


def test_a_projected_first_letter_grows_a_new_mailbox_in_the_plan() -> None:
    """Ящик, ещё не писавший, с предположенным первым письмом сегодня: 5, 5, 10, 10 — а не пять."""
    new = _box(1, wrote=None)
    today = capacity.rooms_on([new], {}, Stage.SALES, None, NOW.date())
    boxes = Mailboxes(
        left=today,
        capacity=today.total(),
        later=lambda day: capacity.rooms_on([new], {}, Stage.SALES, None, day, first=NOW.date()),
    )

    days, _ = plan(NOW, 4, Clock(), boxes, Supply(queued=500), {})

    assert [day.capacity for day in days] == [5, 5, 10, 10]


# --- срок очереди ---------------------------------------------------------------------------


def test_a_queue_that_fits_today_goes_today_with_hours_by_pace() -> None:
    """Девять писем, три ящика, пауза 15 минут: ящики пишут вровень — по три письма, последнее
    через три паузы от сейчас (окно открыто)."""
    clock = Clock(WEEKDAYS_9_17, BERLIN, PACE)
    boxes = _boxes(_box(1, wrote=40), _box(2, wrote=40), _box(3, wrote=40))
    _, eta = plan(NOW, 7, clock, boxes, Supply(queued=9), {})

    assert (eta.day, eta.sending_days, eta.hours) == (NOW.date(), 1, 0.8)
    assert eta.words == (
        "последнее письмо очереди уйдёт ср 21.10, дней отправки: 1, ≈ через 0.8 ч: "
        "автоотправка, письмо с ящика раз в 15 мин (±50%), окно — по часам Europe/Berlin"
    )


def test_a_queue_over_several_days_skips_the_weekend_and_counts_from_the_window() -> None:
    """60 писем по 20 в день: ср, чт, пт — и последнее письмо пятницы уходит через 20 пауз от
    открытия окна (07:00 UTC): в 12:00 UTC пятницы — 50 часов от среды, 10:00."""
    clock = Clock(WEEKDAYS_9_17, BERLIN, PACE)
    _, eta = plan(NOW, 7, clock, _boxes(_box(1, wrote=40)), Supply(queued=60), {})

    assert (eta.day, eta.sending_days, eta.hours) == (date(2026, 10, 23), 3, 50.0)


def test_pace_caps_what_a_mailbox_sends_before_the_window_closes() -> None:
    """Час до закрытия окна, пауза 15 минут: ящик успеет четыре письма, хоть лимит и двадцать."""
    late = NOW.replace(hour=14)
    clock = Clock(WEEKDAYS_9_17, BERLIN, PACE)
    days, _ = plan(late, 2, clock, _boxes(_box(1, wrote=40)), Supply(queued=500), {})

    assert (days[0].capacity, days[0].planned) == (20, 4)
    assert days[0].why_not == (
        "темп автоотправки: письмо с ящика раз в 15 мин — до 15:00 UTC успеет не больше 4 с ящика"
    )
    assert days[1].planned == 20  # 8 часов окна — 32 паузы, лимит двадцать


def test_by_hand_there_are_no_hours_and_the_words_say_when_the_date_holds() -> None:
    _, eta = plan(NOW, 7, Clock(WEEKDAYS_9_17, BERLIN), _boxes(_box(1, wrote=40)), Supply(60), {})

    assert (eta.day, eta.hours) == (date(2026, 10, 23), None)
    assert "вручную: пачка выбирает дневной лимит за секунды (до 200 писем за нажатие)" in eta.words
    assert "если нажимать «Отправить очередь» каждый день отправки в часы окна" in eta.words


def test_an_unknown_zone_keeps_the_days_and_drops_the_hours() -> None:
    clock = Clock(WEEKDAYS_9_17, None, PACE)
    days, eta = plan(NOW, 7, clock, _boxes(_box(1, wrote=40)), Supply(60), {})

    assert [day.sending for day in days] == [True, True, True, False, False, True, True]
    assert (eta.day, eta.hours) == (date(2026, 10, 23), None)
    assert eta.words.endswith("часов не посчитать: пояс получателей очереди неизвестен")


def test_without_mailboxes_the_queue_has_no_date_and_says_why() -> None:
    boxes = Mailboxes(
        left=Rooms({}, {}),
        capacity=0,
        later=lambda _day: Rooms({}, {}),
        idle="ящиков, которые пишут, нет: все выключены или на паузе",
    )
    days, eta = plan(NOW, 3, Clock(), boxes, Supply(queued=12), {})

    assert [day.planned for day in days] == [0, 0, 0]
    assert days[0].why_not == "ящиков, которые пишут, нет: все выключены или на паузе"
    assert (eta.day, eta.sending_days, eta.hours) == (None, None, None)
    assert eta.words == "очередь не уйдёт: ящиков, которые пишут, нет: все выключены или на паузе"


def test_an_empty_queue_has_no_date() -> None:
    _, eta = plan(NOW, 3, Clock(), _boxes(_box(1)), Supply(queued=0, extra=40), {})

    assert eta.day is None
    assert eta.words == "очередь пуста — отправлять нечего"


def test_a_blocked_stage_sends_nothing_and_says_why() -> None:
    words = "Первые письма этапа не уходят: продажи к почте ещё не подключены"
    days, eta = plan(NOW, 2, Clock(), _boxes(_box(1), blocked=words), Supply(queued=30), {})

    assert [(day.capacity, day.planned, day.why_not) for day in days] == [
        (5, 0, words),
        (5, 0, words),
    ]
    assert eta.words == f"очередь не уходит: {words}"


def test_a_queue_longer_than_a_year_says_so() -> None:
    _, eta = plan(NOW, 1, Clock(), _boxes(_box(1, cap=1, wrote=40)), Supply(queued=1000), {})

    assert eta.day is None
    assert eta.words == "очередь не уйдёт и за 366 дней при нынешней мощности ящиков"


def test_a_new_mailbox_stays_on_the_first_step_until_its_first_letter() -> None:
    """Ступень — от первого письма: ящик, ещё не писавший, на первой ступени на все дни вперёд."""
    days, _ = plan(NOW, 4, Clock(), _boxes(_box(1, wrote=None)), Supply(queued=500), {})

    assert [day.capacity for day in days] == [5, 5, 5, 5]
