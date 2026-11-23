"""Лид продаж — адресат привязки по отправителю (`sales/reply_sender.py`, `replies/by_sender.py`).

Письмо продаж уходит лиду переписки, а строки `contacts` у такого письма нет: адрес живёт у лида,
лид — у переписки по связи `sales_threads`. Почта модуль продаж не импортирует, поэтому поиск по
лидам лежит в продажах и приходит в приём параметром — с поиском по контактам его соединяет
маршрут приёма. Здесь — сам поиск (лидов с адресом 0, 1 и 2), приём с обоими поисками и маршрут
целиком: лид, написавший заново на ящик продаж — без метки и цепочки, — попадает в свою
переписку, если платформа подтвердила отправителя (DKIM домена из «От кого» или SPF с тем же
доменом конверта); без подтверждения ответ ждёт человека во вкладке «Не привязаны». База
настоящая, адреса выдуманы.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from backend.api.inbound import routes as inbound_routes
from backend.features.core.domain import MessageStatus, ReplyKind, SenderStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from backend.features.replies import unbound
from backend.features.replies.binding import BindingWay, Unbound
from backend.features.replies.by_sender import mail_letters
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Accepted, Inbox
from backend.features.sales.models import (
    LeadSource,
    LeadStatus,
    SalesHypothesisModel,
    SalesLeadModel,
    SalesThreadModel,
)
from backend.features.sales.reply_sender import lead_letters
from backend.shared.queue import SALES_REPLY_JOB
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.inbound_forms import AUTH, URL, NoQueue, multipart, setup_inbound

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
SALES_BOX = "sales@mail-sales.example.test"
JANE = "jane@lead.example.test"
LEAD_HOST = "lead.example.test"
#: Поиски, которые передаёт приёму маршрут (`api/inbound/routes.SENDER_LOOKUPS`).
BOTH = (mail_letters, lead_letters)


@dataclass(frozen=True, slots=True)
class Dialog:
    """Переписка продаж с лидом: первое письмо и добивка, обе ушли."""

    lead: SalesLeadModel
    first: MessageModel
    followup: MessageModel


@pytest.fixture(autouse=True)
def queue(monkeypatch: pytest.MonkeyPatch) -> NoQueue:
    """Секрет и подставные очереди вебхука: и разбора цены, и продаж."""
    found = setup_inbound(monkeypatch)
    monkeypatch.setattr(inbound_routes, "sales_queue", lambda: found)
    return found


@pytest.fixture(autouse=True)
async def box(session: AsyncSession) -> None:
    """Ящик продаж: по нему ответ узнаёт своё направление."""
    session.add(
        SenderModel(
            domain=SALES_BOX.split("@", 1)[1],
            email=SALES_BOX,
            stage=Stage.SALES,
            daily_cap=20,
            status=SenderStatus.FREE,
            enabled=True,
        )
    )
    await session.flush()


async def _domain(session: AsyncSession, host: str) -> DomainModel:
    found = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    if found is None:
        found = DomainModel(host=host)
        session.add(found)
        await session.flush()
    return found


async def _sent(
    session: AsyncSession, thread: ThreadModel, step: int, sent_at: datetime | None
) -> MessageModel:
    """Письмо продаж в переписке — как его заводит сборка очереди: без контакта."""
    letter = MessageModel(
        campaign_id=thread.campaign_id,
        thread_id=thread.id,
        domain_id=thread.domain_id,
        contact_id=None,
        step=step,
        status=MessageStatus.SENT if sent_at else MessageStatus.QUEUED,
        subject="A made-up question",
        body="Hi,\n\nshort question.",
        sent_at=sent_at,
        idempotency_key=f"sales-sender:{thread.id}:{step}",
    )
    session.add(letter)
    await session.flush()
    return letter


async def dialog(session: AsyncSession, email: str = JANE, *, sent: bool = True) -> Dialog:
    """Лид своей гипотезы, его переписка и два письма: первое и добивка — позже."""
    domain = await _domain(session, LEAD_HOST)
    taken = await session.scalar(select(func.count(SalesHypothesisModel.id)))
    hypothesis = SalesHypothesisModel(name=f"Выдуманная гипотеза {taken}")
    campaign = CampaignModel(stage=Stage.SALES, name="Продажи", status="running")
    session.add_all([hypothesis, campaign])
    await session.flush()
    lead = SalesLeadModel(
        hypothesis_id=hypothesis.id,
        domain_id=domain.id,
        email=email,
        source=LeadSource.IMPORT,
        status=LeadStatus.READY,
    )
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=None)
    session.add_all([lead, thread])
    await session.flush()
    session.add(
        SalesThreadModel(
            thread_id=thread.id, lead_id=lead.id, language="en", chain_version="chain-made-up-1"
        )
    )
    first = await _sent(session, thread, 0, NOW - timedelta(days=8) if sent else None)
    followup = await _sent(session, thread, 1, NOW - timedelta(days=1) if sent else None)
    return Dialog(lead=lead, first=first, followup=followup)


def fresh(sender: str = JANE) -> Incoming:
    """Письмо, написанное лидом заново: на ящик продаж, без метки и без заголовков цепочки.
    Отправитель подтверждён подписью DKIM домена лида."""
    return Incoming(
        message_id="<fresh-1@lead.example.test>",
        to=(SALES_BOX,),
        from_email=sender,
        subject="Still interested",
        text="Hi! Is your offer still on? We could talk next week.",
        dkim_passed=(LEAD_HOST,),
    )


def webhook_form(
    proof: Sequence[tuple[str, bytes]], envelope_from: str = JANE
) -> list[tuple[str, bytes]]:
    """Форма платформы с письмом лида заново; итоги проверок подлинности — `proof`."""
    return [
        ("headers", b"Message-ID: <fresh-1@lead.example.test>\nSubject: Still interested\n"),
        ("to", SALES_BOX.encode()),
        ("from", f"Jane <{JANE}>".encode()),
        ("envelope", json.dumps({"to": [SALES_BOX], "from": envelope_from}).encode()),
        ("subject", b"Still interested"),
        ("charsets", json.dumps({"to": "UTF-8", "from": "UTF-8", "text": "UTF-8"}).encode()),
        ("text", b"Hi! Is your offer still on? We could talk next week."),
        *proof,
    ]


async def _stored(session: AsyncSession, got: Accepted) -> ReplyModel:
    assert got.reply_id is not None
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    return reply


class TestLeadLookup:
    async def test_nobody_with_this_address(self, session: AsyncSession) -> None:
        await dialog(session)

        assert await lead_letters(session, "max@globex.example.test", Stage.SALES) == []

    async def test_one_lead_pairs_its_letters_latest_first(self, session: AsyncSession) -> None:
        jane = await dialog(session)

        found = await lead_letters(session, JANE, Stage.SALES)

        key = f"lead:{jane.lead.id}"
        assert found == [(jane.followup.id, key), (jane.first.id, key)]

    async def test_two_leads_with_one_address_are_two_addressees(
        self, session: AsyncSession
    ) -> None:
        """Один адрес у лидов двух гипотез — два адресата."""
        one, two = await dialog(session), await dialog(session)

        found = await lead_letters(session, JANE, Stage.SALES)

        assert {key for _, key in found} == {f"lead:{one.lead.id}", f"lead:{two.lead.id}"}

    async def test_the_lead_address_is_compared_whole_without_case_and_edge_spaces(
        self, session: AsyncSession
    ) -> None:
        jane = await dialog(session, " Jane@Lead.Example.Test ")

        assert [n for n, _ in await lead_letters(session, JANE, Stage.SALES)] == [
            jane.followup.id,
            jane.first.id,
        ]
        assert await lead_letters(session, "info@lead.example.test", Stage.SALES) == []

    async def test_letters_still_in_the_queue_do_not_count(self, session: AsyncSession) -> None:
        await dialog(session, sent=False)

        assert await lead_letters(session, JANE, Stage.SALES) == []

    @pytest.mark.parametrize("stage", [Stage.DONORS, Stage.ADVERTISERS])
    async def test_outside_sales_a_lead_is_nobody(
        self, session: AsyncSession, stage: Stage
    ) -> None:
        await dialog(session)

        assert await lead_letters(session, JANE, stage) == []


class TestInboxWithBothLookups:
    async def test_a_fresh_letter_of_the_lead_joins_its_dialog_at_the_latest_letter(
        self, session: AsyncSession
    ) -> None:
        jane = await dialog(session)

        got = await Inbox(session, now=NOW, lookups=BOTH).accept(fresh())

        reply = await _stored(session, got)
        assert (got.bound, got.way, got.kind) == (True, BindingWay.SENDER, ReplyKind.HUMAN)
        assert (reply.message_id, reply.thread_id) == (jane.followup.id, jane.followup.thread_id)
        # Ответ продаж — очереди продаж: его вид разбирает модуль продаж.
        assert got.to_sales == reply.id

    async def test_contacts_alone_do_not_know_the_lead(self, session: AsyncSession) -> None:
        """Приём по умолчанию ищет только контакты — поэтому маршрут передаёт и поиск лидов."""
        await dialog(session)

        got = await Inbox(session, now=NOW).accept(fresh())

        assert (got.bound, got.unbound) == (False, Unbound.NO_LABEL)

    async def test_two_leads_with_one_address_stay_with_a_human(
        self, session: AsyncSession
    ) -> None:
        await dialog(session)
        await dialog(session)

        got = await Inbox(session, now=NOW, lookups=BOTH).accept(fresh())

        reply = await _stored(session, got)
        assert (got.bound, got.unbound) == (False, Unbound.NO_LABEL)
        assert (reply.message_id, reply.thread_id) == (None, None)


class TestWebhook:
    @pytest.mark.parametrize(
        ("proof", "envelope_from"),
        [
            ([("dkim", b"{@lead.example.test : pass}"), ("SPF", b"softfail")], JANE),
            ([("dkim", b"none"), ("SPF", b"pass")], "bounce@lead.example.test"),
        ],
        ids=["dkim домена From", "SPF и тот же домен конверта"],
    )
    async def test_the_route_binds_a_confirmed_fresh_letter_of_the_lead_and_queues_it_for_sales(
        self,
        session: AsyncSession,
        client: AsyncClient,
        queue: NoQueue,
        proof: list[tuple[str, bytes]],
        envelope_from: str,
    ) -> None:
        """Маршрут приёма соединяет оба поиска: письмо лида заново — в его переписку."""
        jane = await dialog(session)
        await session.commit()
        body, headers = multipart(webhook_form(proof, envelope_from))

        response = await client.post(URL, content=body, headers={**headers, **AUTH})

        assert response.status_code == 200, response.text
        taken = response.json()
        assert taken["bound"] is True
        reply = await session.get(ReplyModel, taken["reply_id"])
        assert reply is not None
        assert (reply.message_id, reply.thread_id) == (jane.followup.id, jane.followup.thread_id)
        assert (SALES_REPLY_JOB, (reply.id,)) in queue.jobs

    @pytest.mark.parametrize(
        ("proof", "envelope_from"),
        [
            ([], JANE),
            (
                [("dkim", b"{@lead.example.test : fail}"), ("SPF", b"pass")],
                "bounce@relay.example.test",
            ),
            ([("dkim", b"{@relay.example.test : pass}"), ("SPF", b"fail")], JANE),
            ([("dkim", b"none"), ("SPF", b"softfail")], JANE),
            ([("dkim", b"none"), ("SPF", b"neutral")], JANE),
            ([("dkim", b"none"), ("SPF", b"none")], JANE),
            ([("dkim", b"none"), ("SPF", b"")], JANE),
            ([("dkim", b"{@lead.example.test pass}"), ("SPF", b"temperror")], JANE),
        ],
        ids=[
            "итогов нет",
            "dkim fail, SPF чужого конверта",
            "подпись пересылки",
            "SPF softfail",
            "SPF neutral",
            "SPF none",
            "SPF пусто",
            "битая строка dkim",
        ],
    )
    async def test_an_unconfirmed_fresh_letter_of_the_lead_waits_in_unbound(
        self,
        session: AsyncSession,
        client: AsyncClient,
        queue: NoQueue,
        proof: list[tuple[str, bytes]],
        envelope_from: str,
    ) -> None:
        """«От кого» без подтверждения — просто поле письма: ответ ждёт человека во вкладке
        «Не привязаны» с прежней причиной, очереди продаж не достаётся."""
        await dialog(session)
        await session.commit()
        body, headers = multipart(webhook_form(proof, envelope_from))

        response = await client.post(URL, content=body, headers={**headers, **AUTH})

        assert response.status_code == 200, response.text
        taken = response.json()
        assert (taken["bound"], taken["needs_review"]) == (False, True)
        reply = await session.get(ReplyModel, taken["reply_id"])
        assert reply is not None
        assert (reply.message_id, reply.thread_id) == (None, None)
        assert reply.unbound_reason == Unbound.NO_LABEL
        assert [found.id for found in await unbound.page(session, stages=(Stage.SALES,))] == [
            reply.id
        ]
        assert all(job != SALES_REPLY_JOB for job, _ in queue.jobs)
