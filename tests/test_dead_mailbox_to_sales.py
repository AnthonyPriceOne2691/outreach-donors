"""Автоответ «мёртвый ящик» на письмо продаж уходит очереди продаж (просьба продаж, 12.10.2026).

По виду это отказ доставки, и продажам он не доходил. А «я ушёл, пишите jane@» — новый лид:
адрес в тексте ответа, и заводит его модуль продаж, мост для этого не нужен. В общие контакты
домена адрес из такого автоответа не ложится (`Inbox._redirect`). Одно правило у приёма и у
повтора вебхука (`outcome.to_sales_queue`). На письмо донора — как раньше: не очередь продаж,
адрес из автоответа — в контакты донора. Модуль продаж здесь подставной.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.contacts.preference import DEAD
from backend.features.core import stages
from backend.features.core.domain import MessageStatus, ReplyKind
from backend.features.core.models.donor import ContactModel
from backend.features.letters import reply_to
from backend.features.replies import classify, redirect
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_delivery_events_foreign import LeadMail
from tests.test_sales_stage_bridge import LEAD_EMAIL
from tests.test_sales_stage_mail import LEAD, World, sales_world
from tests.thread_letters import BOX as DONOR_BOX
from tests.thread_letters import HOST, conversation

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
SECRET = "m" * 32
#: Ящик мира продаж (`sales_world`): с него ушло письмо, на него — метка ответа.
SALES_BOX = "sales@mail-sales.example.test"


@pytest.fixture(autouse=True)
def inbound_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> World:
    """Письмо продаж ушло лиду `LEAD_EMAIL`; его адреса среди контактов сайта нет."""
    found = await sales_world(session, status=MessageStatus.SENT)
    monkeypatch.setattr(stages._SALES, "load", None)
    stages.register_sales(lambda: LeadMail(threads=(found.thread.id,)))
    return found


def _dead_mailbox(letter_id: int, box: str, sender: str, named: str) -> Incoming:
    """Автоответ «ящик больше не читается, пишите на `named`» на письмо `letter_id`. Метка —
    действующим секретом: `filled_legal` ставит свой."""
    label = reply_to.address_for(letter_id, sender_email=box, secret=outreach_cfg.INBOUND_SECRET)
    return Incoming(
        message_id=f"<dead-{letter_id}@{sender.partition('@')[2]}>",
        to=(label,),
        from_email=sender,
        subject="Automatic reply",
        text=f"This mailbox is no longer monitored. Please write to {named}.",
        headers={"Auto-Submitted": "auto-replied"},
    )


async def _contacts(session: AsyncSession, domain_id: int) -> list[tuple[str, str | None]]:
    rows = await session.execute(
        select(ContactModel.email, ContactModel.verification_status)
        .where(ContactModel.domain_id == domain_id)
        .order_by(ContactModel.id)
    )
    return list(rows.tuples().all())


async def test_a_dead_mailbox_reply_to_a_sales_letter_goes_to_the_sales_queue(
    session: AsyncSession, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(*_args: object, **_kwargs: object) -> redirect.Redirect:
        raise AssertionError("адреса из автоответа на письмо продаж не берутся")

    monkeypatch.setattr(redirect, "follow", never)
    before = await _contacts(session, world.letter.domain_id)

    got = await Inbox(session, now=NOW).accept(
        _dead_mailbox(world.letter.id, SALES_BOX, LEAD_EMAIL, f"editor@{LEAD}")
    )
    await session.flush()

    assert (got.kind, got.rule, got.bound) == (ReplyKind.BOUNCE, classify.DEAD_MAILBOX, True)
    assert got.sales_pending
    assert got.to_sales == got.reply_id
    assert got.forwarding == redirect.Redirect()
    assert await _contacts(session, world.letter.domain_id) == before


async def test_a_repeated_webhook_hands_it_to_the_sales_queue_again(
    session: AsyncSession, world: World
) -> None:
    """Очередь лежала в минуту вебхука: повтор застаёт ответ без разбора вида и отдаёт его
    продажам снова — тем же правилом, что приём."""
    incoming = _dead_mailbox(world.letter.id, SALES_BOX, LEAD_EMAIL, f"editor@{LEAD}")
    first = await Inbox(session, now=NOW).accept(incoming)
    await session.flush()

    again = await Inbox(session, now=NOW).accept(incoming)

    assert again.duplicate
    assert first.reply_id is not None
    assert again.to_sales == first.reply_id


async def test_a_dead_mailbox_reply_to_a_donor_letter_is_as_before(
    session: AsyncSession, filled_legal: None
) -> None:
    """Письмо донора: не очередь продаж, адрес мёртвого ящика похоронен, названные адреса
    разобраны, как раньше (`redirect.follow`). У сайта `.test` корня в списке суффиксов нет,
    поэтому названный адрес ждёт человека, а не ложится в контакты — так было и до правки."""
    talk = await conversation(session, sent_at=NOW - timedelta(days=1))
    dead = f"editor@{HOST}"

    got = await Inbox(session, now=NOW).accept(
        _dead_mailbox(talk.first.id, DONOR_BOX, dead, f"desk@{HOST}")
    )
    await session.flush()

    assert (got.rule, got.sales_pending, got.to_sales) == (classify.DEAD_MAILBOX, False, None)
    assert got.forwarding.elsewhere == (f"desk@{HOST}",)
    assert got.needs_review
    contacts = dict(await _contacts(session, talk.first.domain_id))
    assert contacts[dead] == DEAD
