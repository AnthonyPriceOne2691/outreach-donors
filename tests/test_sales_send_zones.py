"""Окно получателя и передача лида — с настоящим модулем продаж (4.6b поверх 4.3 и 5.3).

Модуль продаж отвечает мосту почты политикой (`mail.policy` → окно из настроек продаж) и
поясами получателя (`Recipient.zones`: лида, его страны). Подставной модуль окна —
`test_sales_send_window.py`; здесь — что настоящий модуль отдаёт их почте: письмо продаж вне
окна ждёт, добивка уходит с открытием окна плюс сдвиг, лид без пояса ждёт со словами. Лид,
переданный телемаркетологу точкой входа 5.3, писем больше не получает: модуль спрашивает шов
`handoff.handed_off`. Мир — подключённые продажи (`test_sales_send_world.py`) с окном продаж
по умолчанию: пн–пт 9–17 по часам получателя.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest
from backend.config import sales as sales_cfg
from backend.features.core import stages, window
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.outreach import MessageModel
from backend.features.core.window import SendWindow
from backend.features.letters import followups
from backend.features.letters.sending import OutsideWindowError, Sending, UnknownZoneError
from backend.features.sales import handoff, queue
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_sales_send import JANE, OLGA, _seen, _transports

BERLIN = ZoneInfo("Europe/Berlin")
#: Понедельник 14:00 в Берлине, 08:00 в Нью-Йорке.
MONDAY_NOON = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
#: Суббота 11:38 в Берлине — срок первой добивки письма, ушедшего в среду (`w.NOW`).
SATURDAY = w.NOW + timedelta(days=3, minutes=1)
#: Понедельник после неё, 09:00 в Берлине — открытие окна.
MONDAY_NINE = datetime(2026, 10, 19, 9, 0, tzinfo=BERLIN).astimezone(UTC)
WEEKDAYS_9_17 = SendWindow(days=frozenset(range(5)), start=time(9), end=time(17))


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    """Подключённые продажи с окном по умолчанию и сдвигом с зерном."""
    found = await w.world(session, monkeypatch)
    monkeypatch.setattr(sales_cfg, "SEND_DAYS", WEEKDAYS_9_17.days)
    monkeypatch.setattr(sales_cfg, "SEND_OPENS", WEEKDAYS_9_17.start)
    monkeypatch.setattr(sales_cfg, "SEND_CLOSES", WEEKDAYS_9_17.end)
    monkeypatch.setattr(sales_cfg, "SEND_SPREAD_MIN", 30)
    monkeypatch.setattr(window, "JITTER", random.Random(5))
    return found


async def _letters(session: AsyncSession, world: w.World, **lead: object) -> list[MessageModel]:
    """Лиды гипотезы (у Jane — поля `lead`) и их первые письма — сборкой продаж."""
    await w.lead(session, world.hypothesis_id, JANE, name="Jane", **lead)
    if not lead:
        await w.lead(session, world.hypothesis_id, OLGA, name="Olga")
    await queue.build(session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=10)
    return list(await session.scalars(select(MessageModel).order_by(MessageModel.id)))


async def test_policy_of_sales_comes_from_the_settings_through_the_bridge(
    session: AsyncSession, world: w.World
) -> None:
    """Мутант «модуль отдаёт нынешнюю политику»: окна, мягких сигналов и сторожа не было бы."""
    found = await stages.mail_policy(session, Stage.SALES, "Письмо продаж")

    assert found.window == WEEKDAYS_9_17
    assert found.soft is not None
    assert found.soft.complaints == sales_cfg.COMPLAINT_PAUSE
    assert found.watch is True


@pytest.mark.parametrize(
    ("lead", "goes"),
    [
        ({"timezone": "Europe/Berlin", "country": None}, True),
        ({"timezone": None, "country": "de"}, True),
        ({"timezone": "America/New_York", "country": "de"}, False),
    ],
    ids=["пояс лида", "пояс страны", "пояс лида раньше страны"],
)
async def test_window_counts_the_zone_of_the_lead_then_of_his_country(
    session: AsyncSession, world: w.World, lead: dict[str, str | None], goes: bool
) -> None:
    """Понедельник 14:00 в Берлине — окно открыто; в Нью-Йорке 08:00 — ещё закрыто."""
    [letter] = await _letters(session, world, **lead)
    source = _transports()
    sending = Sending(session, source, now=MONDAY_NOON)

    if goes:
        await sending.send(letter.id)
    else:
        with pytest.raises(OutsideWindowError, match="уйдёт не раньше пн 12.10 09:"):
            await sending.send(letter.id)

    assert [outgoing.to for outgoing in _seen(source)] == ([JANE] if goes else [])


async def test_lead_without_a_zone_waits_and_the_reason_is_said(
    session: AsyncSession, world: w.World
) -> None:
    [letter] = await _letters(session, world, timezone=None, country=None)
    source = _transports()

    with pytest.raises(UnknownZoneError, match="пояс получателя неизвестен"):
        await Sending(session, source, now=MONDAY_NOON).send(letter.id)

    assert _seen(source) == []
    await session.refresh(letter)
    assert (letter.status, letter.sender_id) == (MessageStatus.QUEUED, None)


async def test_first_letter_on_saturday_waits_in_the_queue(
    session: AsyncSession, world: w.World
) -> None:
    [letter, _] = await _letters(session, world)
    source = _transports()

    with pytest.raises(OutsideWindowError) as late:
        await Sending(session, source, now=SATURDAY).send(letter.id)

    assert "вне окна получателя (пн–пт 09:00–17:00 по его часам)" in str(late.value)
    assert "уйдёт не раньше пн 19.10 09:" in str(late.value)
    assert _seen(source) == []
    await session.refresh(letter)
    assert (letter.status, letter.sender_id) == (MessageStatus.QUEUED, None)


async def test_followup_due_on_saturday_goes_at_the_opening_plus_shift(
    session: AsyncSession, world: w.World
) -> None:
    """Добивка вне окна ждёт его открытия плюс сдвиг, а не часа: в выходные — до понедельника."""
    [first, _] = await _letters(session, world)
    source = _transports()
    await Sending(session, source, now=w.NOW).send(first.id)  # среда, 11:37 в Берлине

    report = await followups.send_due(session, transport=source, limit=5, now=SATURDAY)

    assert (report.sent, report.postponed) == (0, 1)
    await session.refresh(first)
    shift = timedelta(minutes=30) * random.Random(5).random()
    assert first.next_action_at == MONDAY_NINE + shift
    later = await followups.send_due(
        session, transport=source, limit=5, now=MONDAY_NINE + timedelta(minutes=30)
    )
    assert later.sent == 1
    assert _seen(source)[-1].in_reply_to == first.internet_message_id


async def test_lead_handed_off_by_the_handoff_entry_gets_no_more_letters(
    session: AsyncSession, world: w.World
) -> None:
    """Шов 5.3: передачу заводит её точка входа по диалогу продаж (лид — по связи
    `sales_threads`), добивка переданному не уходит, у второго лида компании — уходит."""
    jane, olga = await _letters(session, world)
    source = _transports()
    for letter in (jane, olga):
        await Sending(session, source, now=MONDAY_NOON).send(letter.id)
    assert jane.thread_id is not None

    await handoff.start(session, jane.thread_id, enqueue=lambda _id: None)
    report = await followups.send_due(
        session, transport=source, limit=5, now=MONDAY_NOON + timedelta(days=3, minutes=1)
    )

    assert (report.sent, report.stopped) == (1, 1)
    assert [outgoing.to for outgoing in _seen(source)] == [JANE, OLGA, OLGA]
    await session.refresh(jane)
    assert jane.next_action_at is None
