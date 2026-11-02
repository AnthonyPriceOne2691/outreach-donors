"""Причина на стороне получателя — не вина ящика (02.11.2026).

Хостинг получателей лёг: сервера имён замолчали, и платформа за минуты отказала двум десяткам писем
текстом «unable to get mx info». Вид «нет почтового сервера» звал человека и считался против ящика:
три ящика встали на паузу, лимит урезан у всех. Здесь — настоящий текст настоящими путями: вебхук
с подписью платформы, журнал здоровья, окно отказов, адрес письма, тревога о висящем письме.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from backend.features.contacts.preference import DEAD
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import MessageModel, SenderModel
from backend.features.core.stages import MailPolicy
from backend.features.letters.delivery_trouble import (
    NOT_THE_BOX,
    Trouble,
    reason_of,
    refused_kind,
)
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.ops.silence import alarms
from backend.features.outreach import health
from backend.features.outreach.health import SoftSignals
from backend.features.outreach.repository import EVERY_STAGE
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_delivery_events import _keypair
from tests.test_delivery_events_repeat import _deliver, _event, _letter
from tests.test_sales_soft_signals import NOW, _box, _journal
from tests.test_sales_stage_bridge import FakeSalesMail
from tests.test_silence_watchdog import _letter as _hanging
from tests.thread_letters import conversation

#: Ответ платформы в тот день — как он пришёл.
OUTAGE = (
    "unable to get mx info: failed to get IPs from PTR record: lookup <nil>: unrecognized address"
)
TIMEOUT = "error dialing remote address: dial tcp 192.0.2.10:25: i/o timeout"
NO_USER = "550 5.1.1 The email account that you tried to reach does not exist"
EVENT_TIME = NOW - timedelta(hours=1)


@pytest.fixture(autouse=True)
def ruled(monkeypatch: pytest.MonkeyPatch) -> FakeSalesMail:
    """Модуль продаж подключён: мягкие сигналы и окно — порогами по умолчанию."""
    monkeypatch.setattr(stages._SALES, "load", None)
    found = FakeSalesMail(rules=MailPolicy(soft=SoftSignals(), watch=True))
    stages.register_sales(lambda: found)
    return found


@pytest.fixture
def signed(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Подпись платформы — своей парой ключей."""
    private, public = _keypair()
    monkeypatch.setattr("backend.config.outreach.EVENTS_PUBLIC_KEY", public)
    return private


def _said(letter: int, kind: str, reason: str, *, soft: bool = False) -> DeliveryEvent:
    return DeliveryEvent(kind, letter, "x@to.example.test", reason=reason, soft=soft, at=EVENT_TIME)


async def _refused(session: AsyncSession, box: SenderModel, *reasons: str) -> None:
    """Последние письма ящика — отказы с этими причинами: старый след в окне."""
    rows = await session.scalars(
        select(MessageModel)
        .where(MessageModel.sender_id == box.id)
        .order_by(MessageModel.sent_at.desc())
        .limit(len(reasons))
    )
    for letter, reason in zip(rows.all(), reasons, strict=True):
        letter.status, letter.failure_reason = MessageStatus.BOUNCED, reason
    await session.flush()


async def _fresh(session: AsyncSession, box: SenderModel) -> int:
    """Последнее дошедшее письмо ящика: отказ по нему — ещё один, а не повтор старого."""
    found = await session.scalar(
        select(MessageModel.id)
        .where(MessageModel.sender_id == box.id, MessageModel.status == MessageStatus.DELIVERED)
        .order_by(MessageModel.sent_at.desc())
        .limit(1)
    )
    assert found is not None
    return int(found)


# --- вид причины ----------------------------------------------------------------------------


def test_the_outage_text_is_silence_and_not_a_missing_server() -> None:
    said = reason_of(OUTAGE)

    assert said.kind is Trouble.RECIPIENT_DNS
    assert said.words == "DNS домена получателя не отвечает"
    assert said.text == OUTAGE


def test_only_the_recipient_side_is_not_the_box() -> None:
    assert {Trouble.RECIPIENT_DNS, Trouble.UNREACHABLE} == NOT_THE_BOX


@pytest.mark.parametrize(
    "text",
    [
        "550 5.1.10 RESOLVER.ADR.RecipientNotFound; Recipient not found by SMTP address lookup",
        "550 5.7.1 Sender address rejected: Domain not found",
        "450 4.7.25 Client host rejected: cannot find your reverse hostname, DNS timed out",
        "dial tcp 192.0.2.10:25: connect: connection timed out",
    ],
)
def test_a_lookup_of_someone_else_is_not_recipient_dns(text: str) -> None:
    """Поиск ящика, наш адрес и обрыв соединения — не молчание DNS получателя."""
    assert reason_of(text).kind is not Trouble.RECIPIENT_DNS


@pytest.mark.parametrize(
    ("failure", "kind", "said", "found"),
    [
        (OUTAGE, None, None, Trouble.RECIPIENT_DNS),
        # Вид лёг по прежней таблице, текст тот же: платформа сдалась, не назвав причины.
        ("отказ доставки", "no_mail_server", OUTAGE, Trouble.RECIPIENT_DNS),
        ("550 4.4.7 QUEUE.Expired; message expired", "unreachable", TIMEOUT, Trouble.UNREACHABLE),
        # Текста отсрочки нет — записанный вид.
        ("retry timeout exceeded", "unreachable", None, Trouble.UNREACHABLE),
        # Вид переименовали — вид самого отказа.
        ("отказ доставки", "renamed_kind", None, Trouble.UNKNOWN),
        # Отказ назвал причину сам: отсрочки её не отменяют.
        (NO_USER, "unreachable", TIMEOUT, Trouble.NO_MAILBOX),
    ],
)
def test_refused_kind_is_the_letter_text_and_then_the_last_deferral(
    failure: str, kind: str | None, said: str | None, found: Trouble
) -> None:
    assert refused_kind(failure, deferred_kind=kind, deferred_reason=said) is found


# --- мягкие сигналы -------------------------------------------------------------------------


@pytest.mark.parametrize("reason", [OUTAGE, TIMEOUT])
async def test_a_refusal_on_the_recipient_side_leaves_no_signal(
    session: AsyncSession, reason: str
) -> None:
    """Одно письмо: три отсрочки и мягкий отказ. Раньше оно одно резало лимит ящика вдвое."""
    box = await _box(session, Stage.SALES, total=1)
    letter = await _letter(session, box)

    await apply_events(session, [_said(letter, "deferred", reason) for _ in range(3)], now=NOW)
    await apply_events(session, [_said(letter, "bounce", reason, soft=True)], now=NOW)

    assert await _journal(session, box) == []
    assert await health.cuts(session, Stage.SALES, NOW) == {}


async def test_a_refusal_about_us_is_still_a_signal(session: AsyncSession) -> None:
    box = await _box(session, Stage.SALES, total=1)
    letter = await _letter(session, box)
    greylisted = "451 4.7.1 Greylisting in action, please come back later"

    await apply_events(session, [_said(letter, "deferred", greylisted) for _ in range(3)], now=NOW)

    assert await _journal(session, box) == ["deferred", "deferred", "deferred", "limit_cut"]


async def test_a_code_alone_names_the_recipient_side(session: AsyncSession) -> None:
    """Отсрочка без текста, с кодом события: вид — по коду, сигнала нет."""
    box = await _box(session, Stage.SALES, total=1)
    letter = await _letter(session, box)
    silent = DeliveryEvent("deferred", letter, "x@to.example.test", at=EVENT_TIME, status="4.4.3")

    await apply_events(session, [silent], now=NOW)

    assert await _journal(session, box) == []


# --- окно отказов ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", [OUTAGE, TIMEOUT])
async def test_three_recipient_side_refusals_do_not_pause_the_box(
    session: AsyncSession, reason: str
) -> None:
    box = await _box(session, Stage.SALES, total=20)
    await _refused(session, box, reason, reason)
    letter = await _fresh(session, box)

    report = await apply_events(session, [_said(letter, "bounce", reason, soft=True)], now=NOW)

    assert (report.paused_domains, box.enabled) == ([], True)


async def test_letters_refused_before_the_fix_do_not_count_either(session: AsyncSession) -> None:
    """Вид в письме — от прежней таблицы, а судит текст: без правки данных и без миграции."""
    box = await _box(session, Stage.SALES, total=20)
    await _refused(session, box, OUTAGE, OUTAGE, NO_USER)
    rows = await session.scalars(select(MessageModel).where(MessageModel.failure_reason == OUTAGE))
    for letter in rows.all():
        letter.deferred_kind, letter.deferred_reason = "no_mail_server", OUTAGE
    await session.flush()
    fresh = await _fresh(session, box)

    report = await apply_events(session, [_said(fresh, "bounce", OUTAGE, soft=True)], now=NOW)

    assert (report.paused_domains, box.enabled) == ([], True)


async def test_refusals_about_the_box_are_counted_past_the_outage(session: AsyncSession) -> None:
    """Контроль: сбой не прячет настоящие отказы — третий настоящий ставит паузу, как раньше."""
    box = await _box(session, Stage.SALES, total=20)
    await _refused(session, box, OUTAGE, OUTAGE, NO_USER, NO_USER)
    letter = await _fresh(session, box)

    report = await apply_events(session, [_said(letter, "bounce", NO_USER)], now=NOW)

    assert report.paused_domains == [box.email]
    assert box.pause_reason == "отказов 3 в окне 50 писем — порог 5.0%: пауза"


async def test_the_whole_way_through_the_webhook(
    client: AsyncClient, session: AsyncSession, signed: Any
) -> None:
    """Пачка, как её шлёт платформа: две отсрочки и отказ `blocked` — на трёх письмах подряд."""
    box = await _box(session, Stage.SALES, total=20)
    letters = (
        await session.scalars(
            select(MessageModel.id)
            .where(MessageModel.sender_id == box.id)
            .order_by(MessageModel.sent_at.desc())
            .limit(3)
        )
    ).all()
    await session.commit()
    batch = [
        row
        for number, letter in enumerate(letters)
        for row in (
            _event("deferred", letter, f"sg-{number}-1", response=OUTAGE, attempt="1"),
            _event("deferred", letter, f"sg-{number}-2", response=OUTAGE, attempt="2"),
            _event("bounce", letter, f"sg-{number}-3", reason=OUTAGE, type="blocked"),
        )
    ]

    answer = await _deliver(client, signed, batch)

    assert answer.status_code == 200, answer.text
    assert (answer.json()["bounced"], answer.json()["paused"]) == (3, [])
    await session.refresh(box)
    assert box.enabled
    assert await _journal(session, box) == []
    assert await health.cuts(session, Stage.SALES, NOW) == {}


# --- адрес ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "alive"), [(OUTAGE, True), (NO_USER, False)], ids=["молчание DNS", "адреса нет"]
)
async def test_silence_does_not_bury_the_address_even_as_a_hard_bounce(
    session: AsyncSession, filled_legal: None, reason: str, alive: bool
) -> None:
    """Платформа бывает называет молчание обычным отказом, без `type: blocked`: адрес ни при чём.
    Контроль — настоящий «адреса нет»: хоронит, как раньше."""
    talk = await conversation(session, sent_at=NOW - timedelta(hours=2))
    letter = talk.first
    contact = await session.get(ContactModel, letter.contact_id)
    assert contact is not None
    email = contact.email

    await apply_events(session, [DeliveryEvent("bounce", letter.id, email, reason=reason)], now=NOW)

    assert letter.status is MessageStatus.BOUNCED
    assert (contact.verification_status != DEAD) is alive


# --- тревога о висящем письме ---------------------------------------------------------------


async def test_a_letter_hanging_on_silence_is_no_cause(session: AsyncSession) -> None:
    """Молчание пройдёт само: ни «нет почтового сервера», ни «писать на другой адрес»."""
    stuck = await _hanging(session, status=MessageStatus.SENT, sent_at=NOW - timedelta(hours=8))
    stuck.deferred_since = stuck.deferred_at = NOW - timedelta(hours=7)
    stuck.deferred_attempts, stuck.deferred_reason = 4, OUTAGE
    stuck.deferred_kind = Trouble.RECIPIENT_DNS.value
    later = await _hanging(
        session, status=MessageStatus.DELIVERED, sent_at=NOW - timedelta(hours=7), number=1
    )
    later.delivered_at = NOW - timedelta(hours=7)
    await session.flush()

    found = await alarms(session, stages=EVERY_STAGE, now=NOW)
    alarm = next(alarm for alarm in found if alarm.code == "delivery-pending:donors")

    assert alarm.concern is not None
    assert not alarm.concern.cause
    assert "сторона получателя тянет" in alarm.concern.words
    assert "DNS домена получателя не отвечает" in alarm.detail
    assert "нет почтового сервера" not in alarm.detail
