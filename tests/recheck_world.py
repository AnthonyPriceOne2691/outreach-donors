"""Донор с протухшей ценой — для тестов перезапроса цены (`letters/price_recheck.py`).

Строится так, как выглядит в бою: первое письмо ушло с ящика переписки, донор ответил
с другого адреса и назвал цену, цена легла в карточку — и с тех пор прошло больше срока
свежести. Ответ на перезапрос приходит тем же приёмом (`Inbox`) по метке письма и
разбирается тем же разбором (`Parser`), только модель подставная.

**Время ответа ставится руками.** Тесты идут во внешней транзакции, и `now()` базы в ней
одно — минута начала теста: ответ, принятый после перезапроса, иначе выглядел бы старше
него. В бою у каждого запроса своя транзакция, и ответ новее письма, на которое отвечает.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from backend.features.core.domain import (
    MessageStatus,
    PriceSource,
    ReplyKind,
    Stage,
    ThreadStatus,
)
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from backend.features.letters import reply_to
from backend.features.letters.transport import NullTransport, Outgoing
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox, Parser
from backend.features.replies.repository import ReplyRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender

HOST = "stale.example.test"
#: Ящик, который ведёт переписку.
BOX = "anna@mail.example.test"
#: Адрес, с которого донор ответил: не тот, на который ушло первое письмо.
ANSWERED_FROM = f"boss@{HOST}"
#: Сколько дней цене: срок свежести — 150.
STALE_DAYS = 200


class Recording(NullTransport):
    """Нулевая почта, которая запоминает, что ей отдали."""

    def __init__(self) -> None:
        self.seen: list[Outgoing] = []

    async def send(self, outgoing: Outgoing) -> str:
        self.seen.append(outgoing)
        return await super().send(outgoing)


class Priced:
    """Модель разбора: в ответе цена, разбор уверен."""

    def __init__(self, price: str = "120") -> None:
        self.found = Extracted(
            price_white=Decimal(price), currency="USD", placement="sells", confidence=0.95
        )

    async def extract(self, incoming: Incoming) -> Extracted:
        return self.found


@dataclass(frozen=True, slots=True)
class World:
    """Донор с протухшей ценой из ответа в переписке."""

    donor: DonorModel
    thread: ThreadModel
    first: MessageModel
    reply: ReplyModel
    box: SenderModel


async def donor_of(session: AsyncSession, domain_id: int) -> DonorModel:
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain_id))
    assert donor is not None
    return donor


async def stale_conversation(
    session: AsyncSession, moment: datetime, *, host: str = HOST, box: str = BOX
) -> World:
    """Донор ответил ценой `STALE_DAYS` дней назад — с другого адреса, в переписке
    с ящика `box`; цена из ответа лежит в карточке."""
    domain = await make_donor(session, host, email=f"editor@{host}")
    sender = await session.scalar(select(SenderModel).where(SenderModel.email == box))
    if sender is None:
        sender = await make_sender(session, box)
    campaign = CampaignModel(name=f"Рассылка {host}", stage=Stage.DONORS, status="draft")
    session.add(campaign)
    await session.flush()
    contact_id = await session.scalar(
        select(ContactModel.id).where(ContactModel.domain_id == domain.id)
    )
    thread = ThreadModel(
        domain_id=domain.id,
        campaign_id=campaign.id,
        contact_id=contact_id,
        status=ThreadStatus.REPLIED,
    )
    session.add(thread)
    await session.flush()
    long_ago = moment - timedelta(days=STALE_DAYS + 1)
    first = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact_id,
        sender_id=sender.id,
        step=0,
        status=MessageStatus.DELIVERED,
        subject=f"Guest article on {host}",
        body="Hi,\n\nCould you let me know your price per article?",
        sent_at=long_ago,
        internet_message_id=f"<first.{host}@{box.split('@')[1]}>",
        idempotency_key=f"donors:{host}:0",
    )
    session.add(first)
    await session.flush()
    answered = f"boss@{host}"
    await ReplyRepository(session).remember_answering_address(
        domain_id=domain.id, email=answered, now=long_ago
    )
    reply = ReplyModel(
        thread_id=thread.id,
        message_id=first.id,
        kind=ReplyKind.HUMAN,
        raw_body="Our price is $90 per article.",
        inbound_message_id=f"<in-1@{host}>",
        from_email=answered,
        subject=f"Re: Guest article on {host}",
        created_at=moment - timedelta(days=STALE_DAYS),
        price_white=Decimal("90"),
        currency="USD",
        confidence=0.95,
        placement="sells",
        offers=[],
    )
    session.add(reply)
    donor = await donor_of(session, domain.id)
    donor.last_price = Decimal("90")
    donor.last_price_currency = "USD"
    donor.last_price_at = reply.created_at
    donor.last_price_source = PriceSource.REPLY.value
    await session.commit()
    return World(donor=donor, thread=thread, first=first, reply=reply, box=sender)


async def manual_stale(
    session: AsyncSession, moment: datetime, *, host: str, email: str | None = None
) -> DonorModel:
    """Донор, чью цену вписали руками `STALE_DAYS` дней назад, — писем ему не было."""
    domain = await make_donor(session, host, email=email or f"editor@{host}")
    donor = await donor_of(session, domain.id)
    donor.last_price = Decimal("150")
    donor.last_price_currency = "EUR"
    donor.last_price_at = moment - timedelta(days=STALE_DAYS)
    donor.last_price_source = PriceSource.MANUAL.value
    donor.entered_by = "anna@ours.example.test"
    await session.commit()
    return donor


async def answer(
    session: AsyncSession,
    letter: MessageModel,
    text: str,
    *,
    at: datetime,
    sender: str = ANSWERED_FROM,
    box: str = BOX,
    message_id: str = "<in-2@stale.example.test>",
) -> ReplyModel:
    """Донор ответил на письмо `letter` — по метке в адресе ответа, как его примет вебхук."""
    got = await Inbox(session).accept(
        Incoming(
            message_id=message_id,
            to=(reply_to.address_for(letter.id, sender_email=box),),
            from_email=sender,
            subject=f"Re: {letter.subject}",
            text=text,
        )
    )
    assert got.bound, got.as_report
    assert got.reply_id is not None
    reply = await session.get(ReplyModel, got.reply_id)
    assert reply is not None
    reply.created_at = at
    await session.commit()
    return reply


async def parse(session: AsyncSession, reply: ReplyModel, *, price: str = "120") -> bool:
    """Разбор ответа тем же путём, что задача очереди, — моделью, назвавшей `price`."""
    parsed = await Parser(session, Priced(price)).parse(reply.id)
    await session.commit()
    return parsed.stored_price
