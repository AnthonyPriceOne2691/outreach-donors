"""Недельный отчёт продаж — срез 6.3: восемь разделов функциями экранов (A1–A2 Spec 6.3).

A1 — числа отчёта равны функциям экранов за ту же неделю: воронки (`funnel.board`), гипотез
(`browse.hypotheses`), очереди (`queue.state`) — и сверены явно, числом; A2 — неделя без
данных: каждый раздел словами, нулём, а не пропуском. Граница недели — по Москве. A3 (Telegram
недоступен) и «один раз» — в `test_sales_weekly_jobs.py`, экран и CSV через API — в
`test_sales_weekly_api.py`. Строки базы — `test_sales_weekly_rows.py`.
"""

from __future__ import annotations

import csv
import io
from datetime import UTC, date, datetime

import pytest
from backend.config import outreach as outreach_cfg
from backend.config import sales as sales_cfg
from backend.features.core.domain import MessageStatus, ReplyKind, Stage
from backend.features.outreach import senders
from backend.features.sales import browse, connection, funnel, queue, weekly, weekly_text
from backend.features.sales.funnel import Funnel
from backend.features.sales.models import LeadStatus
from backend.features.sales.telegram import MAX_CHARS
from backend.features.sales.weekly import Week, WeeklyReport
from backend.features.sales.weekly_counts import AUTO, UNPARSED, Dialogs, kind_of
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_weekly_rows import BUILT, SETTLED, WEEK, WeekRows, msk

TITLES = [
    "Контакты",
    "Отправки",
    "Доставляемость",
    "Ответы",
    "Диалоги",
    "Лиды поимённо",
    "Проблемы",
    "План",
]
#: Ссылка, которую получил телемаркетолог, — выдуманная сделка.
DEAL = "https://crm.example.test/leads/detail/4417"


@pytest.fixture
def rows(session: AsyncSession) -> WeekRows:
    return WeekRows(session)


async def week_world(rows: WeekRows) -> tuple[int, int, int]:
    """Неделя с данными: две гипотезы на всех шагах, третья пустая, и всё, что рядом с неделей."""
    first = await rows.hypothesis("свои сайты")
    before = msk(26, month=2)
    await rows.loaded(first, "new@a1.example.test", at=msk(2), status=LeadStatus.NEW)
    await rows.loaded(first, "rej@a2.example.test", at=msk(3), status=LeadStatus.REJECTED)
    await rows.loaded(first, "rdy@a3.example.test", at=msk(4), status=LeadStatus.READY)
    await rows.loaded(first, "old@a4.example.test", at=before, status=LeadStatus.READY)
    warm = await rows.lead(first, "jane@warm.example.test", at=before, company="Warm Ltd")
    await rows.letter(warm, step=0, status=MessageStatus.DELIVERED, at=msk(2, 10))
    await rows.letter(warm, step=1, status=MessageStatus.DELIVERED, at=msk(5, 10))
    talks = {"stage": "sales", "kind": "wants_to_talk", "waits": False}
    await rows.reply(warm, ReplyKind.HUMAN, at=msk(6, 15), snapshot=talks)
    await rows.hand_off(warm, at=msk(6, 16), link=DEAL)
    dead = await rows.lead(first, "nobody@dead.example.test", at=msk(1, 8))
    await rows.letter(dead, step=0, status=MessageStatus.BOUNCED, at=msk(3, 11))
    late = await rows.lead(first, "late@late.example.test", at=before)
    await rows.letter(late, step=0, status=MessageStatus.DELIVERED, at=msk(24, 10, month=2))
    await rows.letter(late, step=1, status=MessageStatus.BOUNCED, at=msk(2, 9))
    quiet = await rows.lead(first, "quiet@quiet.example.test", at=before)
    await rows.letter(quiet, step=0, status=MessageStatus.DELIVERED, at=msk(22, month=2))
    await rows.reply(quiet, ReplyKind.HUMAN, at=msk(23, month=2), snapshot=SETTLED)
    talk = await rows.lead(first, "olga@talk.example.test", at=before)
    await rows.letter(talk, step=0, status=MessageStatus.DELIVERED, at=msk(1, 9, 30))
    await rows.reply(talk, ReplyKind.HUMAN, at=msk(4, 14))
    await rows.reply(talk, ReplyKind.BOUNCE, at=msk(4, 15))
    away = await rows.lead(first, "away@away.example.test", at=before)
    await rows.letter(away, step=0, status=MessageStatus.SENT, at=msk(5, 9))
    await rows.reply(away, ReplyKind.AUTO_REPLY, at=msk(5, 10))
    stop = await rows.lead(first, "stop@stop.example.test", at=before)
    await rows.letter(stop, step=0, status=MessageStatus.DELIVERED, at=msk(1, 23, 50))
    await rows.reply(stop, ReplyKind.UNSUBSCRIBE, at=msk(2, 8))
    second = await rows.hypothesis("англоязычные")
    inside = await rows.lead(second, "late@sunday.example.test", at=before)
    await rows.letter(inside, step=0, status=MessageStatus.DELIVERED, at=msk(7, 23, 30))
    outside = await rows.lead(second, "early@monday.example.test", at=before)
    await rows.letter(outside, step=0, status=MessageStatus.DELIVERED, at=msk(8, 0, 30))
    start = await rows.lead(second, "first@minute.example.test", at=before)
    await rows.letter(start, step=0, status=MessageStatus.DELIVERED, at=msk(1, 0, 10))
    failed = {"stage": "sales", "kind": "parse_failed", "waits": True}
    await rows.reply(start, ReplyKind.HUMAN, at=msk(3), snapshot=failed)
    fresh = await rows.lead(second, "fresh@queue.example.test", at=before)
    await rows.letter(fresh, step=0, status=MessageStatus.QUEUED, at=msk(6))
    empty = await rows.hypothesis("пустая")
    writing = await rows.box("box1@sales-one.example.test")
    paused = await rows.box("box2@sales-two.example.test", enabled=False)
    donors = await rows.box("box@donors.example.test", stage=Stage.DONORS)
    await rows.health(writing, "complaint", at=msk(3), detail="жалоба на спам")
    await rows.health(writing, "complaint", at=msk(27, month=2), detail="жалоба на спам")
    await rows.health(paused, "paused", at=msk(4), detail="жалоб 1 в окне 50 писем: пауза")
    await rows.health(donors, "complaint", at=msk(3), detail="жалоба на спам")
    return first, second, empty


async def _report(session: AsyncSession, week: Week = WEEK) -> WeeklyReport:
    return await weekly.build(session, week, now=BUILT)


# --- A1: все разделы заполнены, числа — функциями экранов -----------------------------------


async def test_a1_numbers_are_the_screens_numbers_for_the_same_week(
    session: AsyncSession, rows: WeekRows
) -> None:
    # A1: числа равны функциям экранов за тот же период
    first, second, empty = await week_world(rows)

    report = await _report(session)

    board = await funnel.board(session, WEEK.period)
    assert [(row.hypothesis_id, row.numbers.funnel) for row in report.rows] == [
        (row.hypothesis_id, row.funnel) for row in board.rows
    ]
    assert report.total.funnel == board.total
    states = {
        row.hypothesis.id: row.states for row in await browse.hypotheses(session, WEEK.period)
    }
    letters = await funnel.letters(session, WEEK.period)
    for row in report.rows:
        line = await queue.state(session, row.hypothesis_id)
        seen = states[row.hypothesis_id]
        assert (row.numbers.loaded, row.numbers.ready, row.numbers.rejected) == (
            sum(seen.values()),
            seen["ready"],
            seen["rejected"],
        )
        assert list(row.numbers.steps) == letters.get(row.hypothesis_id, [0, 0, 0])
        assert (row.numbers.unwritten, row.numbers.queued) == (line.unwritten, line.queued)
    assert [row.hypothesis_id for row in report.rows] == [first, second, empty]


async def test_a1_numbers_of_the_week_in_words(session: AsyncSession, rows: WeekRows) -> None:
    """Те же числа — явно: сверка с функцией экрана не прячет ошибку общего правила."""
    # A1: числа недели явно
    first, second, empty = await week_world(rows)

    report = await _report(session)

    by = {row.hypothesis_id: row.numbers for row in report.rows}
    one, two = by[first], by[second]
    assert (one.loaded, one.ready, one.rejected, one.cleaning) == (4, 2, 1, 1)
    assert one.funnel == Funnel(queued=0, sent=5, delivered=3, bounced=1, answered=3, handed_off=1)
    assert one.steps == (5, 2, 0)
    assert {kind: n for kind, n in one.replies.items() if n} == {
        "wants_to_talk": 1,
        "unsubscribe": 1,
        UNPARSED: 1,
        AUTO: 1,
    }
    assert (one.answers, one.handed, one.dialogs) == (3, 1, Dialogs(active=1, silent=1, waiting=1))
    assert (one.unwritten, one.queued) == (2, 0)
    assert two.funnel == Funnel(queued=1, sent=2, delivered=2, bounced=0, answered=1, handed_off=0)
    assert (two.steps, two.answers, two.dialogs) == ((2, 0, 0), 1, Dialogs(1, 0, 1))
    assert (two.unwritten, two.queued) == (0, 1)
    assert by[empty] == weekly.Numbers()
    assert report.total == one + two
    assert (report.mail.complaints, report.mail.boxes, report.mail.idle) == (1, 2, 1)
    [pause] = report.mail.pauses
    assert pause.box == "box2@sales-two.example.test"
    [lead] = report.leads
    assert (lead.company, lead.site, lead.email, lead.link) == (
        "Warm Ltd",
        "warm.example.test",
        "jane@warm.example.test",
        DEAL,
    )


async def test_a1_every_section_is_filled(session: AsyncSession, rows: WeekRows) -> None:
    # A1: все 8 разделов заполнены
    await week_world(rows)

    said = weekly_text.sections(await _report(session))

    assert [section.title for section in said] == TITLES
    assert all(section.lines and all(section.lines) for section in said)
    text = {section.key: "\n".join(section.lines) for section in said}
    assert "загружено за неделю — 4: готово к письмам — 2" in text["contacts"]
    assert "первое письмо получили лидов — 7" in text["sends"]
    assert "1-е — 7, 2-е — 2, 3-е — 0" in text["sends"]
    assert "доставлено — 5, отказ — 1" in text["delivery"]
    assert "ответов за неделю — 4: хочет говорить — 1" in text["replies"]
    assert "автоответов — 1" in text["replies"]
    assert "активных — 2 (было движение с начала недели), молчащих — 1" in text["dialogs"]
    assert f"Warm Ltd (warm.example.test) · jane@warm.example.test — {DEAL}" in text["leads"]
    assert (
        "Ящик box2@sales-two.example.test встал на паузу 04.03 12:00 по Москве" in text["problems"]
    )
    assert "жалоб на спам — 1, пауз ящиков — 1; сейчас не пишут ящиков — 1 из 2" in text["delivery"]
    assert "первых писем ждут отправки — 1, готовых лидов без письма — 2" in text["plan"]


# --- A2: неделя без данных — нули словами -----------------------------------------------------


async def test_a2_empty_week_says_every_section_with_zeros(
    session: AsyncSession, rows: WeekRows, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A2: неделя без ответов — «ответов: 0» словами, а не пропуск
    await rows.hypothesis("пустая")

    async def connected(_session: AsyncSession) -> list[str]:
        return []

    monkeypatch.setattr(connection, "missing", connected)

    report = await _report(session)
    said = weekly_text.sections(report)
    message = weekly_text.message(report)

    assert [section.title for section in said] == TITLES
    lines = {section.key: section.lines[0] for section in said}
    assert lines == {
        "contacts": "загружено за неделю — 0: готово к письмам — 0, отсеяно очисткой — 0, "
        "ждут очистки — 0",
        "sends": "первое письмо получили лидов — 0; писем по шагам цепочки: 1-е — 0, 2-е — 0, "
        "3-е — 0",
        "delivery": "лиды первого письма недели: доставлено — 0, отказ — 0",
        "replies": "ответов за неделю — 0: хочет говорить — 0, вопрос — 0, интерес — 0, "
        "назвал другого — 0, не сейчас — 0, не интересно — 0, просит не писать — 0, "
        "вид не разобран — 0",
        "dialogs": "в диалоге лидов — 0: активных — 0 (было движение с начала недели), "
        "молчащих — 0; ждут человека — 0",
        "leads": "передано телемаркетологу за неделю — 0",
        "problems": "проблем — 0: тревог и сбоев нет",
        "plan": "очередь: первых писем ждут отправки — 0, готовых лидов без письма — 0",
    }
    assert said[-1].lines[1] == "разгон на 08.03–14.03: включённых ящиков продаж — 0, писать нечем"
    for number, title in enumerate(TITLES, start=1):
        assert f"\n{number}. {title}: " in message
    assert "4. Ответы: ответов за неделю — 0" in message


# --- граница недели — по Москве ------------------------------------------------------------------


async def test_week_ends_at_moscow_midnight_not_utc(session: AsyncSession, rows: WeekRows) -> None:
    """Воскресенье 23:30 по Москве — эта неделя (20:30 UTC), понедельник 00:30 — уже следующая,
    хотя по UTC ещё воскресенье; понедельник 00:10 — эта, хотя по UTC ещё прошлая."""
    hypothesis = await rows.hypothesis("граница")
    for email, at in (
        ("sun@a.example.test", msk(7, 23, 30)),
        ("mon@b.example.test", msk(8, 0, 30)),
        ("start@c.example.test", msk(1, 0, 10)),
    ):
        lead = await rows.lead(hypothesis, email, at=msk(20, month=2))
        await rows.letter(lead, step=0, status=MessageStatus.DELIVERED, at=at)

    report = await _report(session)

    assert (report.total.funnel.sent, report.total.steps) == (2, (2, 0, 0))
    assert (await _report(session, WEEK.next)).total.funnel.sent == 1


def test_week_of_a_moment_is_counted_by_moscow_time() -> None:
    sunday_late_utc = datetime(2027, 3, 7, 21, 30, tzinfo=UTC)  # понедельник 00:30 по Москве

    assert Week.containing(sunday_late_utc) == Week(date(2027, 3, 8))
    assert Week.last_complete(sunday_late_utc) == WEEK
    assert (WEEK.since.isoformat(), WEEK.until.isoformat()) == (
        "2027-03-01T00:00:00+03:00",
        "2027-03-08T00:00:00+03:00",
    )
    assert WEEK.words() == "01.03–07.03"
    assert weekly.recent(sunday_late_utc)[:2] == [WEEK, Week(date(2027, 2, 22))]


# --- план: разгон ящиков на неделю -------------------------------------------------------------


async def test_plan_runway_follows_warmup_domain_and_direction_limits(
    session: AsyncSession, rows: WeekRows, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Потолок первых писем по дням отправки — правилами экрана доменов: разгон ящика, лимит
    домена, выдержка домена, лимит направления; выключенный ящик и ящик доноров не пишут."""
    monkeypatch.setattr(senders, "WARMUP_STEPS", (7, 13, 19))
    monkeypatch.setattr(senders, "WARMUP_STEP_DAYS", 2)
    monkeypatch.setattr(outreach_cfg, "DIRECTION_LIMITS", {"sales": 37})
    monkeypatch.setattr(sales_cfg, "SEND_DAYS", frozenset(range(5)))
    started = datetime(2027, 3, 8, 0, 30, tzinfo=UTC)
    await rows.box("young@one.example.test", started=started)
    await rows.box("grown@one.example.test", cap=23)
    await rows.box("fresh@two.example.test", cap=11)
    await rows.box("off@one.example.test", enabled=False)
    await rows.box("donors@three.example.test", stage=Stage.DONORS)
    await rows.domain("one.example.test", limit=29)
    await rows.domain(
        "two.example.test", limit=17, young_until=datetime(2027, 3, 10, 15, tzinfo=UTC)
    )

    report = await _report(session)

    runway = report.plan.runway
    assert runway.boxes == 3
    assert [(day.day.day, day.letters) for day in runway.days] == [
        (8, 29),
        (9, 29),
        (10, 29),
        (11, 37),
        (12, 37),
    ]
    plan = {section.key: section.lines for section in weekly_text.sections(report)}["plan"]
    assert plan[1] == (
        "разгон на 08.03–14.03: ящиков продаж — 3, первых писем до — 161 "
        "(пн — 29, вт — 29, ср — 29, чт — 37, пт — 37)"
    )


# --- виды ответа --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rule", "snapshot", "kind"),
    [
        (ReplyKind.HUMAN, {"stage": "sales", "kind": "question"}, "question"),
        (ReplyKind.HUMAN, {"stage": "sales", "kind": "parse_failed"}, UNPARSED),
        (ReplyKind.HUMAN, {"stage": "sales", "kind": None}, UNPARSED),
        (ReplyKind.HUMAN, None, UNPARSED),
        (ReplyKind.HUMAN, {"stage": "sales", "kind": "unsubscribe"}, "unsubscribe"),
        (ReplyKind.UNSUBSCRIBE, None, "unsubscribe"),
        (ReplyKind.AUTO_REPLY, None, AUTO),
        (ReplyKind.BOUNCE, None, None),
    ],
)
def test_reply_kind_of_the_report(
    rule: ReplyKind, snapshot: dict[str, object] | None, kind: str | None
) -> None:
    assert kind_of(rule, snapshot) == kind


# --- сообщение в группу: потолок Telegram ----------------------------------------------------------


async def test_long_lead_list_is_cut_to_fit_telegram_and_points_to_the_screen(
    session: AsyncSession, rows: WeekRows
) -> None:
    hypothesis = await rows.hypothesis("много лидов")
    for n in range(61):
        lead = await rows.lead(
            hypothesis, f"person{n}@lead{n}.example.test", at=msk(1), company=f"Company {n} " * 6
        )
        await rows.letter(lead, step=0, status=MessageStatus.DELIVERED, at=msk(2))
        await rows.hand_off(lead, at=msk(3, 10, n % 60))

    report = await _report(session)
    message = weekly_text.message(report)

    assert len(message) <= MAX_CHARS
    assert "передано телемаркетологу за неделю — 61" in message
    assert "— на экране отчёта" in message
    assert message.endswith(weekly_text.link(report.week))
    shown = {section.key: section for section in weekly_text.sections(report)}
    assert len(shown["leads"].lines) == 62, "экран показывает всех"


# --- выгрузка: те же числа ------------------------------------------------------------------------


async def test_csv_carries_the_same_numbers(session: AsyncSession, rows: WeekRows) -> None:
    first, _, _ = await week_world(rows)
    report = await _report(session)

    table = list(
        csv.reader(io.StringIO(weekly_text.to_csv(report).decode("utf-8-sig")), delimiter=";")
    )

    assert table[0] == list(weekly_text.CSV_HEAD)
    found = {(part, of, what): value for part, of, what, value in table[1:]}
    one = next(row for row in report.rows if row.hypothesis_id == first)
    for part, of, what, value in weekly_text.number_lines(one.name, one.numbers):
        assert found[(part, of, what)] == str(value)
    for part, of, what, value in weekly_text.number_lines("Всего", report.total):
        assert found[(part, of, what)] == str(value)
    assert found[("ответы", "свои сайты", "хочет говорить")] == "1"
    assert found[("доставляемость", "Всего", "жалоб на спам")] == "1"


async def test_csv_cell_that_looks_like_a_formula_is_inert(
    session: AsyncSession, rows: WeekRows
) -> None:
    hypothesis = await rows.hypothesis("формулы")
    lead = await rows.lead(hypothesis, "x@formula.example.test", at=msk(1), company="=HYPERLINK(1)")
    await rows.hand_off(lead, at=msk(2))

    table = weekly_text.to_csv(await _report(session)).decode("utf-8-sig")

    assert ";'=HYPERLINK(1) (formula.example.test)" in table
