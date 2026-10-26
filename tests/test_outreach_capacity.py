"""Мощность ящиков этапа по дням — правило экрана «Домены рассылки» (`outreach/capacity.py`).

Одно правило у прогноза рассылки и — следующим кругом — у «Плана» недельного отчёта продаж:
потолок разгона включённого ящика этапа в полдень дня, по доменам с их лимитом (закрытый домен —
ноль), затем лимит направления. Сегодня — глазами выбора ящика первого письма: тот же фильтр
лимитов, сниженный мягкими отказами лимит ящика и ушедшие сегодня первые письма. Ящики, домены
и адреса — выдуманные.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import SenderStatus, Stage
from backend.features.core.models.outreach import SenderModel, SendingDomainModel
from backend.features.core.window import SendWindow
from backend.features.outreach import capacity, limits
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 10, 21, 10, 0, tzinfo=UTC)
TODAY = NOW.date()


def _box(
    number: int,
    *,
    domain: str = "mail-a.example.test",
    stage: Stage = Stage.SALES,
    cap: int = 20,
    wrote: int | None = 40,
    enabled: bool = True,
) -> SenderModel:
    """Ящик: `wrote` — сколько дней назад ушло первое письмо разгона; `None` — ещё не писал."""
    return SenderModel(
        id=number,
        domain=domain,
        email=f"box{number}@{domain}",
        stage=stage,
        daily_cap=cap,
        status=SenderStatus.FREE,
        enabled=enabled,
        warmup_started_at=NOW - timedelta(days=100),
        warmup_first_sent_at=None if wrote is None else NOW - timedelta(days=wrote),
    )


def _domain(
    name: str, *, limit: int = 100, stage: Stage = Stage.SALES, **extra: object
) -> SendingDomainModel:
    return SendingDomainModel(domain=name, stage=stage, daily_limit=limit, **extra)


# --- правило дня ----------------------------------------------------------------------------


def test_mailboxes_add_up_by_domain_under_its_limit() -> None:
    """Два ящика по двадцать на домене с лимитом 30 — тридцать; ящик на домене без строки — сколько
    может сам."""
    boxes = [_box(1), _box(2), _box(3, domain="mail-b.example.test")]
    domains = {"mail-a.example.test": _domain("mail-a.example.test", limit=30)}

    assert capacity.day_capacity(boxes, domains, Stage.SALES, None, TODAY) == 50


def test_closed_domains_give_nothing_until_they_open() -> None:
    """Пауза держит домен, пока её не снимут; выдержка — до своего срока: в полдень дня после неё
    домен уже пишет."""
    paused = _domain("mail-a.example.test", paused_at=NOW, pause_reason="жалоба")
    young = _domain("mail-b.example.test", young_until=NOW + timedelta(days=1))
    boxes = [_box(1), _box(2, domain="mail-b.example.test")]
    domains = {row.domain: row for row in (paused, young)}

    assert capacity.day_capacity(boxes, domains, Stage.SALES, None, TODAY) == 0
    assert capacity.day_capacity(boxes, domains, Stage.SALES, None, TODAY + timedelta(days=2)) == 20


def test_only_enabled_mailboxes_of_the_stage_count_and_the_direction_caps() -> None:
    boxes = [
        _box(1),
        _box(2, enabled=False),
        _box(3, domain="mail-d.example.test", stage=Stage.DONORS),
        _box(4, domain="mail-b.example.test"),
    ]

    assert capacity.day_capacity(boxes, {}, Stage.SALES, None, TODAY) == 40
    assert capacity.day_capacity(boxes, {}, Stage.SALES, 25, TODAY) == 25


def test_rooms_tell_how_many_letters_the_busiest_mailbox_gives() -> None:
    """Ящики пишут вровень: пять писем двум ящикам с остатком 10 и 2 — самый занятый отдаст три."""
    rooms = capacity.Rooms({"mail-a.example.test": (10, 2)}, {"mail-a.example.test": None})

    assert rooms.busiest(5) == 3
    assert rooms.total(per_box=3) == 5


def test_the_next_warmup_step_is_named_with_its_day() -> None:
    """Первое письмо вчера: ступень 5 — сегодня и завтра, 10 — послезавтра. Разгон кончился или
    ящик ещё не писал — следующей ступени нет."""
    assert capacity.next_step(_box(1, wrote=1), TODAY) == (TODAY + timedelta(days=1), 10)
    assert capacity.next_step(_box(2, wrote=40), TODAY) is None
    assert capacity.next_step(_box(3, wrote=None), TODAY) is None


# --- ящик без первого письма: правило экрана и предположение прогноза ----------------------


def test_without_projection_a_new_mailbox_stays_on_the_first_step() -> None:
    """Правило экрана и «Плана» отчёта: ступень — от первого письма, его нет — первая ступень."""
    days = [TODAY + timedelta(days=n) for n in range(6)]

    found = [capacity.day_capacity([_box(1, wrote=None)], {}, Stage.SALES, None, d) for d in days]

    assert found == [5, 5, 5, 5, 5, 5]


def test_with_projection_a_new_mailbox_warms_up_from_its_first_sending_day() -> None:
    """Прогноз: первое письмо — в первый день отправки (здесь пятница), разгон — оттуда; ящику,
    который уже писал, предположение ничего не меняет."""
    friday = TODAY + timedelta(days=2)
    days = [TODAY + timedelta(days=n) for n in range(7)]
    new, old = _box(1, wrote=None), _box(2, domain="mail-b.example.test", wrote=1)

    assert [capacity.day_capacity([new], {}, Stage.SALES, None, d, first=friday) for d in days] == [
        5,
        5,
        5,
        5,
        10,
        10,
        15,
    ]
    assert [capacity.day_capacity([old], {}, Stage.SALES, None, d, first=friday) for d in days] == [
        capacity.day_capacity([old], {}, Stage.SALES, None, d) for d in days
    ]
    assert capacity.next_step(new, TODAY, first=friday) == (TODAY + timedelta(days=4), 10)


def test_the_first_sending_day_follows_the_window_days() -> None:
    """Суббота окна пн–пт — первый день отправки в понедельник; без окна — сам день."""
    saturday = date(2026, 10, 24)
    weekdays = SendWindow(days=frozenset(range(5)), start=time(9), end=time(17))

    assert capacity.first_sending(saturday, weekdays) == date(2026, 10, 26)
    assert capacity.first_sending(saturday, None) == saturday
    assert capacity.first_sending(TODAY, weekdays) == TODAY


# --- сегодня глазами выбора ящика -----------------------------------------------------------


def test_today_halves_a_cut_mailbox_and_subtracts_what_went() -> None:
    """Мягкие отказы снизили лимит ящика вдвое (`health.cuts`): сегодня его потолок — десять; с
    него ушло три, с соседа — пять."""
    boxes = [_box(1), _box(2)]
    found = capacity.today(
        boxes, stage=Stage.SALES, sent={1: 3, 2: 5}, domains={}, cuts={1: 10}, now=NOW
    )

    assert [(box.allowance, box.sent, box.cut) for box in found.boxes] == [
        (10, 3, True),
        (20, 5, False),
    ]
    assert (found.capacity, found.left.total(), found.sent) == (30, 22, 8)
    assert found.cut == (
        "мягкие отказы: лимит на сутки снижен вдвое у box1@mail-a.example.test — сегодняшняя "
        "мощность считает сниженный, как выбор ящика"
    )


def test_a_cut_mailbox_that_used_its_halved_limit_is_refused_in_words() -> None:
    found = capacity.today(
        [_box(1)], stage=Stage.SALES, sent={1: 10}, domains={}, cuts={1: 10}, now=NOW
    )

    assert found.left.total() == 0
    assert found.boxes[0].refused == f"{limits.BOX_CUT}: box1@mail-a.example.test — 10 из 10"


def test_today_counts_the_domain_letters_of_every_mailbox_on_it() -> None:
    """Домен считает первые письма всех своих ящиков, как фильтр: ящик доноров на том же домене
    съел восемь из лимита десять — продажам осталось два."""
    boxes = [_box(1), _box(2, stage=Stage.DONORS)]
    domains = {"mail-a.example.test": _domain("mail-a.example.test", limit=10)}
    found = capacity.today(boxes, stage=Stage.SALES, sent={2: 8}, domains=domains, cuts={}, now=NOW)

    assert (found.capacity, found.left.total()) == (10, 2)


def test_today_names_why_a_mailbox_takes_no_first_letters() -> None:
    domains = {
        "mail-a.example.test": _domain(
            "mail-a.example.test", paused_at=NOW, pause_reason="блок-лист"
        )
    }
    found = capacity.today([_box(1)], stage=Stage.SALES, sent={}, domains=domains, cuts={}, now=NOW)

    assert found.left.total() == 0
    assert found.boxes[0].refused == f"{limits.DOMAIN_PAUSED}: mail-a.example.test — блок-лист"


def test_unread_direction_limit_is_zero_letters_and_said_in_words(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Лимит направления с опечаткой — ноль первых писем (`config/_base.spared`), и это сказано
    словами, что поправить, а не «0 писем»."""
    monkeypatch.setitem(outreach_cfg.DIRECTION_LIMITS, "sales", 0)
    monkeypatch.setattr(outreach_cfg, "UNREAD_LIMITS", frozenset({"OUTREACH_SALES_DAILY_LIMIT"}))

    found = capacity.today([_box(1)], stage=Stage.SALES, sent={}, domains={}, cuts={}, now=NOW)

    assert (found.capacity, found.left.total()) == (0, 0)
    assert capacity.unread(Stage.SALES) == limits.unread_words("OUTREACH_SALES_DAILY_LIMIT")
    assert capacity.unread(Stage.DONORS) is None


# --- по дням из базы: подпись «Плана» недельного отчёта -------------------------------------


async def test_capacity_reads_the_stage_mailboxes_and_domains(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Включённые ящики этапа и строки доменов — из базы, на каждый вопрос: новый ящик виден сразу."""
    monkeypatch.setitem(outreach_cfg.DIRECTION_LIMITS, "sales", 35)
    first = datetime.now(UTC) - timedelta(days=40)
    for number, (domain, stage, enabled) in enumerate(
        [
            ("mail-a.example.test", Stage.SALES, True),
            ("mail-a.example.test", Stage.SALES, False),
            ("mail-b.example.test", Stage.SALES, True),
            ("mail-d.example.test", Stage.DONORS, True),
        ],
        start=1,
    ):
        session.add(
            SenderModel(
                domain=domain,
                email=f"made-up-{number}@{domain}",
                stage=stage,
                daily_cap=20,
                status=SenderStatus.FREE,
                enabled=enabled,
                warmup_started_at=first - timedelta(days=1),
                warmup_first_sent_at=first,
            )
        )
    session.add(_domain("mail-b.example.test", limit=12))
    await session.flush()
    day = datetime.now(UTC).date() + timedelta(days=1)

    found = await capacity.capacity(session, Stage.SALES, [day, day + timedelta(days=1)])

    assert found.boxes == 2
    assert [(d.day, d.letters) for d in found.days] == [
        (day, 32),
        (day + timedelta(days=1), 32),
    ]
    assert found.unread is None


async def test_capacity_projects_the_first_letter_only_when_asked(session: AsyncSession) -> None:
    """Без просьбы — как экран: ящик без первого письма на первой ступени; с просьбой — разгон от
    первого дня отправки. У доноров окна нет: первый день отправки — первый из дней."""
    session.add(
        SenderModel(
            domain="mail-n.example.test",
            email="made-up-new@mail-n.example.test",
            stage=Stage.DONORS,
            daily_cap=20,
            status=SenderStatus.FREE,
            enabled=True,
            warmup_started_at=datetime.now(UTC) - timedelta(days=3),
        )
    )
    await session.flush()
    start = datetime.now(UTC).date() + timedelta(days=1)
    days = [start + timedelta(days=n) for n in range(5)]

    as_screen = await capacity.capacity(session, Stage.DONORS, days)
    projected = await capacity.capacity(session, Stage.DONORS, days, project_first_letter=True)

    assert [d.letters for d in as_screen.days] == [5, 5, 5, 5, 5]
    assert [d.letters for d in projected.days] == [5, 5, 10, 10, 15]


def test_a_day_is_asked_at_its_noon() -> None:
    assert capacity.noon(date(2026, 10, 24)) == datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
