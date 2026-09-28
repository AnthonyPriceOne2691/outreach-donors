"""Лицо письма: свой `Message-ID` и адрес ответа на домене отправителя.

Три дефекта, которых не видно ни в одном тесте, пока письма не уходят
живым людям. Добивка ссылалась на номер письма у платформы — без скобок
и без `@`, — и ни одна почта не клала её в ветку. Запасная привязка
ответа искала этот голый номер среди токенов `<…@…>` и не нашла бы его
ни разу. А адрес ответа у всех двадцати доменов стоял на одном общем
домене — один след и одна репутация на всю рассылку.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import CampaignModel, MessageModel, ThreadModel
from backend.features.letters import identity, reply_to
from backend.features.letters.followups import send_due
from backend.features.letters.sendgrid import PROVIDER_ID_HEADER, SendGridTransport
from backend.features.letters.sending import SendError, Sending
from backend.features.letters.transport import NullTransport, Outgoing, TransportError
from backend.features.replies.binding import BindingWay
from backend.features.replies.inbound import Incoming
from backend.features.replies.mime import message_ids_in
from backend.features.replies.pipeline import Inbox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender

SECRET = "s" * 32
NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
HOST = "donor.example.test"
SENDER = "anna@mail-a.example"


async def _queued(session: AsyncSession, *, followup_days: list[int] | None = None) -> MessageModel:
    """Первое письмо донору в очереди и ящик, который его отправит."""
    domain = await make_donor(session, HOST, email=f"editor@{HOST}")
    campaign = CampaignModel(
        name="Проверка", stage=Stage.DONORS, status="draft", followup_days=followup_days
    )
    session.add(campaign)
    await session.flush()
    contact_id = await session.scalar(
        select(ContactModel.id).where(ContactModel.domain_id == domain.id)
    )
    thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id, contact_id=contact_id)
    session.add(thread)
    await session.flush()
    message = MessageModel(
        campaign_id=campaign.id,
        thread_id=thread.id,
        domain_id=domain.id,
        contact_id=contact_id,
        step=0,
        status=MessageStatus.QUEUED,
        subject="Advertising rates",
        body="Hi there,\n\nWhat is your price?\n\nAnna Ro",
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(message)
    await make_sender(session, SENDER)
    await session.flush()
    return message


class Platform:
    """Поддельная платформа: запоминает запросы и отвечает «принято»."""

    def __init__(self) -> None:
        self.seen: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(json.loads(request.content))
        return httpx.Response(202, headers={PROVIDER_ID_HEADER: f"sg-{len(self.seen)}"})


class Recording(NullTransport):
    """Нулевой транспорт, который помнит, что ему отдали."""

    def __init__(self) -> None:
        self.seen: list[Outgoing] = []

    async def send(self, outgoing: Outgoing) -> str:
        self.seen.append(outgoing)
        return await super().send(outgoing)


class TestOwnMessageId:
    def test_it_is_on_the_sender_domain_and_names_the_letter(self) -> None:
        own = identity.new_message_id(417, sender_email="Anna@Mail-A.example")

        assert re.fullmatch(r"<[0-9a-f]{32}\.m417@mail-a\.example>", own)

    def test_it_cannot_be_guessed(self) -> None:
        """Запасная привязка верит идентификатору из заголовков ответа:
        угаданный подсунул бы письмо в чужой диалог."""
        made = {identity.new_message_id(417, sender_email=SENDER) for _ in range(64)}

        assert len(made) == 64

    def test_reply_side_finds_it_in_the_headers(self) -> None:
        """Ответ ищет токены `<…@…>`: идентификатор другой формы там
        не нашёлся бы — ровно так не находился номер платформы."""
        own = identity.new_message_id(1, sender_email=SENDER)

        assert message_ids_in(f"{own} <other@site.test>") == (own, "<other@site.test>")
        assert identity.is_message_id(own)

    @pytest.mark.parametrize(
        "value", ["W8Rx7c2bQvuh3sVUkwV8Hw", "<W8Rx7c2bQvuh3sVUkwV8Hw>", "", None]
    )
    def test_platform_number_is_not_a_message_id(self, value: str | None) -> None:
        assert not identity.is_message_id(value)

    def test_box_without_domain_is_named(self) -> None:
        with pytest.raises(identity.SenderAddressError, match="sender-add"):
            identity.new_message_id(1, sender_email="anna")


class TestReplyAddressOnTheSenderDomain:
    def test_each_domain_gets_its_own_reply_domain(self) -> None:
        """Один общий домен ответов связывал все домены отправки: упала
        его репутация — упали все, и у фильтров один след на всю рассылку."""
        first = reply_to.address_for(1, sender_email="anna@Mail-A.example", secret=SECRET)
        second = reply_to.address_for(1, sender_email="max@mail-b.example", secret=SECRET)

        assert first.startswith("anna+m1.")
        assert first.rpartition("@")[2] == "replies.mail-a.example"
        assert second.rpartition("@")[2] == "replies.mail-b.example"

    def test_works_without_the_setting(self) -> None:
        field = outreach_cfg._Outreach.model_fields["reply_subdomain"]

        assert field.default == "replies"

    def test_prefix_is_a_setting(self) -> None:
        address = reply_to.address_for(1, sender_email=SENDER, subdomain="In.Mail.", secret=SECRET)

        assert address.endswith("@in.mail.mail-a.example")

    @pytest.mark.parametrize("prefix", ["", "replies @", "-replies"])
    def test_broken_prefix_is_named(self, prefix: str) -> None:
        with pytest.raises(reply_to.ReplyAddressError, match="OUTREACH_REPLY_SUBDOMAIN"):
            reply_to.address_for(1, sender_email=SENDER, subdomain=prefix, secret=SECRET)

    @pytest.mark.parametrize(
        "domain", ["replies.mail-a.example", "replies.mail-b.example", "ours.test"]
    )
    def test_label_binds_on_any_domain(self, domain: str) -> None:
        """Метка читается без домена: смена приставки или ящика не должна
        терять ответы на письма, ушедшие раньше."""
        label = reply_to.label_for(417, secret=SECRET)

        assert reply_to.message_id_from(f"anna+{label}@{domain}", secret=SECRET) == 417

    def test_plus_in_the_box_keeps_the_label(self) -> None:
        address = reply_to.address_for(417, sender_email="anna+sales@mail-a.example", secret=SECRET)

        assert reply_to.message_id_from(address, secret=SECRET) == 417


class TestSendingStampsTheLetter:
    async def test_message_id_is_in_the_base_when_the_transport_is_called(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Той же фиксацией, что «отправляется»: платформа могла принять
        письмо за миг до обрыва связи, и якорь к этому мигу уже записан."""
        message = await _queued(session)
        stored_at_call: list[str | None] = []

        class Peeking(Recording):
            async def send(self, outgoing: Outgoing) -> str:
                stored_at_call.append(
                    await session.scalar(
                        select(MessageModel.internet_message_id).where(
                            MessageModel.id == outgoing.message_id
                        )
                    )
                )
                return await super().send(outgoing)

        transport = Peeking()
        await Sending(session, transport, now=NOW).send(message.id)

        own = transport.seen[0].internet_message_id
        assert stored_at_call == [own]
        assert own.endswith("@mail-a.example>")
        await session.refresh(message)
        assert message.internet_message_id == own

    async def test_lost_connection_keeps_the_anchor(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Связь оборвалась, а платформа могла письмо и принять: оно остаётся
        в «отправляется» — и с тем идентификатором, с которым ушло бы."""
        message = await _queued(session)
        handed: list[str] = []

        class Lost(NullTransport):
            async def send(self, outgoing: Outgoing) -> str:
                handed.append(outgoing.internet_message_id)
                raise ConnectionResetError("ответ платформы потерялся")

        with pytest.raises(ConnectionResetError):
            await Sending(session, Lost(), now=NOW).send(message.id)

        await session.refresh(message)
        assert message.status is MessageStatus.SENDING
        assert message.internet_message_id == handed[0]

    async def test_refusal_takes_the_message_id_back(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Платформа сказала «нет» — письмо в очереди, и повтор может уйти
        с другого ящика, то есть с другого домена."""
        message = await _queued(session)

        class Refusing(NullTransport):
            async def send(self, outgoing: Outgoing) -> str:
                raise TransportError("Платформа отказала (400)")

        with pytest.raises(SendError, match="400"):
            await Sending(session, Refusing(), now=NOW).send(message.id)

        await session.refresh(message)
        assert message.status is MessageStatus.QUEUED
        assert message.sender_id is None
        assert message.internet_message_id is None

    async def test_unbuildable_reply_address_leaves_the_letter_queued(
        self, session: AsyncSession, filled_legal: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Отказ настройки — не отказ почты. Раньше адрес ответа собирался
        уже после отметки «отправляется», и письмо, не ушедшее никуда,
        оставалось в ней до разбора руками."""
        message = await _queued(session)
        monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", "")

        class Real(Recording):
            real = True

        transport = Real()
        with pytest.raises(SendError, match="OUTREACH_INBOUND_SECRET"):
            await Sending(session, transport, now=NOW).send(message.id)

        await session.refresh(message)
        assert message.status is MessageStatus.QUEUED
        assert message.sender_id is None
        assert transport.seen == []

    async def test_platform_gets_the_same_message_id(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """До самой платформы: заголовок `Message-ID` в запросе — тот, что
        в базе; номер платформы остаётся рядом, для её журнала."""
        message = await _queued(session)
        platform = Platform()
        async with httpx.AsyncClient(transport=httpx.MockTransport(platform)) as http:
            transport = SendGridTransport(api_key="sg-test-key", allowlist=(), http=http)
            await Sending(session, transport, now=NOW).send(message.id)

        await session.refresh(message)
        sent = platform.seen[0]
        assert sent["headers"]["Message-ID"] == message.internet_message_id
        assert sent["reply_to"]["email"].endswith("@replies.mail-a.example")
        assert message.provider_message_id == "sg-1"


class TestTheChainThreads:
    async def test_followup_points_at_our_first_message_id(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        first = await _queued(session, followup_days=[1, 2])
        platform = Platform()
        async with httpx.AsyncClient(transport=httpx.MockTransport(platform)) as http:
            transport = SendGridTransport(api_key="sg-test-key", allowlist=(), http=http)
            await Sending(session, transport, now=NOW).send(first.id)
            report = await send_due(
                session, transport=transport, limit=5, now=NOW + timedelta(days=2)
            )

        await session.refresh(first)
        assert report.sent == 1
        headers = platform.seen[1]["headers"]
        anchor = first.internet_message_id
        assert anchor is not None
        assert anchor.startswith("<")
        assert anchor.endswith(">")
        assert headers["In-Reply-To"] == anchor
        assert headers["References"] == anchor
        # У добивки свой идентификатор — на том же домене: ящик тот же.
        assert headers["Message-ID"] != anchor
        assert headers["Message-ID"].endswith("@mail-a.example>")

    async def test_first_letter_without_message_id_does_not_break_the_chain(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Выдуманные и пробные письма базы разработки ушли до своего
        идентификатора: добивка к ним уходит, только без заголовков цепочки."""
        first = await _queued(session, followup_days=[1, 2])
        await Sending(session, NullTransport(), now=NOW).send(first.id)
        first.internet_message_id = None
        await session.flush()
        transport = Recording()

        report = await send_due(session, transport=transport, limit=5, now=NOW + timedelta(days=2))

        assert report.sent == 1
        assert transport.seen[0].in_reply_to is None

    async def test_platform_number_never_becomes_a_thread_header(self) -> None:
        platform = Platform()
        outgoing = Outgoing(
            message_id=2,
            to="editor@donor.test",
            from_email=SENDER,
            from_name="Anna Ro",
            reply_to=None,
            subject="Re: Advertising rates",
            body="Just checking in.",
            internet_message_id="<5f0c2a9e.m2@mail-a.example>",
            in_reply_to="W8Rx7c2bQvuh3sVUkwV8Hw",
        )
        async with httpx.AsyncClient(transport=httpx.MockTransport(platform)) as http:
            transport = SendGridTransport(api_key="sg-test-key", allowlist=(), http=http)
            await transport.send(outgoing)
            with pytest.raises(TransportError, match="Message-ID"):
                await transport.send(replace(outgoing, internet_message_id=""))

        assert len(platform.seen) == 1, "письмо без своего Message-ID не уходит"
        assert "In-Reply-To" not in platform.seen[0]["headers"]
        assert "References" not in platform.seen[0]["headers"]


class TestReplyFindsOurLetter:
    async def test_reply_without_label_binds_by_our_message_id(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Ответ пришёл на общий ящик, метки нет — но `In-Reply-To` несёт
        тот идентификатор, что донор видел у себя в ящике."""
        first = await _queued(session)
        await Sending(session, NullTransport(), now=NOW).send(first.id)
        await session.refresh(first)
        assert first.internet_message_id is not None

        got = await Inbox(session, now=NOW).accept(
            Incoming(
                message_id="<re-1@donor.example.test>",
                to=("info@ours.test",),
                from_email=f"editor@{HOST}",
                subject="Re: Advertising rates",
                text="We charge 250 EUR per article.",
                in_reply_to=first.internet_message_id,
            )
        )

        assert got.bound
        assert got.way is BindingWay.HEADERS
