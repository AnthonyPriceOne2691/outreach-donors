"""Следующий адрес целиком: письмо ушло, не дошло — и следующее уходит само.

Соседний `test_next_address.py` кладёт в базу готовые состояния писем.
Здесь то же правило проходит настоящим путём, каким его пройдёт первая
рассылка: сборка, отправка, событие платформы или автоответ через приём
ответов, пересборка — и добивки новой попытки. Заглушкой не проверить
главного: что метка мёртвого адреса, которую ставят события и приём,
и отбор, который её читает, говорят об одном и том же.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from backend.features.contacts.preference import DEAD
from backend.features.core.domain import (
    ContactSource,
    MessageStatus,
    ReplyKind,
    SenderStatus,
    Stage,
)
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, SenderModel
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.events import DeliveryEvent, apply_events
from backend.features.letters.followups import send_due
from backend.features.letters.rewrite import RewriteResult
from backend.features.letters.sending import Sending
from backend.features.letters.transport import Outgoing
from backend.features.replies.inbound import Incoming
from backend.features.replies.pipeline import Inbox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
HOST = "donor.example.test"
INFO = f"info@{HOST}"
EDITOR = f"editor@{HOST}"


class TemplateOnly:
    """Модель, которая ничего не переписывает: текст письма здесь не важен."""

    async def rewrite(self, rendered: object, about: object) -> RewriteResult:
        return RewriteResult(notes=["модель в тесте не участвует"])


class RecordingTransport:
    """Почта, которая запоминает всё, что ей отдали."""

    name = "recording"
    real = False

    def __init__(self) -> None:
        self.handed: list[Outgoing] = []

    async def send(self, outgoing: Outgoing) -> str:
        self.handed.append(outgoing)
        return f"provider-{len(self.handed)}"


async def _donor_with_two_addresses(
    session: AsyncSession, host: str = HOST
) -> tuple[DomainModel, ContactModel]:
    """Донор: `info@` лучший (записан раньше), `editor@` — следующий."""
    domain = await make_donor(session, host, email=f"info@{host}")
    editor = ContactModel(domain_id=domain.id, email=f"editor@{host}", source=ContactSource.PAGE)
    session.add(editor)
    await session.flush()
    return domain, editor


async def _build(
    session: AsyncSession, *, name: str = "Доноры", stage: Stage = Stage.DONORS
) -> MessageModel | None:
    """Собрать очередь и вернуть письмо, которое она положила; `None` — ничего."""
    before = set(await session.scalars(select(MessageModel.id)))
    await QueueBuilder(session, TemplateOnly()).build(  # type: ignore[arg-type]
        BuildRequest(campaign_name=name, stage=stage, followup_days=(1, 2))
    )
    new = [
        letter
        for letter in await session.scalars(select(MessageModel).order_by(MessageModel.id))
        if letter.id not in before
    ]
    assert len(new) <= 1, [letter.idempotency_key for letter in new]
    return new[0] if new else None


async def _sent(
    session: AsyncSession, transport: RecordingTransport, *, name: str = "Доноры"
) -> MessageModel:
    letter = await _build(session, name=name)
    assert letter is not None
    await Sending(session, transport, now=NOW).send(letter.id)
    await session.refresh(letter)
    return letter


async def _email_of(session: AsyncSession, letter: MessageModel) -> str | None:
    contact = await session.get(ContactModel, letter.contact_id)
    return contact.email if contact else None


class TestAfterTheBounce:
    async def test_bounce_event_sends_the_next_letter_to_the_next_address(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Ящика нет — следующий адрес сразу: пересборка кладёт письмо
        на `editor@` с ключом второй попытки."""
        await _donor_with_two_addresses(session)
        await make_sender(session, "anna@mail-a.example.test")
        transport = RecordingTransport()
        first = await _sent(session, transport)
        assert await _email_of(session, first) == INFO

        await apply_events(
            session,
            [DeliveryEvent("bounce", first.id, INFO, reason="550 5.1.1 no such user")],
            now=NOW,
        )
        second = await _build(session)

        assert second is not None
        assert await _email_of(session, second) == EDITOR
        assert second.idempotency_key == f"donors:{HOST}:0:a2"
        assert second.thread_id != first.thread_id
        # Второе письмо уходит тем же путём, что первое.
        await Sending(session, transport, now=NOW).send(second.id)
        assert [outgoing.to for outgoing in transport.handed] == [INFO, EDITOR]

    async def test_dead_mailbox_autoreply_sends_the_next_letter(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Автоответ «ящик больше не читается» проходит настоящий приём:
        правило вида, отметка мёртвого адреса — и сборка её видит."""
        await _donor_with_two_addresses(session)
        await make_sender(session, "anna@mail-a.example.test")
        first = await _sent(session, RecordingTransport())

        accepted = await Inbox(session, now=NOW).accept(
            Incoming(
                message_id="<auto-1@donor.example.test>",
                to=("anna@mail-a.example.test",),
                from_email=INFO,
                subject="Automatic reply: Guest article",
                text="This inbox is not being monitored.",
                in_reply_to=first.internet_message_id,
                headers={"Auto-Submitted": "auto-replied"},
            )
        )
        second = await _build(session)

        assert accepted.bound
        assert (accepted.kind, accepted.rule) == (ReplyKind.BOUNCE, "мёртвый ящик")
        contact = await session.get(ContactModel, first.contact_id)
        assert contact is not None
        assert contact.verification_status == DEAD
        assert second is not None
        assert await _email_of(session, second) == EDITOR
        assert second.idempotency_key == f"donors:{HOST}:0:a2"

    async def test_address_named_by_the_dead_mailbox_goes_first(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Мёртвый ящик сам назвал, куда писать (`replies/redirect.py`), — письмо
        уходит туда, а не на следующий из найденных лестницей: адрес назвал
        сам донор. Домен — с настоящей зоной: адрес на `.test` приём честно
        считает чужим и отдаёт человеку."""
        site = "gardenletters.co.uk"
        await _donor_with_two_addresses(session, site)
        await make_sender(session, "anna@mail-a.example.test")
        first = await _sent(session, RecordingTransport())

        await Inbox(session, now=NOW).accept(
            Incoming(
                message_id=f"<auto-3@{site}>",
                to=("anna@mail-a.example.test",),
                from_email=f"info@{site}",
                subject="Automatic reply: Guest article",
                text=f"This mailbox is no longer monitored. Please write to chief@{site}.",
                in_reply_to=first.internet_message_id,
                headers={"Auto-Submitted": "auto-replied"},
            )
        )
        second = await _build(session)

        assert second is not None
        assert await _email_of(session, second) == f"chief@{site}"
        assert second.idempotency_key == f"donors:{site}:0:a2"

    async def test_human_answer_means_no_next_letter_ever(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Ответ получен — разговор идёт там, где начался. Даже когда ящик,
        с которого ответили, потом вернул отказ доставки."""
        await _donor_with_two_addresses(session)
        await make_sender(session, "anna@mail-a.example.test")
        first = await _sent(session, RecordingTransport())

        accepted = await Inbox(session, now=NOW).accept(
            Incoming(
                message_id="<human-1@donor.example.test>",
                to=("anna@mail-a.example.test",),
                from_email=INFO,
                subject="Re: Guest article",
                text="Hi Anna, a sponsored post is 200 USD.",
                in_reply_to=first.internet_message_id,
            )
        )
        await apply_events(session, [DeliveryEvent("bounce", first.id, INFO)], now=NOW)

        assert accepted.kind is ReplyKind.HUMAN
        assert await _build(session) is None

    async def test_soft_bounce_keeps_the_address_but_takes_the_next(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await _donor_with_two_addresses(session)
        await make_sender(session, "anna@mail-a.example.test")
        first = await _sent(session, RecordingTransport())

        await apply_events(
            session,
            [DeliveryEvent("bounce", first.id, INFO, reason="blocked", soft=True)],
            now=NOW,
        )
        second = await _build(session)

        info = await session.get(ContactModel, first.contact_id)
        assert info is not None
        assert info.verification_status != DEAD
        assert second is not None
        assert await _email_of(session, second) == EDITOR

    async def test_letter_on_its_way_holds_the_next_one(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Ушло, событий ещё нет — это не повод писать на второй адрес."""
        await _donor_with_two_addresses(session)
        await make_sender(session, "anna@mail-a.example.test")
        first = await _sent(session, RecordingTransport())

        assert first.status is MessageStatus.SENT
        assert await _build(session) is None


class TestTheSecondChain:
    async def test_followup_of_the_second_attempt_has_its_own_key_and_anchor(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Добивка второй попытки наследует её номер — иначе упёрлась бы
        в ключ добивки первой — и ложится в ветку своего первого письма,
        а не того, что не дошло."""
        _, editor = await _donor_with_two_addresses(session)
        await make_sender(session, "anna@mail-a.example.test")
        transport = RecordingTransport()
        first = await _sent(session, transport)
        await apply_events(session, [DeliveryEvent("bounce", first.id, INFO)], now=NOW)
        second = await _build(session)
        assert second is not None
        await Sending(session, transport, now=NOW).send(second.id)
        await session.refresh(second)

        report = await send_due(session, transport=transport, limit=5, now=NOW + timedelta(days=2))

        assert report.sent == 1
        followup = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
        assert followup is not None
        assert followup.idempotency_key == f"donors:{HOST}:1:a2"
        assert (followup.thread_id, followup.contact_id) == (second.thread_id, editor.id)
        handed = transport.handed[-1]
        assert handed.to == EDITOR
        assert handed.in_reply_to == second.internet_message_id
        assert handed.in_reply_to != first.internet_message_id

    async def test_second_chain_does_not_collide_with_the_first_ones_followup(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Ящик умер после первой добивки: у первой цепочки уже есть ключ
        `…:1`. Добивка второй попытки с тем же ключом не вставилась бы,
        и проход добивок падал бы на ней каждый час."""
        await _donor_with_two_addresses(session)
        await make_sender(session, "anna@mail-a.example.test")
        transport = RecordingTransport()
        first = await _sent(session, transport)
        await send_due(session, transport=transport, limit=5, now=NOW + timedelta(days=2))
        early = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
        assert early is not None
        assert early.idempotency_key == f"donors:{HOST}:1"
        await Inbox(session, now=NOW).accept(
            Incoming(
                message_id="<auto-2@donor.example.test>",
                to=("anna@mail-a.example.test",),
                from_email=INFO,
                subject="Automatic reply: Guest article",
                text="This mailbox is no longer monitored.",
                in_reply_to=early.internet_message_id,
                headers={"Auto-Submitted": "auto-replied"},
            )
        )
        second = await _build(session)
        assert second is not None
        await Sending(session, transport, now=NOW + timedelta(days=3)).send(second.id)

        report = await send_due(session, transport=transport, limit=5, now=NOW + timedelta(days=5))

        assert report.sent == 1
        keys = sorted(await session.scalars(select(MessageModel.idempotency_key)))
        assert keys == [
            f"donors:{HOST}:0",
            f"donors:{HOST}:0:a2",
            f"donors:{HOST}:1",
            f"donors:{HOST}:1:a2",
        ]
        assert transport.handed[-1].to == EDITOR
        assert first.thread_id != second.thread_id

    async def test_first_attempt_chain_keeps_the_plain_key(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Цепочка первой попытки — прежние ключи: письма, собранные до
        попыток, и их добивки не расходятся с уже лежащими в базе."""
        await _donor_with_two_addresses(session)
        await make_sender(session, "anna@mail-a.example.test")
        await _sent(session, RecordingTransport())

        await send_due(
            session, transport=RecordingTransport(), limit=5, now=NOW + timedelta(days=2)
        )

        keys = sorted(await session.scalars(select(MessageModel.idempotency_key)))
        assert keys == [f"donors:{HOST}:0", f"donors:{HOST}:1"]


class TestStageTwo:
    """Правило одно на оба этапа: оффер рекламодателю тоже уходит на следующий
    адрес, а не дошедшее письмо донору сайт не закрывает."""

    async def _advertiser(self, session: AsyncSession, host: str) -> DomainModel:
        priced = await make_donor(session, "priced.example.test")
        donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == priced.id))
        assert donor is not None
        donor.last_price, donor.last_price_currency = Decimal("150"), "USD"
        donor.last_price_at = datetime.now(UTC) - timedelta(days=10)
        domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
        if domain is None:
            domain = DomainModel(host=host)
            session.add(domain)
            await session.flush()
        session.add(
            AdvertiserModel(
                domain_id=domain.id,
                points=5,
                donors=1,
                links=1,
                best_donor_host="priced.example.test",
                best_page_url="https://priced.example.test/best-tools/",
                best_anchor="best tools",
            )
        )
        await session.flush()
        return domain

    async def _stage_two_sender(self, session: AsyncSession) -> None:
        session.add(
            SenderModel(
                domain="offers.example.test",
                email="max@offers.example.test",
                stage=Stage.ADVERTISERS,
                daily_cap=20,
                status=SenderStatus.FREE,
                enabled=True,
            )
        )
        await session.flush()

    async def test_bounced_offer_goes_to_the_next_address(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        brand = await self._advertiser(session, "brand.example.test")
        for local in ("marketing", "press"):
            session.add(
                ContactModel(
                    domain_id=brand.id,
                    email=f"{local}@brand.example.test",
                    source=ContactSource.PAGE,
                )
            )
        await self._stage_two_sender(session)
        await session.flush()
        first = await _build(session, name="Офферы", stage=Stage.ADVERTISERS)
        assert first is not None
        await Sending(session, RecordingTransport(), now=NOW).send(first.id)

        await apply_events(session, [DeliveryEvent("bounce", first.id, "x")], now=NOW)
        second = await _build(session, name="Офферы", stage=Stage.ADVERTISERS)

        assert second is not None
        assert await _email_of(session, second) == "press@brand.example.test"
        assert second.idempotency_key == "advertisers:brand.example.test:0:a2"

    async def test_dead_donor_letter_does_not_close_the_site(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Письмо донору не дошло — сайт его не видел, и оффер ему как
        рекламодателю законен. Номер попытки свой у этапа: первый оффер —
        прежний ключ; потолок — общий."""
        domain, _editor = await _donor_with_two_addresses(session)
        await self._advertiser(session, HOST)
        await make_sender(session, "anna@mail-a.example.test")
        first = await _sent(session, RecordingTransport())
        await apply_events(session, [DeliveryEvent("bounce", first.id, INFO)], now=NOW)

        offer = await _build(session, name="Офферы", stage=Stage.ADVERTISERS)

        assert offer is not None
        assert await _email_of(session, offer) == EDITOR
        assert offer.idempotency_key == f"advertisers:{HOST}:0"
        campaign = await session.get(CampaignModel, offer.campaign_id)
        assert campaign is not None
        assert campaign.stage is Stage.ADVERTISERS
        assert domain.id == offer.domain_id
