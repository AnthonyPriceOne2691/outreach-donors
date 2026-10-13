"""Автоотправка первых писем продаж — проход на настоящей базе, почта подставная, часы у теста.

Мир — подключённые продажи и очередь, собранная сборкой продаж (`test_sales_send_world.py`);
окно получателя там открыто всегда, тесты окна задают его сами. Почта — нулевой транспорт с
памятью: живых писем нет, адреса — `*.example.test`. Проход получает минуту от теста (`now`):
так видно паузу ящика и окно получателя без ожидания. Тревога и журнал по смене —
`test_sales_autosend_watch.py`, гонка с пачкой — `test_sales_autosend_race.py`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from backend.config import sales as sales_cfg
from backend.features.core.domain import ContactSource, MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import MessageModel, SendingDomainModel
from backend.features.letters import mailbox
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.repository import LetterRepository
from backend.features.letters.sendgrid import SendGridTransport
from backend.features.letters.sending import Sending
from backend.features.letters.transport import Outgoing, TransportError
from backend.features.letters.unknown_outcome import STUCK_AFTER
from backend.features.outreach import senders
from backend.features.sales import autosend, queue
from backend.features.sales.autosend import Stop
from backend.features.sales.models import SalesStoplistModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_mail_accounts import _ByStage
from tests.test_mail_identity import Recording
from tests.test_sales_send import _seen, _transports

NOW = w.NOW
BERLIN = ZoneInfo("Europe/Berlin")
#: Суббота и понедельник, 10:00 по часам получателя в Берлине.
SATURDAY = datetime(2026, 10, 10, 10, 0, tzinfo=BERLIN).astimezone(UTC)
MONDAY = datetime(2026, 10, 12, 10, 0, tzinfo=BERLIN).astimezone(UTC)

JANE = "jane@acme.example.test"
OLGA = "olga@beta.example.test"
IVAN = "ivan@gamma.example.test"
SECOND_BOX = "nora@mail-sales-two.example.test"


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


async def _letters(session: AsyncSession, world: w.World, *emails: str) -> list[MessageModel]:
    """Лиды гипотезы и их первые письма — сборкой продаж, по порядку адресов."""
    for email in emails:
        await w.lead(session, world.hypothesis_id, email, name=email.split("@")[0].title())
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=10)
    rows = await session.scalars(
        select(MessageModel).where(MessageModel.step == FIRST_STEP).order_by(MessageModel.id)
    )
    return list(rows)


def _weekdays_nine_to_five(monkeypatch: pytest.MonkeyPatch) -> None:
    """Окно продаж — пн–пт 9:00–17:00 по часам получателя."""
    monkeypatch.setattr(sales_cfg, "SEND_DAYS", frozenset(range(5)))
    monkeypatch.setattr(sales_cfg, "SEND_OPENS", time(9))
    monkeypatch.setattr(sales_cfg, "SEND_CLOSES", time(17))


async def _status(session: AsyncSession, letter: MessageModel) -> MessageStatus:
    await session.refresh(letter)
    return letter.status


# --- окно получателя ---------------------------------------------------------------------------


async def test_window_closed_nothing_goes_opened_one_letter_per_box_per_pass(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    letters = await _letters(session, world, JANE, OLGA, IVAN)
    second = await w.mailbox(session, SECOND_BOX)
    _weekdays_nine_to_five(monkeypatch)
    source = _transports()

    closed = await autosend.run(session, source, now=SATURDAY)

    assert (closed.sent, closed.refused, closed.stop) == ({}, {}, Stop.OUTSIDE)
    assert closed.stopped == "вне окна получателя у всех писем очереди (3)"
    assert source.asked == []

    opened = await autosend.run(session, source, now=MONDAY)

    # По письму с ящика: два ящика — два письма, третье ждёт следующего прохода.
    assert list(opened.sent) == [letters[0].id, letters[1].id]
    assert sorted(opened.sent.values()) == sorted([w.SALES_BOX, second.email])
    assert [outgoing.to for outgoing in _seen(source)] == [JANE, OLGA]
    assert opened.stop is Stop.RESTING
    assert await _status(session, letters[2]) is MessageStatus.QUEUED


async def test_only_letters_whose_recipient_has_the_window_now_go_oldest_first(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Понедельник 10:00 в Берлине — 01:00 в Лос-Анджелесе: письмо калифорнийцу ждёт, хоть оно
    и старше в очереди; следующее — берлинцу."""
    await w.lead(session, world.hypothesis_id, JANE, timezone="America/Los_Angeles")
    letters = await _letters(session, world, OLGA)
    _weekdays_nine_to_five(monkeypatch)

    seen = await autosend.in_window(session, MONDAY)
    found = await autosend.run(session, _transports(), now=MONDAY)

    assert (seen.queued, seen.ids) == (2, (letters[1].id,))
    assert list(found.sent) == [letters[1].id]
    assert await _status(session, letters[0]) is MessageStatus.QUEUED


async def test_without_any_zone_the_letter_is_not_in_the_window_and_the_words_say_why(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await w.lead(session, world.hypothesis_id, JANE, timezone=None, country=None)
    await _letters(session, world)
    _weekdays_nine_to_five(monkeypatch)

    found = await autosend.run(session, _transports(), now=MONDAY)

    assert found.stop is Stop.OUTSIDE
    assert found.stopped == (
        "вне окна получателя у всех писем очереди (1), у 1 из них пояс получателя неизвестен "
        "— окна не посчитать"
    )


# --- пауза ящика -------------------------------------------------------------------------------


async def test_box_pause_holds_with_its_spread_and_is_never_below_the_lower_edge(
    session: AsyncSession, world: w.World
) -> None:
    letters = await _letters(session, world, JANE, OLGA)
    source = _transports()

    first = await autosend.run(session, source, now=NOW)
    pause = autosend.pause_of(world.sales_box.id, letters[0].id)
    lower = autosend.gap() * (1 - autosend.SPREAD)
    early = await autosend.run(session, source, now=NOW + lower - timedelta(seconds=1))
    still = await autosend.run(session, source, now=NOW + pause - timedelta(seconds=1))
    later = await autosend.run(session, source, now=NOW + pause)

    assert list(first.sent) == [letters[0].id]
    assert lower <= pause <= autosend.gap() * (1 + autosend.SPREAD)
    assert (early.sent, early.stop) == ({}, Stop.RESTING)
    assert early.stopped == (
        f"ящики на паузе между письмами — ближайший освободится в {NOW + pause:%H:%M} UTC"
    )
    assert (still.sent, still.stop) == ({}, Stop.RESTING)
    assert list(later.sent) == [letters[1].id]


async def test_box_pause_counts_from_a_letter_of_the_button_too(
    session: AsyncSession, world: w.World
) -> None:
    """Пачка кнопкой выпустила письмо с ящика — проход ждёт его паузы так же."""
    letters = await _letters(session, world, JANE, OLGA)
    source = _transports()

    await Sending(session, source, now=NOW).send(letters[0].id)
    found = await autosend.run(session, source, now=NOW + timedelta(minutes=1))

    assert (found.sent, found.stop) == ({}, Stop.RESTING)


def test_pause_is_spread_across_the_whole_range_and_stable_per_letter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sales_cfg, "AUTOSEND_GAP_MIN", 20)
    pauses = [autosend.pause_of(box, letter) for box in range(1, 6) for letter in range(1, 41)]

    assert all(timedelta(minutes=10) <= pause <= timedelta(minutes=30) for pause in pauses)
    # Разброс — по всей ширине, а не у одного края.
    assert min(pauses) < timedelta(minutes=12)
    assert max(pauses) > timedelta(minutes=28)
    assert len(set(pauses)) > 150
    # Один и тот же между проходами: зерно — ящик и письмо.
    assert autosend.pause_of(3, 17) == autosend.pause_of(3, 17)
    assert autosend.pause_of(3, 17) != autosend.pause_of(4, 17)


# --- лимиты ------------------------------------------------------------------------------------


async def test_daily_cap_reached_stops_the_pass_in_words(
    session: AsyncSession, world: w.World
) -> None:
    world.sales_box.daily_cap = 1
    letters = await _letters(session, world, JANE, OLGA)
    source = _transports()

    first = await autosend.run(session, source, now=NOW)
    after = await autosend.run(session, source, now=NOW + timedelta(hours=1))

    assert list(first.sent) == [letters[0].id]
    assert (after.sent, after.stop) == ({}, Stop.CAP)
    assert after.stopped is not None
    assert after.stopped.startswith("Сегодня писать некому")
    assert await _status(session, letters[1]) is MessageStatus.QUEUED


async def test_warmup_allowance_is_the_cap_of_a_young_box(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(senders, "WARMUP_STEPS", (3, 7, 11))
    world.sales_box.warmup_started_at = NOW
    allowance = senders.warmup_state(world.sales_box, now=NOW).allowance
    assert allowance == 3
    emails = [f"lead{number}@acme{number}.example.test" for number in range(allowance + 1)]
    await _letters(session, world, *emails)
    source = _transports()

    passes = [
        await autosend.run(session, source, now=NOW + timedelta(minutes=30) * number)
        for number in range(allowance + 1)
    ]

    assert sum(len(found.sent) for found in passes) == allowance
    assert passes[-1].stop is Stop.CAP


async def test_domain_on_hold_keeps_its_boxes_silent(session: AsyncSession, world: w.World) -> None:
    letters = await _letters(session, world, JANE)
    session.add(
        SendingDomainModel(
            domain="mail-sales.example.test",
            stage=Stage.SALES,
            daily_limit=30,
            young_until=NOW + timedelta(days=3),
        )
    )
    await session.flush()

    held = await autosend.run(session, _transports(), now=NOW)
    other = await w.mailbox(session, SECOND_BOX)
    later = await autosend.run(session, _transports(), now=NOW + timedelta(minutes=1))

    assert (held.sent, held.stop) == ({}, Stop.CAP)
    assert held.stopped is not None
    assert "домен на выдержке: mail-sales.example.test" in held.stopped
    assert later.sent == {letters[0].id: other.email}


# --- отказы по письму --------------------------------------------------------------------------


class Guarded(Recording):
    """Почта с предохранителем: адрес `refused` — не из своих (слова `sendgrid.py`)."""

    def __init__(self, refused: str) -> None:
        super().__init__()
        self.refused = refused

    async def send(self, outgoing: Outgoing) -> str:
        if outgoing.to == self.refused:
            raise TransportError(
                f"Адрес {outgoing.to} не в списке разрешённых получателей "
                "(OUTREACH_SALES_ALLOWED_RECIPIENTS)"
            )
        return await super().send(outgoing)


async def test_stoplist_and_allowlist_refuse_a_letter_and_the_pass_goes_on(
    session: AsyncSession, world: w.World
) -> None:
    letters = await _letters(session, world, JANE, OLGA, IVAN)
    session.add(SalesStoplistModel(email=JANE))
    await session.flush()
    guarded = Guarded(OLGA)
    source = _ByStage(sales=guarded, donors=Recording(), advertisers=Recording())

    found = await autosend.run(session, source, now=NOW)

    assert found.refused == {
        letters[0].id: "стоп-лист",
        letters[1].id: "предохранитель: адрес не из своих",
    }
    # Отказ ящика не тратит: третье письмо ушло тем же ящиком в том же проходе.
    assert found.sent == {letters[2].id: w.SALES_BOX}
    assert [outgoing.to for outgoing in guarded.seen] == [IVAN]
    assert await _status(session, letters[0]) is MessageStatus.QUEUED
    assert await _status(session, letters[1]) is MessageStatus.QUEUED


def _sendgrid(status: int) -> tuple[_ByStage, list[str]]:
    """Боевой транспорт продаж на подставном HTTP: платформа отвечает `status` на каждое письмо.
    Второй — адресаты писем, которые проход отдал платформе."""
    asked: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        asked.append(json.loads(request.content)["personalizations"][0]["to"][0]["email"])
        return httpx.Response(status, text='{"errors":[{"message":"made-up refusal"}]}')

    http = httpx.AsyncClient(transport=httpx.MockTransport(answer))
    sales = SendGridTransport(api_key="SG.made-up-sales-key", allowlist=(), http=http)
    return _ByStage(sales=sales, donors=Recording(), advertisers=Recording()), asked


async def test_platform_refusing_every_letter_stops_the_pass_on_the_first_one(
    session: AsyncSession, world: w.World
) -> None:
    """Ключ не принят (401): ответ был бы тем же любому письму. Проход взял одно письмо — оно
    вернулось в очередь, — а следующие не берёт: иначе каждую минуту захватывал бы и возвращал
    всё окно. Слова — без ключа и адреса."""
    letters = await _letters(session, world, JANE, OLGA, IVAN)
    await w.mailbox(session, SECOND_BOX)
    source, asked = _sendgrid(401)

    found = await autosend.run(session, source, now=NOW)

    assert asked == [JANE]
    assert (found.sent, found.refused, found.stop, found.due) == ({}, {}, Stop.REFUSED, True)
    assert found.stopped == (
        f"платформа отказывает: ключ платформы не принят (401) — письмо №{letters[0].id} "
        "вернулось в очередь, следующие письма проход не берёт: ответ был бы тем же"
    )
    assert [await _status(session, letter) for letter in letters] == [MessageStatus.QUEUED] * 3


async def test_a_refusal_of_this_letter_lets_the_pass_take_the_next_ones(
    session: AsyncSession, world: w.World
) -> None:
    """Отказ о письме (400 — адресат, содержимое) — как прежде: письмо ждёт с причиной, проход
    берёт следующие. Отказ получило каждое письмо окна, а не ушло ни одно — проход стоит не по
    делу словами (`REFUSED_ALL`, тревога через 15 минут — `test_sales_seam_send_stalls.py`)."""
    letters = await _letters(session, world, JANE, OLGA, IVAN)
    source, asked = _sendgrid(400)

    found = await autosend.run(session, source, now=NOW)

    assert asked == [JANE, OLGA, IVAN]
    assert found.refused == {letter.id: "почта отказала" for letter in letters}
    assert (found.sent, found.stop) == ({}, Stop.REFUSED_ALL)
    assert found.stopped == (
        "ни одно письмо в окне получателя не ушло — почта отказала: 3; причина каждого — в "
        "журнале разбора"
    )


# --- тумблеры и чужие письма -------------------------------------------------------------------


async def test_sales_switched_off_the_pass_sends_nothing_and_says_why(
    session: AsyncSession, world: w.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _letters(session, world, JANE)
    monkeypatch.setattr(sales_cfg, "ENABLED", False)
    source = _transports()

    found = await autosend.run(session, source, now=NOW)

    assert (found.sent, found.stop) == ({}, Stop.NOT_CONNECTED)
    assert found.stopped is not None
    assert "модуль продаж выключен" in found.stopped
    assert source.asked == []


async def test_donor_letters_are_not_taken(session: AsyncSession, world: w.World) -> None:
    donor = DomainModel(host="donor.example.test")
    session.add(donor)
    await session.flush()
    contact = ContactModel(
        domain_id=donor.id, email="editor@donor.example.test", source=ContactSource.PAGE
    )
    session.add(contact)
    campaign = await LetterRepository(session).campaign(
        name="Доноры", stage=Stage.DONORS, followup_days=(3, 7)
    )
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            domain_id=donor.id,
            contact_id=contact.id,
            step=FIRST_STEP,
            status=MessageStatus.QUEUED,
            subject="Made-up donor subject",
            body="Made-up donor body",
            idempotency_key="donors:autosend:1",
        )
    )
    await session.flush()
    source = _transports()

    found = await autosend.run(session, source, now=NOW)

    assert (found.sent, found.stop) == ({}, Stop.EMPTY)
    assert source.asked == []


# --- исход неизвестен --------------------------------------------------------------------------


async def test_unknown_outcome_stops_the_pass_in_words_until_it_is_settled(
    session: AsyncSession, world: w.World
) -> None:
    letters = await _letters(session, world, JANE, OLGA)
    letters[0].status = MessageStatus.SENDING
    letters[0].updated_at = NOW - STUCK_AFTER
    await session.flush()
    source = _transports()

    stuck = await autosend.run(session, source, now=NOW)

    assert (stuck.sent, stuck.stop, stuck.due) == ({}, Stop.UNKNOWN, True)
    assert stuck.stopped is not None
    assert stuck.stopped.startswith(f"исход письма №{letters[0].id} неизвестен")
    assert source.asked == []

    letters[0].status = MessageStatus.SENT
    await session.flush()
    went = await autosend.run(session, source, now=NOW)

    assert list(went.sent) == [letters[1].id]


async def test_a_broken_connection_leaves_the_letter_unknown_and_the_pass_waits_on_it(
    session: AsyncSession, world: w.World
) -> None:
    """Связь оборвалась посреди передачи: проход падает (как пачка, слать дальше вслепую
    нельзя), письмо остаётся «отправляется» — и с `STUCK_AFTER`, в блоке «Исход неизвестен»,
    проход на нём стоит словами."""
    letters = await _letters(session, world, JANE, OLGA)

    class Silent(Recording):
        async def send(self, outgoing: Outgoing) -> str:
            raise ConnectionResetError("соединение сброшено")

    source = _ByStage(sales=Silent(), donors=Recording(), advertisers=Recording())

    with pytest.raises(ConnectionResetError):
        await autosend.run(session, source, now=NOW)
    # Захват пишет время базы, а часы прохода — у теста: передача началась минутой прохода.
    letters[0].updated_at = NOW
    await session.flush()
    after = await autosend.run(session, _transports(), now=NOW + STUCK_AFTER)

    assert await _status(session, letters[0]) is MessageStatus.SENDING
    assert after.stop is Stop.UNKNOWN
    assert await _status(session, letters[1]) is MessageStatus.QUEUED


async def test_a_letter_another_sending_hands_over_right_now_does_not_stop_the_pass(
    session: AsyncSession, world: w.World
) -> None:
    """Добивка, ответ или кнопка передают письмо продаж почте в ту же минуту: оно «отправляется»
    секунды, худшая передача — две с половиной минуты. Проход его не ждёт и шлёт своё; с
    `STUCK_AFTER` письмо — в блоке «Исход неизвестен», и проход на нём стоит."""
    letters = await _letters(session, world, JANE, OLGA)
    letters[0].status = MessageStatus.SENDING
    letters[0].updated_at = NOW - STUCK_AFTER + timedelta(seconds=1)
    await session.flush()
    source = _transports()

    going = await autosend.run(session, source, now=NOW)
    stuck = await autosend.run(session, source, now=NOW + timedelta(seconds=1))

    assert (going.sent, going.stop) == ({letters[1].id: w.SALES_BOX}, None)
    assert stuck.stop is Stop.UNKNOWN
    assert stuck.stopped is not None
    assert stuck.stopped.startswith(f"исход письма №{letters[0].id} неизвестен")


# --- общий выбор ящика: пауза автоотправки ----------------------------------------------------


async def test_resting_boxes_are_left_out_of_the_first_letter_choice_only_inside_the_block(
    session: AsyncSession, world: w.World
) -> None:
    [letter] = await _letters(session, world, JANE)
    other = await w.mailbox(session, SECOND_BOX)

    async def chosen() -> mailbox.Choice:
        return await mailbox.choose(
            session, letter, stage=Stage.SALES, thread_sender_id=None, now=NOW
        )

    before = await chosen()
    with mailbox.resting({world.sales_box.id}):
        rested = await chosen()
    with mailbox.resting({world.sales_box.id, other.id}):
        nobody = await chosen()
        # Письмо переписки уходит с ящика переписки: пауза первых писем его не держит.
        thread = await mailbox.choose(
            session, letter, stage=Stage.SALES, thread_sender_id=other.id, now=NOW
        )
    after = await chosen()

    assert _box(before) == world.sales_box.id
    assert _box(rested) == other.id
    assert (nobody.sender, nobody.refusal) == (None, f"Сейчас писать некому: {mailbox.RESTING}")
    assert _box(thread) == other.id
    assert _box(after) == world.sales_box.id


def _box(choice: mailbox.Choice) -> int | None:
    return None if choice.sender is None else choice.sender.id


async def test_resting_boxes_with_the_cap_spent_are_refused_by_the_cap_not_the_pause(
    session: AsyncSession, world: w.World
) -> None:
    world.sales_box.daily_cap = 0
    [letter] = await _letters(session, world, JANE)

    with mailbox.resting({world.sales_box.id}):
        found = await mailbox.choose(
            session, letter, stage=Stage.SALES, thread_sender_id=None, now=NOW
        )

    assert found.sender is None
    assert mailbox.RESTING not in found.refusal
    assert found.refusal.startswith("Сегодня писать некому")
