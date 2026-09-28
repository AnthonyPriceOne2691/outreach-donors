"""Автоответ с суммой в валюте ждёт человека.

Письмо с заголовками автомата остаётся автоответом, даже если в нём цена:
заголовок — факт, а не слово, и цепочка добивок после него идёт, как шла.
Но автоответы модель не разбирает, и цена из прайса тикет-системы или
отпускной подписи пропадала молча. Теперь такой ответ на письмо донору
ждёт человека тем же путём, что неуверенный разбор: «ждёт разбора»
выводится из сохранённого текста, а не хранится отдельным полем.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.api.threads.schemas import IncomingCard
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    ReplyKind,
    Stage,
    UserRole,
)
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters import reply_to
from backend.features.outreach.repository import OutreachRepository
from backend.features.outreach.threads import ThreadState
from backend.features.replies import outcome
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox
from backend.features.replies.repository import ReplyRepository
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
SECRET = "s" * 32
HOST = "travelnotes.co.uk"
AUTO = {"Auto-Submitted": "auto-replied"}
PRICED = "Thank you for your email. I am on vacation until Monday. Our sponsored post rate is $150."
PLAIN = "Thank you for your email. I am on vacation until Monday."

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


# --- правило без базы --------------------------------------------------------


def test_priced_auto_reply_waits_but_keeps_the_chain() -> None:
    got = outcome.decide(ReplyKind.AUTO_REPLY, stage=Stage.DONORS, names_a_sum=True)

    assert got.needs_review
    assert got.review_reason == outcome.AUTO_REPLY_WITH_SUM
    assert not got.stop_chain, "автоответ цепочку не обрывает — с ценой или без"
    assert not got.remember_answering_address


def test_plain_auto_reply_is_still_silence() -> None:
    assert outcome.decide(ReplyKind.AUTO_REPLY, stage=Stage.DONORS) == outcome.Consequences()


def test_advertisers_sum_is_their_spend_not_a_price() -> None:
    """У рекламодателя сумма — его расход, а не цена площадки."""
    got = outcome.decide(ReplyKind.AUTO_REPLY, stage=Stage.ADVERTISERS, names_a_sum=True)

    assert not got.needs_review


@pytest.mark.parametrize(
    ("text", "want"),
    [
        (PRICED, True),
        (PLAIN, False),
        # Сумма только в цитате — это наше письмо, а не слова донора.
        ("I am on vacation until Monday.\n\n> Our budget is $150 per article.", False),
        ("Ich bin im Urlaub. Gastartikel: 120 €.", True),
    ],
)
def test_sum_is_counted_in_what_the_donor_wrote(text: str, want: bool) -> None:
    assert outcome.names_a_sum(text) is want


def test_waiting_is_computed_for_a_priced_auto_reply() -> None:
    assert outcome.waiting_for_review(ReplyKind.AUTO_REPLY, None, reviewed=False, names_a_sum=True)
    assert not outcome.waiting_for_review(
        ReplyKind.AUTO_REPLY, None, reviewed=True, names_a_sum=True
    )
    assert not outcome.waiting_for_review(ReplyKind.AUTO_REPLY, None, reviewed=False)


# --- приём и экран на настоящей базе ------------------------------------------


@pytest.fixture(autouse=True)
def inbound_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)


async def _sent(session: AsyncSession, stage: Stage = Stage.DONORS) -> MessageModel:
    domain = DomainModel(host=HOST)
    campaign = CampaignModel(stage=stage, name="Проверка", status="running")
    session.add_all([domain, campaign])
    await session.flush()
    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=40))
    contact = ContactModel(domain_id=domain.id, email=f"editor@{HOST}", source=ContactSource.PAGE)
    session.add(contact)
    await session.flush()
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact.id)
    session.add(thread)
    await session.flush()
    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact.id,
        step=0,
        status=MessageStatus.SENT,
        subject="Advertising rates",
        body="Good afternoon,",
        sent_at=NOW,
        # Срок добивки: автоответ его не гасит.
        next_action_at=NOW + timedelta(days=7),
        internet_message_id="<ours-1@mail.test>",
        idempotency_key=f"{stage.value}:{HOST}:0",
    )
    session.add(message)
    await session.commit()
    return message


def _auto_reply(message: MessageModel, text: str) -> Incoming:
    return Incoming(
        message_id="<auto-1@travelnotes.co.uk>",
        to=(reply_to.address_for(message.id, sender_email="anna@mail.test", secret=SECRET),),
        from_email=f"editor@{HOST}",
        subject="Re: Advertising rates",
        text=text,
        headers=AUTO,
    )


async def test_priced_auto_reply_is_taken_for_a_human(session: AsyncSession) -> None:
    sent = await _sent(session)

    got = await Inbox(session, now=NOW).accept(_auto_reply(sent, PRICED))
    await session.flush()

    assert got.kind is ReplyKind.AUTO_REPLY
    assert got.needs_review
    assert got.review_reason == outcome.AUTO_REPLY_WITH_SUM
    assert not got.parse_pending, "модель автоответы не разбирает — цену смотрит человек"
    assert sent.next_action_at is not None, "добивка после автоответа уходит, как шла"


async def test_thread_and_card_show_it_waiting(session: AsyncSession) -> None:
    """Список диалогов и карточка ответа считают одно и то же — одним правилом."""
    sent = await _sent(session)
    await Inbox(session, now=NOW).accept(_auto_reply(sent, PRICED))
    await session.flush()
    assert sent.thread_id is not None

    detail = await OutreachRepository(session).thread(sent.thread_id)
    card = IncomingCard.of(detail.replies[0], detail.row.stage)

    assert detail.row.summary.state is ThreadState.NEEDS_REVIEW
    assert card.needs_review
    assert card.review_reason == outcome.AUTO_REPLY_WITH_SUM


async def test_price_confirmed_by_a_human_ends_the_wait(session: AsyncSession) -> None:
    sent = await _sent(session)
    got = await Inbox(session, now=NOW).accept(_auto_reply(sent, PRICED))
    await session.flush()
    repository = ReplyRepository(session)
    reply = await repository.reply(got.reply_id or 0)

    await repository.confirm(
        reply,
        by="оператор@site.com",
        price_white=Decimal("150"),
        price_grey=None,
        currency="USD",
        payment_methods=None,
        now=NOW,
    )
    await session.flush()
    assert sent.thread_id is not None
    detail = await OutreachRepository(session).thread(sent.thread_id)

    assert detail.row.summary.state is ThreadState.PRICED
    assert not IncomingCard.of(detail.replies[0], detail.row.stage).needs_review


async def test_plain_auto_reply_does_not_wait(session: AsyncSession) -> None:
    sent = await _sent(session)

    got = await Inbox(session, now=NOW).accept(_auto_reply(sent, PLAIN))
    await session.flush()
    assert sent.thread_id is not None
    detail = await OutreachRepository(session).thread(sent.thread_id)

    assert not got.needs_review
    assert detail.row.summary.state is ThreadState.WAITING
    assert IncomingCard.of(detail.replies[0], detail.row.stage).review_reason is None


async def test_advertiser_auto_reply_with_a_sum_does_not_wait(session: AsyncSession) -> None:
    sent = await _sent(session, Stage.ADVERTISERS)

    got = await Inbox(session, now=NOW).accept(_auto_reply(sent, PRICED))
    await session.flush()
    reply = await session.get(ReplyModel, got.reply_id or 0)
    assert reply is not None

    assert not got.needs_review
    assert not IncomingCard.of(reply, Stage.ADVERTISERS).needs_review


async def test_screen_gives_the_reason(
    session: AsyncSession, client: AsyncClient, make_user: MakeUser, sign_in: SignIn
) -> None:
    """Карточка диалога по HTTP: причина словами сервера — по ней экран
    даёт форму цены (`frontend/src/threads/ThreadPage.tsx`)."""
    sent = await _sent(session)
    await Inbox(session, now=NOW).accept(_auto_reply(sent, PRICED))
    await session.commit()
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    token = await sign_in("оператор@site.com")

    response = await client.get(f"/api/threads/{sent.thread_id}", headers=bearer(token))

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["card"]["state"] == "needs_review"
    assert body["incoming"][0]["kind"] == "auto_reply"
    assert body["incoming"][0]["needs_review"]
    assert body["incoming"][0]["review_reason"] == outcome.AUTO_REPLY_WITH_SUM
