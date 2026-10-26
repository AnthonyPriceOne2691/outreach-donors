"""Одна мощность ящиков продаж — у «Плана» недельного отчёта, запаса автосборки и прогноза рассылки.

«План» недели — прогноз на те же дни (`weekly_counts.runway` и `letters/forecast.py` — одной функцией
`outreach/capacity.capacity`), а запас автосборки — мощность прогноза на ближайший день отправки
(`autobuild.needed`). Ящики — на разных ступенях разгона, новый ящик без первого письма (его разгон
начинается в первый день, когда письма ещё уходят, — от минуты сборки, а не от первого дня недели),
закрытый домен, лимит домена и лимит направления — каждое правило видно своим числом. Совпадение с
прогнозом — на будущих днях окна: за сегодня у прогноза своя мощность — глазами выбора ящика
(снижение лимита мягкими отказами, домен — на минуту вопроса). Неделя — не с понедельника: отчёт
собирается в среду днём и в среду после окна получателя (пояс очереди — берлинский), автосборка — ещё
и в субботу. Ступени разгона — выдуманные (7, 13, 19 по два дня); ящики, домены и адреса — выдуманные.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.core.domain import MessageStatus, SenderStatus, Stage
from backend.features.core.models.outreach import SenderModel, SendingDomainModel
from backend.features.letters import forecast
from backend.features.outreach import senders
from backend.features.sales import autobuild, hypotheses, weekly
from backend.features.sales.weekly import Week
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_mailing import Book

MONDAY = date(2027, 1, 11)
#: Среда, 11:00 по Берлину (зима, UTC+1): окно получателя 09–17 открыто.
WEDNESDAY = datetime(2027, 1, 13, 10, 0, tzinfo=UTC)
#: Среда, 17:30 по Берлину: окно на сегодня закрылось — ближайший день отправки четверг.
AFTER_WINDOW = datetime(2027, 1, 13, 16, 30, tzinfo=UTC)
SATURDAY = datetime(2027, 1, 16, 10, 0, tzinfo=UTC)


def _day(number: int) -> date:
    return MONDAY + timedelta(days=number)


@pytest.fixture(autouse=True)
def _rules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ступени разгона, лимит направления и окно получателя пн–пт 09–17 — выдуманные."""
    monkeypatch.setattr(senders, "WARMUP_STEPS", (7, 13, 19))
    monkeypatch.setattr(senders, "WARMUP_STEP_DAYS", 2)
    monkeypatch.setattr(outreach_cfg, "DIRECTION_LIMITS", {"sales": 60})
    monkeypatch.setattr(outreach_cfg, "UNREAD_LIMITS", frozenset())
    monkeypatch.setattr(sales_cfg, "SEND_DAYS", frozenset(range(5)))
    monkeypatch.setattr(sales_cfg, "SEND_OPENS", time(9))
    monkeypatch.setattr(sales_cfg, "SEND_CLOSES", time(17))


def _box(
    email: str,
    *,
    cap: int = 20,
    started: datetime | None = None,
    wrote: datetime | None = None,
    enabled: bool = True,
    stage: Stage = Stage.SALES,
) -> SenderModel:
    """Ящик: `started` — начало разгона (пусто — разгон кончен), `wrote` — его первое письмо."""
    return SenderModel(
        domain=email.split("@", 1)[1],
        email=email,
        stage=stage,
        daily_cap=cap,
        status=SenderStatus.FREE,
        enabled=enabled,
        warmup_started_at=started,
        warmup_first_sent_at=wrote,
    )


@pytest.fixture
async def mailboxes(session: AsyncSession) -> None:
    """Ящики продаж — каждое правило мощности своим числом — и письмо в очереди лиду из Берлина.

    - `warm` — разгон кончен: 23 каждый день;
    - `step` — первое письмо в понедельник: 7, 7, 13, 13, 19;
    - `new` — разгон начат, письма ещё не было: 7 с первого дня отправки, через два дня — 13;
    - `paused` — домен на паузе: ноль;
    - `tight` — два ящика по 20 на домене с лимитом 9;
    - лимит направления — 60; выключенный ящик и ящик доноров не пишут.
    """
    monday = datetime.combine(MONDAY, time(8), tzinfo=UTC)
    session.add_all(
        [
            _box("grown@warm.example.test", cap=23),
            _box("young@step.example.test", cap=30, started=monday, wrote=monday),
            _box("fresh@new.example.test", cap=30, started=monday + timedelta(days=1)),
            _box("stopped@paused.example.test"),
            _box("one@tight.example.test"),
            _box("two@tight.example.test"),
            _box("off@warm.example.test", enabled=False),
            _box("donors@donors.example.test", stage=Stage.DONORS),
            SendingDomainModel(
                domain="paused.example.test",
                stage=Stage.SALES,
                daily_limit=100,
                paused_at=monday,
                pause_reason="выдуманная жалоба",
            ),
            SendingDomainModel(domain="tight.example.test", stage=Stage.SALES, daily_limit=9),
        ]
    )
    await session.flush()
    owner = (await hypotheses.add(session, "Выдуманная гипотеза мощности", None)).id
    book = Book(session, owner)
    await book.first(await book.lead(timezone="Europe/Berlin"), MessageStatus.QUEUED)


async def _plan(session: AsyncSession, now: datetime) -> dict[date, int]:
    """«План» недельного отчёта, собранного в `now` за прошлую неделю: дни отправки этой недели."""
    report = await weekly.build(session, Week(MONDAY - timedelta(days=7)), now=now)
    return {day.day: day.letters for day in report.plan.runway.days}


async def _forecast(session: AsyncSession, now: datetime) -> dict[date, int]:
    """Мощность прогноза рассылки по дням отправки — неделя от `now`."""
    found = await forecast.forecast(session, Stage.SALES, days=7, back=0, now=now)
    return {day.day: day.capacity for day in found.days if day.sending}


def _ahead(planned: dict[date, int], projected: dict[date, int], now: datetime) -> list[date]:
    """Будущие дни окна, которые есть и у «Плана», и у прогноза."""
    return sorted(day for day in set(planned) & set(projected) if day > now.date())


@pytest.mark.usefixtures("mailboxes")
async def test_the_week_plan_is_the_forecast_for_the_same_days_on_wednesday(
    session: AsyncSession,
) -> None:
    """Новый ящик начинает разгон в среду — у «Плана» так же, как у прогноза, а не с понедельника
    недели плана: иначе в четверг «План» сказал бы 58, а прогноз 52."""
    planned = await _plan(session, WEDNESDAY)
    projected = await _forecast(session, WEDNESDAY)

    assert planned == {_day(0): 46, _day(1): 46, _day(2): 52, _day(3): 52, _day(4): 60}
    same = _ahead(planned, projected, WEDNESDAY)
    assert same == [_day(3), _day(4)]
    assert [planned[day] for day in same] == [projected[day] for day in same]


@pytest.mark.usefixtures("mailboxes")
async def test_after_the_window_the_new_mailbox_starts_tomorrow_in_both(
    session: AsyncSession,
) -> None:
    """Окно получателя на сегодня закрылось: новый ящик напишет первое письмо в четверг — и у
    «Плана», и у прогноза; в пятницу он ещё на первой ступени."""
    planned = await _plan(session, AFTER_WINDOW)
    projected = await _forecast(session, AFTER_WINDOW)

    assert planned == {_day(0): 46, _day(1): 46, _day(2): 52, _day(3): 52, _day(4): 58}
    same = _ahead(planned, projected, AFTER_WINDOW)
    assert same == [_day(3), _day(4)]
    assert [planned[day] for day in same] == [projected[day] for day in same]


@pytest.mark.parametrize(
    ("now", "day", "letters"),
    [
        (WEDNESDAY, _day(2), 52),
        (AFTER_WINDOW, _day(3), 52),
        (SATURDAY, _day(7), 58),
    ],
    ids=["среда-днём", "среда-после-окна", "суббота"],
)
@pytest.mark.usefixtures("mailboxes")
async def test_the_autobuild_need_is_the_forecast_capacity_of_the_nearest_sending_day(
    session: AsyncSession, now: datetime, day: date, letters: int
) -> None:
    """Запас автосборки — мощность прогноза на ближайший день отправки, с закрытым доменом и
    лимитами: прежние «капы с разгоном» дали бы в среду 103 письма (23 + 13 + 7 + 20 + 20 + 20).
    В среду днём это сегодня: мягких отказов нет, и мощность прогноза на сегодня та же."""
    found = await autobuild.needed(session, now)
    projected = await _forecast(session, now)

    assert found == (day, letters)
    assert projected[day] == letters
