"""Приём ответов на настоящей базе: привязка, повторы, последствия.

На заглушках эти правила не проверить: все они про то, что после письма
изменилось в базе, а не про то, что вернула функция. Повтор вебхука,
стоп-лист, отметка мёртвого адреса и цена, не попавшая в карточку
донора, — четыре разных таблицы.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    ReplyKind,
    Stage,
)
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.letters import reply_to
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Attachment, Incoming
from backend.features.replies.pipeline import Inbox, Parser
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
SECRET = "s" * 32
HOST = "donor.example.test"
WROTE_TO = "editor@donor.example.test"


class FakeExtractor:
    """Модель разбора. Возвращает то, что велели, и считает вызовы."""

    def __init__(self, found: Extracted | None = None) -> None:
        self.found = found or Extracted()
        self.calls = 0

    async def extract(self, incoming: Incoming) -> Extracted:
        self.calls += 1
        return self.found


@pytest.fixture(autouse=True)
def inbound_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    monkeypatch.setattr(outreach_cfg, "REPLY_DOMAIN", "replies.ours.test")


@pytest.fixture
async def sent(session: AsyncSession) -> MessageModel:
    """Наше письмо, которое уже ушло донору."""
    domain = DomainModel(host=HOST)
    campaign = CampaignModel(stage=Stage.DONORS, name="Проверка", status="running")
    session.add_all([domain, campaign])
    await session.flush()

    session.add(DonorModel(domain_id=domain.id, status=DonorStatus.SUITABLE, dr=40))
    contact = ContactModel(domain_id=domain.id, email=WROTE_TO, source=ContactSource.PAGE)
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
        provider_message_id="<ours-1@mail.test>",
        idempotency_key=f"donors:{HOST}:0",
    )
    session.add(message)
    await session.commit()
    return message


def reply_from(
    message: MessageModel,
    text: str,
    *,
    sender: str = WROTE_TO,
    message_id: str = "<in-1@site.test>",
    labelled: bool = True,
    **extra: object,
) -> Incoming:
    to = (
        reply_to.address_for(
            message.id,
            sender_email="anna@mail.test",
            reply_domain="replies.ours.test",
            secret=SECRET,
        )
        if labelled
        else "info@ours.test"
    )
    values: dict[str, object] = {
        "message_id": message_id,
        "to": (to,),
        "from_email": sender,
        "subject": "Re: Advertising rates",
        "text": text,
    }
    values.update(extra)
    return Incoming(**values)  # type: ignore[arg-type]


async def _count(session: AsyncSession, model: type) -> int:
    rows = await session.execute(select(func.count()).select_from(model))
    return int(rows.scalar_one())


class TestAccepting:
    async def test_label_binds_the_reply_to_the_thread(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        got = await Inbox(session, now=NOW).accept(reply_from(sent, "We charge 250 EUR."))

        assert got.bound
        assert got.kind is ReplyKind.HUMAN
        reply = (await session.execute(select(ReplyModel))).scalars().one()
        assert reply.thread_id == sent.thread_id
        assert reply.raw_body == "We charge 250 EUR."

    async def test_repeat_of_the_webhook_does_not_double_anything(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Платформа доставляет события «хотя бы один раз» и повторяет их
        при сбое. Без отметки повтор дал бы второй ответ, второй разбор
        и второй платный вызов модели."""
        inbox = Inbox(session, now=NOW)
        await inbox.accept(reply_from(sent, "250 EUR"))
        await session.commit()

        again = await inbox.accept(reply_from(sent, "250 EUR"))

        assert again.duplicate
        assert again.reply_id is None
        assert await _count(session, ReplyModel) == 1

    async def test_unbound_reply_is_kept(self, session: AsyncSession) -> None:
        """Выброшенный ответ выглядит как «донор не ответил», и причину
        будут искать в лестнице контактов."""
        stray = Incoming(
            message_id="<stray@site.test>",
            to=("info@ours.test",),
            from_email="someone@elsewhere.test",
            subject="Hello",
            text="Are you the people who wrote to us?",
        )

        got = await Inbox(session, now=NOW).accept(stray)

        assert not got.bound
        assert got.needs_review
        reply = (await session.execute(select(ReplyModel))).scalars().one()
        assert reply.thread_id is None

    async def test_headers_bind_when_the_label_is_lost(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        got = await Inbox(session, now=NOW).accept(
            reply_from(sent, "250 EUR", labelled=False, in_reply_to="<ours-1@mail.test>")
        )

        assert got.bound
        assert got.way.value == "headers"

    async def test_attachments_are_recorded(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Прайс приходит файлом чаще, чем текстом: ответ, выглядящий
        пустым, — это ответ, из которого не видно главного."""
        await Inbox(session, now=NOW).accept(
            reply_from(
                sent,
                "See attached.",
                attachments=(
                    Attachment(name="price.pdf", size=900, content_type="application/pdf"),
                ),
            )
        )

        reply = (await session.execute(select(ReplyModel))).scalars().one()
        assert reply.attachments is not None
        assert reply.attachments[0]["имя"] == "price.pdf"


class TestConsequencesOnTheBase:
    async def test_unsubscribe_puts_the_address_in_the_stop_list(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        await Inbox(session, now=NOW).accept(
            reply_from(sent, "Please unsubscribe me from your list.")
        )

        row = (await session.execute(select(SuppressionModel))).scalars().one()
        assert row.email == WROTE_TO

    async def test_bounce_marks_the_contact_and_the_letter(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        await Inbox(session, now=NOW).accept(
            reply_from(sent, "550 5.1.1 user unknown", sender="mailer-daemon@site.test")
        )

        # Сбрасываем в базу, а не перечитываем: `refresh` затёр бы
        # несохранённые изменения значениями из базы.
        await session.flush()
        contact = await session.get(ContactModel, sent.contact_id)
        assert contact is not None
        assert contact.verification_status == "bounced"
        assert sent.status is MessageStatus.BOUNCED

    async def test_answering_address_becomes_preferred(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Дальше пишем тому, кто отвечает, а не в ящик, где письмо
        пролежало неделю."""
        await Inbox(session, now=NOW).accept(
            reply_from(sent, "We charge 250 EUR.", sender="elena@donor.example.test")
        )

        rows = await session.execute(
            select(ContactModel).where(ContactModel.email == "elena@donor.example.test")
        )
        elena = rows.scalars().one()
        assert elena.last_replied_at == NOW

    async def test_auto_reply_changes_nothing(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        await Inbox(session, now=NOW).accept(
            reply_from(sent, "I am out of the office until Monday.")
        )

        assert await _count(session, SuppressionModel) == 0
        await session.flush()
        assert sent.status is MessageStatus.SENT

    async def test_reply_stops_queued_follow_ups(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        followup = MessageModel(
            campaign_id=sent.campaign_id,
            thread_id=sent.thread_id,
            domain_id=sent.domain_id,
            contact_id=sent.contact_id,
            step=1,
            status=MessageStatus.QUEUED,
            subject="Re: Advertising rates",
            body="Just checking in",
            idempotency_key=f"donors:{HOST}:1",
        )
        session.add(followup)
        await session.flush()

        await Inbox(session, now=NOW).accept(reply_from(sent, "We charge 250 EUR."))

        await session.flush()
        assert followup.status is MessageStatus.STOPPED


class TestParsingThePrice:
    async def _accept(self, session: AsyncSession, sent: MessageModel, text: str) -> int:
        got = await Inbox(session, now=NOW).accept(reply_from(sent, text))
        await session.flush()
        assert got.reply_id is not None
        return got.reply_id

    async def test_confident_price_reaches_the_donor(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        reply_id = await self._accept(session, sent, "Placement is 250 EUR.")
        extractor = FakeExtractor(
            Extracted(price_white=Decimal("250"), currency="EUR", confidence=0.95)
        )

        parsed = await Parser(session, extractor, now=NOW).parse(reply_id)  # type: ignore[arg-type]

        assert parsed.stored_price
        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert donor.last_price == Decimal("250.00")
        assert donor.last_price_currency == "EUR"

    async def test_unsure_price_does_not_reach_the_donor(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Э1-24: ниже порога — в ручную очередь, а не в базу."""
        reply_id = await self._accept(session, sent, "Maybe around 250 EUR, I need to check.")
        extractor = FakeExtractor(
            Extracted(price_white=Decimal("250"), currency="EUR", confidence=0.4)
        )

        parsed = await Parser(session, extractor, now=NOW).parse(reply_id)  # type: ignore[arg-type]

        assert not parsed.stored_price
        assert parsed.needs_review
        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert donor.last_price is None
        # Разобранное всё равно сохранено: человек должен видеть, что
        # именно модель предложила, рядом с исходным текстом.
        reply = await session.get(ReplyModel, reply_id)
        assert reply is not None
        assert reply.price_white == Decimal("250.00")

    async def test_currency_is_kept_as_named(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Конвертации в сервисе нет: подписать евро долларами значит
        записать неверное число."""
        reply_id = await self._accept(session, sent, "Placement is 1200 PLN.")
        extractor = FakeExtractor(
            Extracted(price_white=Decimal("1200"), currency="PLN", confidence=0.9)
        )

        await Parser(session, extractor, now=NOW).parse(reply_id)  # type: ignore[arg-type]

        donor = (await session.execute(select(DonorModel))).scalars().one()
        assert donor.last_price_currency == "PLN"

    async def test_model_is_not_called_for_an_auto_reply(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Разбирать цену в автоответчике — платить за заведомо пустой
        результат."""
        reply_id = await self._accept(session, sent, "I am out of the office until Monday.")
        extractor = FakeExtractor()

        await Parser(session, extractor, now=NOW).parse(reply_id)  # type: ignore[arg-type]

        assert extractor.calls == 0

    async def test_acceptance_itself_never_calls_the_model(
        self, session: AsyncSession, sent: MessageModel
    ) -> None:
        """Платформа повторяет вебхук по таймауту: платный вызов внутри
        запроса означал бы повторные списания."""
        got = await Inbox(session, now=NOW).accept(reply_from(sent, "Placement is 250 EUR."))

        assert got.parse_pending
        reply = await session.get(ReplyModel, got.reply_id or 0)
        assert reply is not None
        assert reply.confidence is None
