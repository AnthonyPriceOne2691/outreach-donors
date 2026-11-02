"""Молчащий DNS получателя и письмо продаж — на настоящем модуле продаж.

Тесты почты (`test_dns_return.py`, `test_recipient_dns_hold.py`) идут на подставном модуле: они о
правиле возврата. Здесь — стык: письмо собрала сборка продаж, ушло автоотправкой и проходом
добивок, отказ пришёл событием, вернул его проход разбора, и дальше письмо снова судит модуль. Его
решения подставной модуль не повторит: что сборка второго письма не заводит, что письмо снятого лида
не возвращается, что правка лида не теряется и что ожидание DNS — не остановка прохода.

Часы теста и часы базы — разные шкалы: времена правки лида здесь заданы на шкале теста.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from backend.features.core.domain import MessageStatus
from backend.features.core.models.outreach import MessageModel
from backend.features.letters import dns_return, followups, recipient_dns
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.letters.transport import Outgoing
from backend.features.sales import autosend, lead_edit, queue, stoplist_leads
from backend.features.sales.autosend import Stop
from backend.features.sales.models import SalesLeadModel, SalesStoplistModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests import test_sales_send_world as w
from tests.test_mail_accounts import _ByStage
from tests.test_mail_identity import Recording
from tests.test_recipient_dns_hold import Dns, Postman

OUTAGE = (
    "unable to get mx info: failed to get IPs from PTR record: lookup <nil>: unrecognized address"
)
JANE = "jane@acme.example.test"
OLGA = "olga@beta.example.test"
SILENT = "acme.example.test"
SENT = w.NOW
REFUSED = SENT + timedelta(minutes=1)
BACK = SENT + dns_return.SETTLE + timedelta(minutes=1)
AGAIN = BACK + timedelta(minutes=1)


class Keeper(Postman):
    """Настоящая почта для отправки, которая помнит письмо целиком."""

    def __init__(self) -> None:
        super().__init__()
        self.kept: list[Outgoing] = []

    async def send(self, outgoing: Outgoing) -> str:
        self.kept.append(outgoing)
        return await super().send(outgoing)


def _mail() -> tuple[_ByStage, Keeper]:
    post = Keeper()
    return _ByStage(sales=post, donors=Recording(), advertisers=Recording()), post


@pytest.fixture
async def world(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> w.World:
    return await w.world(session, monkeypatch)


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> Dns:
    found = Dns()
    monkeypatch.setattr(recipient_dns, "mail_route", found)
    return found


async def _build(session: AsyncSession, world: w.World) -> queue.QueueReport:
    return await queue.build(
        session, w.CorridorRewriter(), hypothesis_id=world.hypothesis_id, limit=10
    )


async def _queued(session: AsyncSession, world: w.World, *emails: str) -> list[MessageModel]:
    """Первые письма лидов — сборкой продаж. Лида после этого не правили."""
    for email in emails:
        await w.lead(session, world.hypothesis_id, email, name=email.split("@")[0].title())
    await _build(session, world)
    await session.execute(
        update(SalesLeadModel)
        .values(updated_at=SENT - timedelta(days=1))
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    rows = await session.scalars(
        select(MessageModel).where(MessageModel.step == FIRST_STEP).order_by(MessageModel.id)
    )
    return list(rows)


async def _gone(session: AsyncSession, world: w.World) -> tuple[MessageModel, _ByStage, Keeper]:
    """Первое письмо Джейн ушло автоотправкой."""
    [letter] = await _queued(session, world, JANE)
    source, post = _mail()
    await autosend.run(session, source, now=SENT)
    await session.commit()
    await session.refresh(letter)
    return letter, source, post


async def _refused(session: AsyncSession, letter: MessageModel, at: datetime) -> None:
    event = DeliveryEvent("bounce", letter.id, JANE, reason=OUTAGE, soft=False, at=at)
    await apply_events(session, [event], now=at)
    await session.commit()
    await session.refresh(letter)


async def _return(session: AsyncSession, now: datetime = BACK) -> dns_return.Returned:
    report = await dns_return.return_letters(session, now=now)
    await session.commit()
    return report


async def _letters_of_step(session: AsyncSession, step: int) -> int:
    found = await session.scalar(
        select(func.count()).select_from(MessageModel).where(MessageModel.step == step)
    )
    return int(found or 0)


async def _edit(session: AsyncSession, company: str) -> None:
    """Человек поправил компанию лида, пока письмо лежало отказом."""
    lead = await session.scalar(select(SalesLeadModel))
    assert lead is not None
    await lead_edit.change(session, lead.id, {"company": company}, author_id=None)
    await session.execute(
        update(SalesLeadModel)
        .where(SalesLeadModel.id == lead.id)
        .values(updated_at=REFUSED + timedelta(minutes=5))
        .execution_options(synchronize_session=False)
    )
    await session.commit()


class TestTheWay:
    async def test_a_first_letter_goes_again_and_its_chain_lives(
        self, session: AsyncSession, world: w.World, dns: Dns
    ) -> None:
        letter, source, post = await _gone(session, world)
        await _refused(session, letter, REFUSED)

        report = await _return(session)
        await session.refresh(letter)
        found = await autosend.run(session, source, now=AGAIN)
        await session.commit()
        await session.refresh(letter)

        assert (report.back, report.taken) == (1, 0)
        assert list(found.sent) == [letter.id]
        assert post.sent == [JANE, JANE]
        assert (letter.status, letter.sent_at) == (MessageStatus.SENT, AGAIN)
        assert letter.next_action_at is not None

        follow = await followups.send_due(
            session, transport=source, limit=5, now=letter.next_action_at
        )

        assert follow.sent == 1
        assert post.kept[-1].in_reply_to == letter.internet_message_id

    async def test_the_builder_writes_no_second_letter(
        self, session: AsyncSession, world: w.World, dns: Dns
    ) -> None:
        letter, _, _ = await _gone(session, world)
        await _refused(session, letter, REFUSED)

        await _build(session, world)
        assert await _letters_of_step(session, FIRST_STEP) == 1

        await _return(session)
        again = await _build(session, world)

        assert again.waiting.get(queue.UP_TO_DATE) == 1
        assert again.tokens_spent == 0
        assert await _letters_of_step(session, FIRST_STEP) == 1

    async def test_a_followup_goes_again_as_the_same_letter_from_the_box_of_the_dialog(
        self, session: AsyncSession, world: w.World, dns: Dns
    ) -> None:
        letter, source, post = await _gone(session, world)
        due = letter.next_action_at
        assert due is not None
        await followups.send_due(session, transport=source, limit=5, now=due)
        step = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
        assert step is not None
        await _refused(session, step, due + timedelta(minutes=1))
        back = due + dns_return.SETTLE + timedelta(minutes=5)

        await _return(session, back)
        report = await followups.send_due(session, transport=source, limit=5, now=back)
        await session.refresh(step)

        assert report.sent == 1
        assert step.status is MessageStatus.SENT
        assert step.sender_id == letter.sender_id
        assert await _letters_of_step(session, 1) == 1
        assert post.sent == [JANE, JANE, JANE]


class TestALeadThatChanged:
    async def test_a_closed_lead_gets_nothing_and_the_letter_stays_refused(
        self, session: AsyncSession, world: w.World, dns: Dns
    ) -> None:
        letter, source, post = await _gone(session, world)
        await _refused(session, letter, REFUSED)
        session.add(SalesStoplistModel(email=JANE))
        await session.flush()
        assert await stoplist_leads.close(session, now=REFUSED) == 1
        await session.commit()

        report = await _return(session)
        await session.refresh(letter)
        found = await autosend.run(session, source, now=AGAIN)

        assert report.back == 0
        assert letter.status is MessageStatus.BOUNCED
        assert found.stop is Stop.EMPTY
        assert post.sent == [JANE]

    async def test_an_edit_of_the_lead_is_not_lost(
        self, session: AsyncSession, world: w.World, dns: Dns
    ) -> None:
        """Человек видит письмо к новой компании, а не к прежней."""
        letter, source, post = await _gone(session, world)
        await _refused(session, letter, REFUSED)
        await _edit(session, "Renamed Test Co")

        await _return(session)
        refused = await autosend.run(session, source, now=AGAIN)
        await _build(session, world)
        found = await autosend.run(session, source, now=AGAIN + timedelta(minutes=1))

        assert refused.sent == {}
        assert list(found.sent) == [letter.id]
        assert len(post.kept) == 2
        assert "Renamed Test Co" in post.kept[-1].body
        assert await _letters_of_step(session, FIRST_STEP) == 1


class TestThePass:
    async def test_a_window_waiting_for_dns_is_no_stall(
        self, session: AsyncSession, world: w.World, dns: Dns
    ) -> None:
        [letter] = await _queued(session, world, JANE)
        dns.silent.add(SILENT)
        source, post = _mail()

        found = await autosend.run(session, source, now=SENT)
        await session.refresh(letter)

        assert found.stop is None
        assert found.refused == {letter.id: "DNS получателя не отвечает"}
        assert letter.status is MessageStatus.QUEUED
        assert post.sent == []

    async def test_a_refusal_next_to_waiting_letters_is_still_a_stall(
        self, session: AsyncSession, world: w.World, dns: Dns
    ) -> None:
        await _queued(session, world, JANE, OLGA)
        dns.silent.add(SILENT)
        session.add(SalesStoplistModel(email=OLGA))
        await session.flush()
        source, _ = _mail()

        found = await autosend.run(session, source, now=SENT)

        assert found.stop is Stop.REFUSED_ALL
        assert "стоп-лист: 1" in (found.stopped or "")

    async def test_a_silent_domain_at_the_head_does_not_hold_the_next_letter(
        self, session: AsyncSession, world: w.World, dns: Dns
    ) -> None:
        jane, _ = await _queued(session, world, JANE, OLGA)
        dns.silent.add(SILENT)
        source, post = _mail()

        await autosend.run(session, source, now=SENT)
        await session.refresh(jane)

        assert post.sent == [OLGA]
        assert jane.status is MessageStatus.QUEUED

    async def test_a_followup_waits_for_the_domain_and_goes_after(
        self, session: AsyncSession, world: w.World, dns: Dns
    ) -> None:
        letter, source, _ = await _gone(session, world)
        due = letter.next_action_at
        assert due is not None
        dns.silent.add(SILENT)

        waits = await followups.send_due(session, transport=source, limit=5, now=due)
        await session.refresh(letter)

        assert (waits.sent, waits.postponed) == (0, 1)
        assert letter.next_action_at == due + recipient_dns.WAIT

        dns.silent.clear()
        recipient_dns.MEMORY.silent.clear()
        goes = await followups.send_due(
            session, transport=source, limit=5, now=due + recipient_dns.WAIT
        )

        assert goes.sent == 1
        assert await _letters_of_step(session, 1) == 1
