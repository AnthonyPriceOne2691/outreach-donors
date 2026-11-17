"""Лента тревог: род — одним сообщением, время — с какого и сколько, длина — в потолке Telegram.

Слово владельца 17.11.2026: «нужно, чтобы алерты стали адекватнее и информативнее». Пришло
пятнадцать сообщений «Ящик … молчит» подряд и через десять минут пятнадцать «прошло»: ни когда
это началось, ни сколько длилось, ни что делать. Здесь держится:

- тревоги одного рода из одного прохода — одно сообщение со списком, «прошло» — так же и с
  длительностью; без рода и одна тревога рода — сообщение на тревогу; разная пометка — разные
  сообщения; прошли не все — «прошло» о прошедших;
- «тревога» называет минуту начала по Москве, «прошло» — когда шла и сколько: начало — первый
  проход, который её увидел (Telegram не принял — не сдвигается; повод появился — прежнее),
  конец — первый проход без неё; другой день — с датой;
- начало переживает перезапуск (заметки — своим хэшем рядом со сказанным); сказанное прежней
  версией без заметки — «прошло» без времени, а род оно получает с первого прохода; битая
  заметка — строка журнала, а не падение прохода;
- длинный список — первые строки и «и ещё M», сообщение — в потолке Telegram, пометка — последней;
- бот не настроен — одна строка «ТРЕВОГА НЕ ОТПРАВЛЕНА» на сообщение рода;
- лента бота продаж — тот же механизм; четыре тревоги почты задают род, имя и экран.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

import pytest
from backend.config import alerts as alerts_cfg
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, SenderStatus, Stage
from backend.features.core.stages import MailPolicy
from backend.features.ops import alarm_feed, alarm_words, mail_watch
from backend.features.ops.alarm_feed import QUIET_PASSES, Feed
from backend.features.ops.alarms import Alarm, Kind, cause, no_cause
from backend.features.outreach import senders
from backend.features.outreach.repository import EVERY_STAGE
from backend.features.sales.agent import notify
from backend.shared.alerts import MAX_CHARS, SOURCE
from backend.workers import reaper
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import WATCH_MINUTE
from tests.test_mail_watch import NOW, _letter
from tests.test_mail_watch_seams import FakeRedis
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_sales_stage_mail import sender

QUIET = Kind("Молчат ящики продаж", "Письма переписки ждут эти ящики, а с них не ушло ничего")
STANDS = cause("переписки ящика стоят", "проверить, идёт ли проход добивок")
SCREEN = "Домены рассылки продаж"


def _quiet(number: int, waiting: int = 3, *, item: str = "") -> Alarm:
    """Ящик продаж молчит — как тревога сторожа почты: род, имя, число и экран."""
    box = f"box{number:02}@example.com"
    return Alarm(
        code=f"quiet-box:{box}",
        title=f"Ящик {box} молчит",
        detail=f"Писем ждут его: {waiting}",
        stage=Stage.SALES,
        concern=STANDS,
        kind=QUIET,
        item=item or box,
        brief=f"писем ждут: {waiting}",
        screen=SCREEN,
    )


@dataclass
class Clock:
    """Часы слов ленты: идут, когда тест их двигает."""

    now: datetime = WATCH_MINUTE

    def __call__(self) -> datetime:
        return self.now

    def advance(self, minutes: float) -> None:
        self.now += timedelta(minutes=minutes)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    found = Clock()
    monkeypatch.setattr(alarm_feed, "wall_clock", found)
    return found


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Что лента сказала в Telegram — по порядку; Telegram принимает всё."""
    said: list[str] = []

    async def send(text: str) -> bool:
        said.append(text)
        return True

    monkeypatch.setattr(alarm_feed, "send_alert", send)
    return said


@pytest.fixture
def told_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    """Redis сказанного — подделкой: переживает «перезапуск» (новый `Feed`)."""
    redis = FakeRedis()
    monkeypatch.setattr(alarm_feed, "connection", lambda: redis)
    return redis


async def _passes(feed: Feed, clock: Clock, found: list[Alarm], *, every: float = 2) -> None:
    """`QUIET_PASSES` проходов ленты с одними тревогами, раз в `every` минут."""
    for _ in range(QUIET_PASSES):
        await feed.tell(found)
        clock.advance(every)


# --- род одним сообщением ------------------------------------------------------------------------


async def test_fifteen_boxes_of_one_kind_are_one_alarm_and_one_all_clear(
    telegram: list[str], clock: Clock
) -> None:
    """Пример 17.11.2026: пятнадцать ящиков молчат по одной причине — одно «тревога» со списком
    и одно «прошло» с длительностью вместо тридцати сообщений."""
    feed = Feed("kinds-fifteen")
    await feed.tell([_quiet(number, waiting=number + 1) for number in range(15)])
    clock.advance(10)
    await _passes(feed, clock, [])

    assert telegram == [
        "тревога: Молчат ящики продаж — 15. С 03:11 МСК.\n"
        "Письма переписки ждут эти ящики, а с них не ушло ничего.\n"
        + "".join(f"• box{n:02}@example.com — писем ждут: {n + 1}\n" for n in range(15))
        + f"Где смотреть: экран «{SCREEN}».\n{STANDS.words}",
        "прошло: Молчат ящики продаж — 15. 03:11–03:21 МСК, 10 минут.\n"
        + "\n".join(f"• box{n:02}@example.com" for n in range(15)),
    ]


async def test_what_passed_first_is_said_first_and_the_rest_later(
    telegram: list[str], clock: Clock
) -> None:
    """Прошли не все — «прошло» о прошедших; последний ящик рода — своими словами."""
    feed = Feed("kinds-partial")
    first, second, last = _quiet(1), _quiet(2), _quiet(3)
    await feed.tell([first, second, last])
    clock.advance(4)
    await _passes(feed, clock, [last])  # 03:15 и 03:17 — первые два прошли
    clock.advance(6)
    await _passes(feed, clock, [])  # 03:25 — прошёл и третий

    assert telegram[1:] == [
        "прошло: Молчат ящики продаж — 2. 03:11–03:15 МСК, 4 минуты.\n"
        "• box01@example.com\n• box02@example.com",
        "прошло: Ящик box03@example.com молчит. 03:11–03:25 МСК, 14 минут.",
    ]


async def test_other_kinds_marks_and_lone_alarms_go_on_their_own(telegram: list[str]) -> None:
    """Род — вместе с пометкой: она одна строка, последняя. Без рода и одна тревога рода —
    сообщение своими словами: подробности полнее строки списка."""
    calm = replace(_quiet(3), concern=no_cause("ящик на выдержке"))
    lone = Alarm(code="runs-stuck", title="Прогон стоит", detail="Отметки жизни нет 40 минут")
    other = replace(_quiet(4), kind=Kind("Выбыли ящики продаж"))

    await Feed("kinds-apart").tell([_quiet(1), lone, _quiet(2), calm, other])

    assert [text.splitlines()[0] for text in telegram] == [
        "тревога: Молчат ящики продаж — 2. С 03:11 МСК.",
        "тревога: Прогон стоит. С 03:11 МСК.",
        "тревога: Ящик box03@example.com молчит. С 03:11 МСК.",
        "тревога: Ящик box04@example.com молчит. С 03:11 МСК.",
    ]
    assert telegram[1] == "тревога: Прогон стоит. С 03:11 МСК.\nОтметки жизни нет 40 минут."
    assert telegram[2] == (
        "тревога: Ящик box03@example.com молчит. С 03:11 МСК.\nПисем ждут его: 3.\n"
        f"Где смотреть: экран «{SCREEN}».\nПовода для беспокойства нет — ящик на выдержке."
    )


async def test_the_watchdog_names_the_overview_where_an_alarm_has_no_screen(
    telegram: list[str],
) -> None:
    """Тревоги сторожа видны на «Обзоре»: его называет строка «где смотреть» тревоги без своего
    экрана, а свой экран тревоги — сильнее."""
    watchdog = Feed(reaper.FEED.name, screen=reaper.FEED.screen)
    lone = Alarm(code="runs-stuck", title="Прогон стоит", detail="Отметки жизни нет 40 минут")

    await watchdog.tell([lone, _quiet(1)])

    assert reaper.FEED.screen == "Обзор"
    assert [line for text in telegram for line in text.splitlines() if "Где смотреть" in line] == [
        "Где смотреть: экран «Обзор».",
        f"Где смотреть: экран «{SCREEN}».",
    ]


async def test_a_later_member_of_the_kind_names_its_own_start(
    telegram: list[str], clock: Clock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Telegram не принял — начало тревоги не сдвигается: в сообщении рода, которое ушло позже,
    у каждой тревоги своя минута — у моложе прочих она в строке."""
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "made-up-token")
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_CHAT_ID", "made-up-chat")
    answers = iter([False, False, True])

    async def refusing(text: str) -> bool:
        telegram.append(text)
        return next(answers)

    monkeypatch.setattr(alarm_feed, "send_alert", refusing)
    feed = Feed("kinds-refused")
    await feed.tell([_quiet(1)])  # 03:11 — не принят
    clock.advance(4)
    await feed.tell([_quiet(1), _quiet(2)])  # 03:15 — не принят
    clock.advance(2)
    await feed.tell([_quiet(1), _quiet(2)])  # 03:17 — принят

    assert telegram[-1].splitlines()[:4] == [
        "тревога: Молчат ящики продаж — 2. С 03:11 МСК.",
        "Письма переписки ждут эти ящики, а с них не ушло ничего.",
        "• box01@example.com — писем ждут: 3",
        "• box02@example.com — писем ждут: 3, с 03:15",
    ]


async def test_a_cause_that_appears_keeps_the_start(telegram: list[str], clock: Clock) -> None:
    """Повод появился — тревога ещё раз, с прежней минутой начала: видно, что она не новая."""
    calm = replace(_quiet(1), concern=no_cause("ящик на выдержке"))
    feed = Feed("kinds-grows")
    await feed.tell([calm])
    clock.advance(40)
    await feed.tell([_quiet(1)])

    assert [text.splitlines()[0] for text in telegram] == [
        "тревога: Ящик box01@example.com молчит. С 03:11 МСК.",
        "тревога: Ящик box01@example.com молчит. С 03:11 МСК.",
    ]
    assert telegram[-1].endswith(STANDS.words)


# --- время: перезапуск, прежняя версия, другой день ----------------------------------------------


async def test_the_start_survives_a_restart(
    telegram: list[str], clock: Clock, told_redis: FakeRedis
) -> None:
    """Выкатка между тревогой и её концом: новый процесс знает, с какого времени она шла."""
    await Feed("kinds-restart").tell([_quiet(1)])
    clock.advance(30)

    await _passes(Feed("kinds-restart"), clock, [])  # процесс перезапущен, тревоги нет

    assert telegram[-1] == "прошло: Ящик box01@example.com молчит. 03:11–03:41 МСК, 30 минут."
    assert told_redis.hashes == {}, "о прошедшей тревоге Redis не помнит ничего"


async def test_an_alarm_told_before_the_notes_learns_its_kind(
    telegram: list[str], told_redis: FakeRedis
) -> None:
    """Сказанное прежней версией — «код → заголовок» без заметки: тревоги не повторяются, род
    берут с первого прохода, «прошло» о них — одним сообщением, но без времени: начала лента не
    знает."""
    feed = Feed("kinds-rollout")
    old = [_quiet(1), _quiet(2)]
    told_redis.hashes[alarm_feed.told_key(feed.name)] = {one.code: one.title for one in old}

    await feed.tell(old)
    learned = dict(told_redis.hashes[alarm_feed.notes_key(feed.name)])
    for _ in range(QUIET_PASSES):
        await feed.tell([])

    assert set(learned) == {one.code for one in old}
    assert telegram == ["прошло: Молчат ящики продаж — 2\n• box01@example.com\n• box02@example.com"]


async def test_a_broken_note_does_not_stop_the_watch(
    telegram: list[str], told_redis: FakeRedis, caplog: pytest.LogCaptureFixture
) -> None:
    """Заметка в Redis не JSON (чужая запись) — строка журнала и «прошло» без времени, а не
    исключение, которое ронял бы каждый проход сторожа."""
    feed = Feed("kinds-broken")
    told_redis.hashes[alarm_feed.told_key(feed.name)] = {"quiet-box:x": "Ящик x молчит"}
    told_redis.hashes[alarm_feed.notes_key(feed.name)] = {"quiet-box:x": b"\xd0{"}

    with caplog.at_level(logging.WARNING, logger=alarm_feed.__name__):
        for _ in range(QUIET_PASSES):
            await feed.tell([])

    assert telegram == ["прошло: Ящик x молчит"]
    assert "заметка о сказанной тревоге не разобрана" in caplog.text


async def test_an_alarm_of_yesterday_names_its_day(telegram: list[str], clock: Clock) -> None:
    clock.now = datetime.fromisoformat("2026-11-16T20:50:00+00:00")  # 23:50 по Москве
    feed = Feed("kinds-midnight")
    await feed.tell([_quiet(1)])
    clock.advance(30)
    await _passes(feed, clock, [])

    assert telegram[-1] == (
        "прошло: Ящик box01@example.com молчит. 16.11 23:50–00:20 МСК, 30 минут."
    )


def test_the_duration_reads_as_words() -> None:
    assert [alarm_words.counted(n, alarm_words.MINUTES) for n in (1, 3, 5, 11, 21, 22, 112)] == [
        "1 минута",
        "3 минуты",
        "5 минут",
        "11 минут",
        "21 минута",
        "22 минуты",
        "112 минут",
    ]
    assert [alarm_words.lasted(minutes) for minutes in (1, 59, 60, 125, 1440, 1500, 3000)] == [
        "1 минута",
        "59 минут",
        "1 час",
        "2 часа 5 минут",
        "1 день",
        "1 день 1 час",
        "2 дня 2 часа",
    ]


# --- длина ----------------------------------------------------------------------------------------


@pytest.mark.parametrize("item", ["", "x" * 250], ids=["короткие строки", "длинные строки"])
async def test_a_long_kind_is_cut_into_the_telegram_ceiling(
    telegram: list[str], clock: Clock, item: str
) -> None:
    """Шестьдесят тревог рода: в списке — первые `LISTED` или сколько влезает, дальше —
    «и ещё M»; сообщение в потолке Telegram, пометка — последней строкой целиком."""
    found = [_quiet(number, item=f"{item}{number:02}" if item else "") for number in range(60)]
    feed = Feed("kinds-long")
    await feed.tell(found)
    clock.advance(10)
    await _passes(feed, clock, [])

    alarmed, passed = telegram
    for text in (alarmed, passed):
        lines = text.splitlines()
        named = [line for line in lines if line.startswith("• ") and "и ещё" not in line]
        assert len(f"{SOURCE}: {text}") <= MAX_CHARS
        assert 0 < len(named) <= alarm_words.LISTED
        assert f"• и ещё {60 - len(named)}" in lines
    assert alarmed.endswith(f"\n{STANDS.words}")
    if not item:
        assert "• и ещё 40" in alarmed.splitlines()


async def test_without_a_bot_a_kind_is_one_journal_line(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Бот не настроен — громкая строка журнала одна на сообщение рода, а не на тревогу."""
    monkeypatch.setattr(alerts_cfg, "TELEGRAM_BOT_TOKEN", "")
    feed = Feed("kinds-journal")

    with caplog.at_level(logging.ERROR):
        await feed.tell([_quiet(number) for number in range(5)])
        await feed.tell([_quiet(number) for number in range(5)])

    assert caplog.text.count("ТРЕВОГА НЕ ОТПРАВЛЕНА") == 1
    assert "Молчат ящики продаж — 5" in caplog.text


async def test_the_sales_bot_feed_groups_by_the_same_mechanism(telegram: list[str]) -> None:
    """Лента бота продаж — та же лента (`notify.BOT_FEED`): род работает и у неё."""
    bot = Feed(notify.BOT_FEED.name)  # лента бота продаж после перезапуска

    await bot.tell([_quiet(1), _quiet(2)])

    assert isinstance(notify.BOT_FEED, Feed)
    assert [text.splitlines()[0] for text in telegram] == [
        "тревога: Молчат ящики продаж — 2. С 03:11 МСК."
    ]


# --- тревоги почты: род, имя и экран ---------------------------------------------------------------


@pytest.fixture
def sales_watched(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Продажи со сторожем в политике — подставным модулем, как в `tests/test_mail_watch.py`."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(watch=True))
    stages.register_sales(lambda: found)
    return found


@pytest.mark.usefixtures("sales_watched")
async def test_two_quiet_sales_boxes_are_one_message(
    session: AsyncSession, telegram: list[str]
) -> None:
    for number, email in enumerate(("anna@mail-a.example.com", "boris@mail-b.example.com")):
        box = await sender(session, email, Stage.SALES)
        await _letter(
            session,
            Stage.SALES,
            box,
            number,
            status=MessageStatus.SENT,
            sent_at=NOW - timedelta(days=3),
            next_action_at=NOW - timedelta(minutes=20),
        )

    await Feed("kinds-quiet").tell(await mail_watch.alarms(session, NOW, stages=EVERY_STAGE))

    [said] = telegram
    assert said.splitlines()[0] == "тревога: Молчат ящики продаж — 2. С 03:11 МСК."
    assert "• anna@mail-a.example.com — писем ждут: 1" in said.splitlines()
    assert "Где смотреть: экран «Домены рассылки продаж»." in said.splitlines()


@pytest.mark.usefixtures("sales_watched")
async def test_two_retired_sales_boxes_are_one_message(
    session: AsyncSession, telegram: list[str]
) -> None:
    await sender(session, "writing@mail-w.example.com", Stage.SALES)
    for number, email in enumerate(("anna@mail-a.example.com", "boris@mail-b.example.com")):
        box = await sender(session, email, Stage.SALES)
        senders.disable(box, "выдуманная причина паузы", now=NOW - timedelta(minutes=3))
        await _letter(
            session,
            Stage.SALES,
            box,
            number,
            status=MessageStatus.SENT,
            sent_at=NOW - timedelta(days=3),
            next_action_at=NOW + timedelta(days=1),
        )

    await Feed("kinds-retired").tell(await mail_watch.alarms(session, NOW, stages=EVERY_STAGE))

    [said] = telegram
    assert said.splitlines()[:4] == [
        "тревога: Выбыли ящики продаж — 2. С 03:11 МСК.",
        "Ящики выключены, на паузе или удалены, или их домен на паузе, а письма их переписок "
        "ждут именно их: с другого ящика они не уйдут.",
        "• anna@mail-a.example.com — писем ждут: 1",
        "• boris@mail-b.example.com — писем ждут: 1",
    ]
    assert said.endswith(mail_watch.RETIRED.words)


@pytest.mark.usefixtures("sales_watched")
async def test_a_paused_direction_names_its_kind_and_screen(session: AsyncSession) -> None:
    """«Все на паузе» и «отправить некому» — по одной на направление: род у них общий для
    направлений, имя в списке — направление, экран — его «Домены рассылки»."""
    box = await sender(session, "hello@mail-sales.example.com", Stage.SALES)
    box.status = SenderStatus.PAUSED
    await _letter(session, Stage.SALES, None, status=MessageStatus.QUEUED)

    found = await mail_watch.alarms(session, NOW, stages=EVERY_STAGE)

    assert [
        (alarm.kind.title if alarm.kind else "", alarm.item, alarm.brief, alarm.screen)
        for alarm in found
    ] == [
        ("Все ящики направления на паузе", "направление продаж", "ящиков 1", SCREEN),
        (
            "Очередь есть — отправить некому",
            "направление продаж",
            "первых писем в очереди: 1",
            SCREEN,
        ),
    ]
