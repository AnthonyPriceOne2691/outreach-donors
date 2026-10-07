"""Этап продаж в общей почте, ответы — срез 1.1b, часть «б».

Ответ лида продаж приходит туда же, куда ответы доноров и рекламодателей.
Почта продажи ещё не ведёт, и ответ не должен стать ни «лидом рекламодателя»,
ни ценой донора: его сохраняют, цепочку останавливают, и он ждёт человека
с причиной словами. Домен лида в этих тестах заодно принятый донор — тот
случай, где донорский разбор положил бы сумму из ответа в его карточку.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import MessageStatus, ReplyKind, Stage, ThreadStatus
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import MessageModel, ReplyModel
from backend.features.core.stages import SALES_NOT_CONNECTED, SalesNotConnectedError
from backend.features.letters import reply_to
from backend.features.outreach.threads import Review, ThreadState, review_of, summarize
from backend.features.replies import outcome
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox, Parser
from backend.features.replies.repository import ReplyRepository
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_sales_stage_mail import LEAD, NOW, admin_token, sales_world

__all__ = ["admin_token"]  # фикстура общая с частью «а»

SECRET = "s" * 32
SALES_BOX = "sales@mail-sales.example.test"
#: Уверенная цена — то, что донорский путь положил бы в карточку донора.
PRICE = Extracted(price_white=Decimal("300"), currency="USD", confidence=0.99)


@pytest.fixture
def inbound_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)


class CountingExtractor:
    """Модель разбора: считает вызовы и возвращает уверенную цену."""

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, incoming: Incoming) -> Extracted:
        self.calls += 1
        return PRICE


async def _donor_price(session: AsyncSession) -> Decimal | None:
    domain_id = await session.scalar(
        select(ContactModel.domain_id).where(ContactModel.email == f"ceo@{LEAD}")
    )
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain_id))
    assert donor is not None
    return donor.last_price


# --- A3: ответ человека в треде продаж ---------------------------------------------------


async def test_a3_answer_is_kept_and_waits_for_a_human_not_as_a_lead_or_a_price(
    session: AsyncSession, inbound_secret: None
) -> None:
    world = await sales_world(session, status=MessageStatus.SENT, due=NOW + timedelta(days=7))
    incoming = Incoming(
        message_id=f"<in-2@{LEAD}>",
        to=(reply_to.address_for(world.letter.id, sender_email=SALES_BOX, secret=SECRET),),
        from_email=f"boss@{LEAD}",
        subject="Re: A question about your team",
        text="We pay $300 a month for this today. Call me tomorrow.",
    )

    got = await Inbox(session, now=NOW).accept(incoming)

    assert (got.kind, got.bound, got.parse_pending) == (ReplyKind.HUMAN, True, False)
    assert (got.needs_review, got.review_reason) == (True, outcome.SALES_WAITING)
    saved = await session.get(ReplyModel, got.reply_id)
    assert saved is not None
    assert (saved.thread_id, saved.message_id) == (world.thread.id, world.letter.id)
    await session.refresh(world.letter)
    await session.refresh(world.thread)
    assert world.letter.next_action_at is None  # ответил — добивок больше нет
    assert world.thread.status is ThreadStatus.REPLIED
    remembered = await session.scalar(
        select(func.count()).select_from(ContactModel).where(ContactModel.email == f"boss@{LEAD}")
    )
    assert remembered == 0  # адрес лида — не контакт донора


async def test_parse_job_that_slipped_through_calls_no_model_and_writes_no_price(
    session: AsyncSession,
) -> None:
    world = await sales_world(session, status=MessageStatus.SENT)
    extractor = CountingExtractor()

    parsed = await Parser(session, extractor, now=NOW).parse(world.reply.id)

    assert extractor.calls == 0
    assert (parsed.skipped, parsed.review_reason) == ("ответ продаж", outcome.SALES_WAITING)
    assert (parsed.needs_review, parsed.stored_price) == (True, False)
    assert await _donor_price(session) is None


async def test_webhook_repeat_does_not_hand_it_to_the_model(session: AsyncSession) -> None:
    world = await sales_world(session, status=MessageStatus.SENT)

    assert not await ReplyRepository(session).parse_never_ran(world.reply)


# --- подтверждение цены и лид --------------------------------------------------------------


async def test_price_confirmation_is_refused_before_any_write(session: AsyncSession) -> None:
    world = await sales_world(session, status=MessageStatus.SENT)

    with pytest.raises(SalesNotConnectedError, match=SALES_NOT_CONNECTED):
        await ReplyRepository(session).confirm(
            world.reply,
            by="operator@sales-stage.example.test",
            price_white=Decimal("300"),
            price_grey=None,
            currency="USD",
            payment_methods=None,
        )

    assert (world.reply.reviewed_at, world.reply.price_white) == (None, None)


async def test_screen_confirmation_gets_409_and_the_donor_card_stays_empty(
    session: AsyncSession, client: AsyncClient, admin_token: str
) -> None:
    world = await sales_world(session, status=MessageStatus.SENT)
    await session.commit()

    response = await client.patch(
        f"/api/replies/{world.reply.id}",
        json={"price_white": "300", "currency": "USD"},
        headers=bearer(admin_token),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        f"Цена из ответа №{world.reply.id} не подтверждена: {SALES_NOT_CONNECTED}"
    )
    assert await _donor_price(session) is None


async def test_sales_answer_is_not_taken_as_an_advertiser_lead(
    session: AsyncSession, client: AsyncClient, admin_token: str
) -> None:
    world = await sales_world(session, status=MessageStatus.SENT)
    await session.commit()

    with pytest.raises(SalesNotConnectedError, match=f"Ответ №{world.reply.id} лидом не взят"):
        await ReplyRepository(session).take_lead(
            world.reply, by="operator@sales-stage.example.test"
        )
    response = await client.post(f"/api/replies/{world.reply.id}/lead", headers=bearer(admin_token))

    assert response.status_code == 409
    assert SALES_NOT_CONNECTED in response.json()["detail"]
    assert world.reply.reviewed_at is None


# --- правила без базы ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        (
            ReplyKind.HUMAN,
            outcome.Consequences(
                stop_chain=True, needs_review=True, review_reason=outcome.SALES_WAITING
            ),
        ),
        (ReplyKind.AUTO_REPLY, outcome.Consequences()),
        (ReplyKind.BOUNCE, outcome.Consequences(stop_chain=True, mark_contact_dead=True)),
        (ReplyKind.UNSUBSCRIBE, outcome.Consequences(stop_chain=True, suppress_email=True)),
    ],
    ids=["human", "auto-reply-with-sum", "bounce", "unsubscribe"],
)
def test_consequences_of_a_sales_answer(kind: ReplyKind, expected: outcome.Consequences) -> None:
    """Уверенная цена в разборе и сумма в автоответе — а цены в базу нет."""
    assert outcome.decide(kind, PRICE, stage=Stage.SALES, names_a_sum=True) == expected


@pytest.mark.parametrize(
    ("stage", "priced"),
    [(Stage.DONORS, True), (Stage.ADVERTISERS, False), (Stage.SALES, False), (None, False)],
)
def test_only_donor_answers_go_to_the_price_model(stage: Stage | None, priced: bool) -> None:
    assert outcome.priced_by_model(stage) is priced


def _sent() -> MessageModel:
    return MessageModel(status=MessageStatus.SENT, sent_at=NOW - timedelta(days=1), step=0)


def _reply(kind: ReplyKind, *, taken: bool = False) -> ReplyModel:
    return ReplyModel(
        kind=kind, raw_body="We pay $300.", created_at=NOW, reviewed_at=NOW if taken else None
    )


@pytest.mark.parametrize(
    ("replies", "state"),
    [
        ([], ThreadState.WAITING),
        ([_reply(ReplyKind.AUTO_REPLY)], ThreadState.WAITING),
        ([_reply(ReplyKind.HUMAN)], ThreadState.SALES_PENDING),
        ([_reply(ReplyKind.HUMAN, taken=True)], ThreadState.REPLIED),
        ([_reply(ReplyKind.HUMAN), _reply(ReplyKind.UNSUBSCRIBE)], ThreadState.UNSUBSCRIBED),
    ],
    ids=["silence", "auto-reply", "human", "taken", "unsubscribed"],
)
def test_sales_thread_has_its_own_state_not_a_price_or_a_lead(
    replies: list[ReplyModel], state: ThreadState
) -> None:
    assert summarize([_sent()], replies, Stage.SALES).state is state


def test_card_says_why_a_sales_answer_waits() -> None:
    assert review_of(_reply(ReplyKind.HUMAN), Stage.SALES) == Review(
        waiting=True, reason=outcome.SALES_WAITING
    )
    assert review_of(_reply(ReplyKind.AUTO_REPLY), Stage.SALES) == Review(waiting=False)
