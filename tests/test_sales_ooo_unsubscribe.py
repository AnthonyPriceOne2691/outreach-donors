"""Автоответ и отписка словами в треде продаж — срез 2.3b.

Всё на настоящей базе дерева: сроки писем, стоп-лист, диалог. Модель подставная
(её работа — срез 2.2); автоответ и отписку правилами модель не получает вовсе.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from backend.features.core.domain import MessageStatus, ReplyKind, Stage, ThreadStatus
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.outreach.threads import ThreadState, summarize
from backend.features.replies import outcome
from backend.features.replies.pipeline import Inbox
from backend.features.sales.ooo import return_date
from backend.features.sales.replies import SalesReplies
from backend.features.sales.reply_kind import KindFound, SalesKind
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_sales_reply_routing import (
    HOST,
    NOW,
    FakeClassifier,
    _incoming,
    sales_letter,
    secret,
)

__all__ = ["secret"]  # подпись адреса ответа — фикстура 2.1


async def _answer(
    session: AsyncSession, letter: MessageModel, text: str, **extra: object
) -> ReplyModel:
    got = await Inbox(session, now=NOW).accept(_incoming(letter, text, **extra))
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    reply.created_at = NOW  # время получения — от него считается срок по умолчанию
    return reply


def _sales(
    session: AsyncSession, found: KindFound | None = None
) -> tuple[SalesReplies, FakeClassifier]:
    model = FakeClassifier(found)
    return SalesReplies(session, model, threshold=0.8, now=NOW), model


# --- A3: автоответ «вернусь 14.10» ----------------------------------------------------------


async def test_a3_out_of_office_moves_the_next_step_to_the_return_date(
    session: AsyncSession,
) -> None:
    letter = await sales_letter(session)
    letter.next_action_at = NOW + timedelta(days=2)
    reply = await _answer(
        session,
        letter,
        "Я в отпуске, вернусь 14.10 и сразу отвечу.",
        headers={"Auto-Submitted": "auto-replied"},
    )
    assert reply.kind is ReplyKind.AUTO_REPLY
    sales, model = _sales(session)

    handled = await sales.handle(reply.id)

    await session.flush()
    await session.refresh(letter)
    assert letter.next_action_at == datetime(2026, 10, 14, tzinfo=UTC), "не раньше 14.10"
    assert model.calls == 0, "автоответ модели не отдаётся"
    assert "не раньше 14.10.2026 (дата возвращения из письма)" in str(handled.reason)
    thread = await session.get(ThreadModel, letter.thread_id)
    assert thread is not None
    assert thread.status is ThreadStatus.OPEN, "автоответ цепочку не останавливает"


async def test_a3_without_a_date_the_step_waits_the_default_days(session: AsyncSession) -> None:
    letter = await sales_letter(session)
    letter.next_action_at = NOW + timedelta(days=1)
    reply = await _answer(
        session,
        letter,
        "I am out of the office with limited access to email.",
        headers={"Auto-Submitted": "auto-replied"},
    )
    sales, _ = _sales(session)

    await sales.handle(reply.id)

    await session.flush()
    await session.refresh(letter)
    assert letter.next_action_at == NOW + timedelta(days=7)


async def test_a3_a_later_step_is_not_pulled_earlier(session: AsyncSession) -> None:
    letter = await sales_letter(session)
    later = NOW + timedelta(days=30)
    letter.next_action_at = later
    reply = await _answer(
        session, letter, "Back on 14.10.", headers={"Auto-Submitted": "auto-replied"}
    )
    sales, _ = _sales(session)

    handled = await sales.handle(reply.id)

    await session.flush()
    await session.refresh(letter)
    assert letter.next_action_at == later
    assert "сдвинуто сроков — 0" in str(handled.reason)


def test_a3_out_of_office_goes_to_sales_without_the_model() -> None:
    assert outcome.to_sales_queue(ReplyKind.AUTO_REPLY, Stage.SALES) is True
    assert outcome.to_sales_queue(ReplyKind.AUTO_REPLY, Stage.DONORS) is False
    assert outcome.to_sales_queue(ReplyKind.BOUNCE, Stage.SALES) is False


RECEIVED = date(2026, 10, 6)


@pytest.mark.parametrize(
    ("text", "back"),
    [
        ("Back on 14.10", date(2026, 10, 14)),
        ("Я в отпуске, вернусь 14 октября.", date(2026, 10, 14)),
        ("Out from October 10 to October 14th.", date(2026, 10, 14)),
        ("Ich bin ab dem 14. Oktober wieder im Büro.", date(2026, 10, 14)),
        ("Back 2026-10-14", date(2026, 10, 14)),
        ("Back 14/10/2026", date(2026, 10, 14)),
        ("Back 10/14", date(2026, 10, 14)),
        ("с 10.10.26 по 20.10.26 меня нет", date(2026, 10, 20)),
        ("Back 10/11", None),  # октябрь или ноябрь — не понять
        ("Back at 10.30 tomorrow", None),  # время, а не дата
        ("I was out on 01.10, sorry", None),  # прошлое
        ("Back on 14.10.2027", None),  # дальше окна
        ("Out of the office with limited access.", None),
    ],
)
def test_return_date_is_taken_only_when_it_is_in_the_text(text: str, back: date | None) -> None:
    assert return_date(text, RECEIVED) == back


def test_return_date_rolls_over_the_new_year() -> None:
    assert return_date("Back on 05.01", date(2026, 12, 28)) == date(2027, 1, 5)


# --- A4: «remove me» в треде продаж -------------------------------------------------------


async def _other_direction(
    session: AsyncSession, letter: MessageModel
) -> tuple[MessageModel, MessageModel]:
    """Тот же адрес в рассылке доноров: письмо в очереди и отправленное со сроком."""
    contact = await session.scalar(select(ContactModel).where(ContactModel.id == letter.contact_id))
    assert contact is not None
    donors = CampaignModel(stage=Stage.DONORS, name="Доноры", status="running")
    session.add(donors)
    await session.flush()
    queued = MessageModel(
        campaign_id=donors.id,
        domain_id=letter.domain_id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.QUEUED,
        next_action_at=NOW + timedelta(hours=3),
        idempotency_key=f"donors:{HOST}:0",
    )
    sent = MessageModel(
        campaign_id=donors.id,
        domain_id=letter.domain_id,
        contact_id=contact.id,
        step=1,
        status=MessageStatus.SENT,
        sent_at=NOW - timedelta(days=3),
        next_action_at=NOW + timedelta(days=4),
        idempotency_key=f"donors:{HOST}:1",
    )
    session.add_all([queued, sent])
    await session.flush()
    return queued, sent


async def _suppressions(session: AsyncSession) -> list[tuple[str | None, Stage | None]]:
    rows = await session.execute(select(SuppressionModel.email, SuppressionModel.stage))
    return [tuple(row) for row in rows.all()]  # type: ignore[misc]


async def test_a4_remove_me_closes_the_address_everywhere_and_unschedules(
    session: AsyncSession,
) -> None:
    """«remove me» узнают правила приёма (вид `unsubscribe`): модель не зовётся,
    а модуль продаж снимает назначенное этому адресу во всех направлениях."""
    letter = await sales_letter(session)
    queued, sent = await _other_direction(session, letter)
    reply = await _answer(session, letter, "remove me")
    assert reply.kind is ReplyKind.UNSUBSCRIBE
    sales, model = _sales(session)

    handled = await sales.handle(reply.id)

    await session.flush()
    assert model.calls == 0
    assert await _suppressions(session) == [(f"ceo@{HOST}", None)], "стоп-лист без этапа"
    await session.refresh(queued)
    await session.refresh(sent)
    assert (queued.status, queued.next_action_at) == (MessageStatus.STOPPED, None)
    assert sent.next_action_at is None
    thread = await session.get(ThreadModel, letter.thread_id)
    assert thread is not None
    assert thread.status is ThreadStatus.UNSUBSCRIBED
    assert "адрес закрыт во всех направлениях" in str(handled.reason)


async def test_a4_stop_in_words_the_rules_miss_is_closed_by_the_model_kind(
    session: AsyncSession,
) -> None:
    letter = await sales_letter(session)
    queued, _ = await _other_direction(session, letter)
    reply = await _answer(session, letter, "Please stop sending these emails.")
    assert reply.kind is ReplyKind.HUMAN
    found = KindFound(SalesKind.UNSUBSCRIBE, 0.95, quote="Please stop sending these emails")
    sales, _ = _sales(session, found)

    handled = await sales.handle(reply.id)

    await session.flush()
    assert (handled.route, handled.waits) == ("unsubscribe", False)
    assert await _suppressions(session) == [(f"ceo@{HOST}", None)]
    await session.refresh(queued)
    assert queued.status is MessageStatus.STOPPED
    messages = (
        await session.scalars(
            select(MessageModel).where(MessageModel.thread_id == letter.thread_id)
        )
    ).all()
    assert summarize(messages, [reply], Stage.SALES).state is ThreadState.UNSUBSCRIBED


async def test_a4_unsure_unsubscribe_waits_for_a_human_and_closes_nothing(
    session: AsyncSession,
) -> None:
    letter = await sales_letter(session)
    reply = await _answer(session, letter, "Please stop sending these emails.")
    found = KindFound(SalesKind.UNSUBSCRIBE, 0.5, quote="Please stop sending these emails")
    sales, _ = _sales(session, found)

    handled = await sales.handle(reply.id)

    assert (handled.route, handled.waits) == ("manual", True)
    assert await _suppressions(session) == []


async def test_a4_donors_unchanged_a_donor_answer_never_reaches_sales(
    session: AsyncSession,
) -> None:
    """Для доноров ничего не меняется: их «stop sending» — ответ человека для
    разбора цены, отписку правилами решает приём, модуль продаж не зовётся."""
    letter = await sales_letter(session, Stage.DONORS)
    words = await Inbox(session, now=NOW).accept(
        _incoming(letter, "Please stop sending these emails.")
    )

    assert (words.kind, words.to_sales, words.to_parse) == (ReplyKind.HUMAN, None, words.reply_id)
    assert outcome.to_sales_queue(ReplyKind.UNSUBSCRIBE, Stage.DONORS) is False
    assert await _suppressions(session) == []
