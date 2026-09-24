"""Письма рекламодателям (Этап 2) на настоящей базе: кому, под что, чем кончается.

Всё здесь — про то, чего в базе быть не должно: письма без найденной
ссылки, оффера на протухшей цене донора, второго письма тому, кому уже
писали на другом этапе, цены рекламодателя в карточке донора. Заглушкой
такое не проверить: ответ функции выглядит верно, пока не посмотришь,
что легло в таблицы.

Модель заменена так же, как в тестах очереди доноров: переписывает
приветствие и больше ничего — видно, что ссылка до неё не доходит.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    ContactSource,
    DonorStatus,
    MessageStatus,
    ReplyKind,
    SenderStatus,
    Stage,
    SuppressionReason,
)
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
    ThreadModel,
)
from backend.features.letters import reply_to
from backend.features.letters.building import BuildRequest, LetterScopeError, QueueBuilder
from backend.features.letters.followups import send_due
from backend.features.letters.repository import LetterRepository
from backend.features.letters.rewrite import Personalization, RewriteResult
from backend.features.letters.sending import (
    NoSenderError,
    RemovedAdvertiserError,
    Sending,
)
from backend.features.letters.template import TemplateError
from backend.features.letters.transport import NullTransport
from backend.features.replies.extract import Extracted
from backend.features.replies.inbound import Incoming
from backend.features.replies.outcome import ADVERTISER_LEAD
from backend.features.replies.pipeline import Inbox, Parser
from backend.features.replies.repository import LeadError, NotAPriceError, ReplyRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor, make_sender

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
DONOR = "donor.example.test"
PAGE = "https://donor.example.test/best-crm-tools-2026/"
ANCHOR = "best CRM for small teams"
SECRET = "s" * 32


class FakeRewriter:
    """Модель, которая переписывает приветствие и запоминает, что видела."""

    def __init__(self) -> None:
        self.seen: list[Personalization] = []
        self.zones: list[str] = []

    async def rewrite(self, rendered: object, about: Personalization) -> RewriteResult:
        self.seen.append(about)
        self.zones.extend(zone.text for zone in rendered.rewritable())  # type: ignore[attr-defined]
        return RewriteResult(zones={"greeting": "Good afternoon,"}, tokens_spent=90)


async def priced_donor(
    session: AsyncSession, host: str = DONOR, *, priced_days_ago: int | None = 10
) -> DomainModel:
    """Донор с ценой, полученной `priced_days_ago` дней назад. `None` — цены нет."""
    domain = await make_donor(session, host)
    donor = await session.scalar(select(DonorModel).where(DonorModel.domain_id == domain.id))
    assert donor is not None
    if priced_days_ago is not None:
        donor.last_price = Decimal("150")
        donor.last_price_currency = "USD"
        donor.last_price_at = datetime.now(UTC) - timedelta(days=priced_days_ago)
    await session.flush()
    return domain


async def make_advertiser(
    session: AsyncSession,
    host: str,
    *,
    email: str | None = "__default__",
    donor_host: str = DONOR,
    page_url: str | None = PAGE,
    anchor: str | None = ANCHOR,
    points: int = 5,
    donors: int = 1,
) -> DomainModel:
    """Рекламодатель с лучшей ссылкой и, по умолчанию, с адресом."""
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == host))
    if domain is None:
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
    session.add(
        AdvertiserModel(
            domain_id=domain.id,
            points=points,
            donors=donors,
            links=1,
            best_donor_host=donor_host,
            best_page_url=page_url,
            best_anchor=anchor,
        )
    )
    address = f"marketing@{host}" if email == "__default__" else email
    if address is not None:
        session.add(ContactModel(domain_id=domain.id, email=address, source=ContactSource.PAGE))
    await session.flush()
    return domain


async def stage_two_sender(session: AsyncSession, email: str = "max@offers.test") -> SenderModel:
    sender = SenderModel(
        domain=email.split("@", 1)[1],
        email=email,
        stage=Stage.ADVERTISERS,
        daily_cap=20,
        status=SenderStatus.FREE,
        enabled=True,
    )
    session.add(sender)
    await session.flush()
    return sender


async def build_offers(
    session: AsyncSession, rewriter: FakeRewriter | None = None, **request: object
) -> object:
    builder = QueueBuilder(session, rewriter or FakeRewriter())  # type: ignore[arg-type]
    values: dict[str, object] = {"campaign_name": "Рекламодатели", "stage": Stage.ADVERTISERS}
    values.update(request)
    report = await builder.build(BuildRequest(**values))  # type: ignore[arg-type]
    await session.flush()
    return report


async def offers(session: AsyncSession) -> list[MessageModel]:
    rows = await session.execute(
        select(MessageModel)
        .join(CampaignModel, CampaignModel.id == MessageModel.campaign_id)
        .where(CampaignModel.stage == Stage.ADVERTISERS)
        .order_by(MessageModel.id)
    )
    return list(rows.scalars().all())


async def write_as_donor(session: AsyncSession, domain: DomainModel) -> None:
    """Письмо Этапа 1 этому домену — в любом состоянии, как в жизни."""
    campaign = CampaignModel(name="Доноры", stage=Stage.DONORS, status="draft")
    session.add(campaign)
    await session.flush()
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            domain_id=domain.id,
            step=0,
            status=MessageStatus.QUEUED,
            subject="Guest article",
            body="Hi",
            idempotency_key=f"donors:{domain.host}:0",
        )
    )
    await session.flush()


class TestWhoGetsAnOffer:
    async def test_letter_is_written_under_the_found_link(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Требование — персонализация «под конкретную найденную ссылку —
        страницу и анкор»: все три части доходят до письма дословно,
        а модель не видит ни одной."""
        await priced_donor(session)
        await make_advertiser(session, "brand.example.test")
        rewriter = FakeRewriter()

        report = await build_offers(session, rewriter)

        assert report.prepared == 1  # type: ignore[attr-defined]
        (letter,) = await offers(session)
        assert letter.subject == f"Your placement on {DONOR}"
        assert f'"{ANCHOR}"' in (letter.body or "")
        assert PAGE in (letter.body or "")
        assert letter.idempotency_key == "advertisers:brand.example.test:0"
        assert [about.host for about in rewriter.seen] == ["brand.example.test"]
        assert not any(part in " ".join(rewriter.zones) for part in (DONOR, PAGE, ANCHOR))

    async def test_funnel_says_where_advertisers_ran_out(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Пустая очередь при «нет ссылки», «цена донора протухла» и «все
        написаны» выглядит одинаково — различает их только воронка."""
        await priced_donor(session)
        await priced_donor(session, "stale.example.test", priced_days_ago=200)
        await priced_donor(session, "unpriced.example.test", priced_days_ago=None)
        await make_advertiser(session, "no-anchor.example.test", anchor="  ")
        await make_advertiser(session, "stale-price.example.test", donor_host="stale.example.test")
        await make_advertiser(session, "no-price.example.test", donor_host="unpriced.example.test")
        await make_advertiser(session, "mute.example.test", email=None)
        stopped = await make_advertiser(session, "stopped.example.test")
        session.add(
            SuppressionModel(
                domain_id=stopped.id,
                reason=SuppressionReason.MANUAL,
                stage=Stage.ADVERTISERS,
            )
        )
        await make_advertiser(session, "ok.example.test")
        await session.flush()

        report = await build_offers(session)

        assert report.funnel == {  # type: ignore[attr-defined]
            "рекламодателей": 6,
            "со ссылкой": 5,
            "цена донора свежая": 3,
            "с адресом": 2,
            "вне стоп-листа": 1,
            "ещё не писали": 1,
        }
        assert [letter.idempotency_key for letter in await offers(session)] == [
            "advertisers:ok.example.test:0"
        ]

    async def test_most_convincing_go_first(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Если письма за раз кончатся на середине, они должны кончиться
        на сомнительных: сначала балл, потом число наших доноров."""
        await priced_donor(session)
        await make_advertiser(session, "weak.example.test", points=4, donors=1)
        await make_advertiser(session, "wide.example.test", points=5, donors=1)
        await make_advertiser(session, "widest.example.test", points=5, donors=3)

        await build_offers(session, limit=2)

        assert [letter.idempotency_key for letter in await offers(session)] == [
            "advertisers:widest.example.test:0",
            "advertisers:wide.example.test:0",
        ]

    async def test_runs_are_refused_not_ignored(self, session: AsyncSession) -> None:
        """У рекламодателей прогонов нет. Молча отброшенные прогоны
        человек принял бы за рассылку, суженную до них."""
        with pytest.raises(LetterScopeError, match="не по прогонам"):
            await build_offers(session, run_ids=(1,))

    async def test_stored_text_is_read_as_an_offer(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Текст вопроса донору, попавший в рассылку рекламодателям, —
        отказ сборки, а не письмо без ссылки."""
        await priced_donor(session)
        await make_advertiser(session, "brand.example.test")
        donor_text = (
            "subject: Guest article on {{host}}\n\n"
            "[greeting] rewrite\nHi,\n\n"
            "[opening] rewrite\nI came across your site while reading about tools and liked it.\n\n"
            "[offer] fixed\nWe publish sponsored articles.\n\n"
            "[ask] rewrite\nWhat is your price per article with one link, and is it dofollow?\n\n"
            "[terms] fixed\nWe pay on time.\n\n"
            "[signature] fixed\nBest regards,\n{{sender_name}}\n"
        )

        with pytest.raises(TemplateError, match="donor_host"):
            await build_offers(session, letter_template=donor_text)


class TestOneAddresseeOneStage:
    """Один адресат не получает письмо и как донор, и как рекламодатель:
    тот же довод, что у общего стоп-листа, и в обе стороны."""

    async def test_written_as_donor_gets_no_offer(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await priced_donor(session)
        both = await make_advertiser(session, "both.example.test")
        await write_as_donor(session, both)

        report = await build_offers(session)

        assert report.prepared == 0  # type: ignore[attr-defined]
        assert report.funnel["ещё не писали"] == 0  # type: ignore[attr-defined]

    async def test_offered_advertiser_is_not_asked_for_a_price(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await priced_donor(session)
        both = await make_donor(session, "both.example.test", email="editor@both.example.test")
        await make_advertiser(session, "both.example.test", email=None)
        await build_offers(session)
        assert len(await offers(session)) == 1

        donors = await LetterRepository(session).candidates(Stage.DONORS, limit=50)

        assert both.id not in {candidate.domain_id for candidate in donors}


class TestSendingAnOffer:
    async def test_goes_only_from_stage_two_boxes(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """У этапов домены отправки разные: Этап 2 конфликтнее, и репутацию
        доменов Этапа 1 он задевать не должен."""
        await priced_donor(session)
        await make_advertiser(session, "brand.example.test")
        await build_offers(session)
        (letter,) = await offers(session)
        await make_sender(session, "anna@mail.test")
        postman = Sending(session, NullTransport(), now=NOW)

        with pytest.raises(NoSenderError):
            await postman.send(letter.id)

        await stage_two_sender(session)
        sent = await postman.send(letter.id)
        assert sent.sender_email == "max@offers.test"

    async def test_removed_advertiser_is_not_written(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Перевод кандидатов снимает рекламодателя, чей донор задним числом
        попал в стоп-лист поставщиков. Собранное до этого письмо уйти не должно."""
        await priced_donor(session)
        brand = await make_advertiser(session, "brand.example.test")
        await build_offers(session)
        (letter,) = await offers(session)
        await stage_two_sender(session)
        row = await session.scalar(
            select(AdvertiserModel).where(AdvertiserModel.domain_id == brand.id)
        )
        await session.delete(row)
        await session.flush()

        with pytest.raises(RemovedAdvertiserError, match=r"brand\.example\.test"):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)


async def sent_offer(
    session: AsyncSession, *, followup_days: list[int] | None = None
) -> MessageModel:
    """Оффер, ушедший по-настоящему: с ящиком Этапа 2, тредом и сроком добивки."""
    await priced_donor(session)
    await make_advertiser(session, "brand.example.test")
    await build_offers(session, followup_days=tuple(followup_days or ()))
    (letter,) = await offers(session)
    await stage_two_sender(session)
    await Sending(session, NullTransport(), now=NOW).send(letter.id)
    await session.refresh(letter)
    return letter


class TestTheOfferChain:
    async def test_reminder_is_about_the_offer_and_keeps_the_subject(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Добивка рекламодателю — напоминание об оффере, а не вопрос о цене
        гостевой статьи, и уходит темой первого письма: переписка одна."""
        first = await sent_offer(session, followup_days=[1, 2])

        report = await send_due(
            session, transport=NullTransport(), limit=5, now=NOW + timedelta(days=2)
        )

        assert report.sent == 1
        reminder = await session.scalar(select(MessageModel).where(MessageModel.step == 1))
        assert reminder is not None
        assert reminder.subject == first.subject == f"Your placement on {DONOR}"
        assert "sponsored placement of yours" in (reminder.body or "")
        assert "guest article" not in (reminder.body or "").lower()
        assert reminder.idempotency_key == "advertisers:brand.example.test:1"

    async def test_removed_advertiser_ends_the_chain(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        first = await sent_offer(session, followup_days=[1, 2])
        row = await session.scalar(
            select(AdvertiserModel).where(AdvertiserModel.domain_id == first.domain_id)
        )
        await session.delete(row)
        await session.flush()

        report = await send_due(
            session, transport=NullTransport(), limit=5, now=NOW + timedelta(days=2)
        )

        assert report.stopped == 1
        assert report.sent == 0


class FakeExtractor:
    """Модель разбора: считает вызовы и возвращает «цену»."""

    def __init__(self) -> None:
        self.calls = 0

    async def extract(self, incoming: Incoming) -> Extracted:
        self.calls += 1
        return Extracted(price_white=Decimal("300"), currency="USD", confidence=0.99)


@pytest.fixture
def inbound_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(outreach_cfg, "INBOUND_SECRET", SECRET)
    monkeypatch.setattr(outreach_cfg, "REPLY_DOMAIN", "replies.ours.test")


def answer_to(letter: MessageModel, text: str) -> Incoming:
    return Incoming(
        message_id="<in-1@brand.test>",
        to=(
            reply_to.address_for(
                letter.id,
                sender_email="max@offers.test",
                reply_domain="replies.ours.test",
                secret=SECRET,
            ),
        ),
        from_email="marketing@brand.example.test",
        subject=f"Re: {letter.subject}",
        text=text,
    )


class TestAnAdvertiserAnswers:
    """«Мы платим $300 за статью» — расход рекламодателя, а не цена площадки."""

    async def test_answer_is_a_lead_for_a_human(
        self, session: AsyncSession, filled_legal: None, inbound_secret: None
    ) -> None:
        letter = await sent_offer(session, followup_days=[1, 2])

        got = await Inbox(session, now=NOW).accept(
            answer_to(letter, "Interesting. We pay about $300 per article at the moment.")
        )

        assert got.kind is ReplyKind.HUMAN
        assert got.bound
        assert not got.parse_pending
        assert got.needs_review
        assert got.review_reason == ADVERTISER_LEAD
        await session.refresh(letter)
        assert letter.next_action_at is None  # ответил — добивок больше нет

    async def test_parse_refuses_even_when_the_site_is_also_a_donor(
        self, session: AsyncSession, filled_legal: None, inbound_secret: None
    ) -> None:
        """Сайт бывает и донором, и рекламодателем. Разбор ответа на оффер
        положил бы его расход ценой в карточку донора."""
        letter = await sent_offer(session)
        session.add(
            DonorModel(
                domain_id=letter.domain_id, status=DonorStatus.SUITABLE, dr=40, review="accepted"
            )
        )
        await session.flush()
        got = await Inbox(session, now=NOW).accept(answer_to(letter, "We pay $300 per article."))
        extractor = FakeExtractor()

        parsed = await Parser(session, extractor, now=NOW).parse(got.reply_id or 0)  # type: ignore[arg-type]

        assert extractor.calls == 0
        assert not parsed.stored_price
        donor = await session.scalar(
            select(DonorModel).where(DonorModel.domain_id == letter.domain_id)
        )
        assert donor is not None
        assert donor.last_price is None

    async def test_price_confirmation_is_refused(
        self, session: AsyncSession, filled_legal: None, inbound_secret: None
    ) -> None:
        letter = await sent_offer(session)
        got = await Inbox(session, now=NOW).accept(answer_to(letter, "We pay $300 per article."))
        reply = await session.get(ReplyModel, got.reply_id)
        assert reply is not None

        with pytest.raises(NotAPriceError, match="лид"):
            await ReplyRepository(session).confirm(
                reply,
                by="anthony@parsingprices.com",
                price_white=Decimal("300"),
                price_grey=None,
                currency="USD",
                payment_methods=None,
            )

        assert reply.reviewed_at is None
        assert reply.price_white is None


class TestTakingTheLead:
    """Лид — не цена: его не разбирают, а берут в работу."""

    async def test_lead_is_taken_once(
        self, session: AsyncSession, filled_legal: None, inbound_secret: None
    ) -> None:
        letter = await sent_offer(session)
        got = await Inbox(session, now=NOW).accept(answer_to(letter, "Tell me more."))
        reply = await session.get(ReplyModel, got.reply_id)
        assert reply is not None
        repository = ReplyRepository(session)

        taken_at = await repository.take_lead(reply, by="anna@parsingprices.com", now=NOW)

        assert taken_at == NOW
        assert reply.reviewed_by == "anna@parsingprices.com"
        with pytest.raises(LeadError, match=r"уже в работе: взял anna@parsingprices\.com"):
            await repository.take_lead(reply, by="ivan@parsingprices.com")

    async def test_donor_answer_is_not_a_lead(self, session: AsyncSession) -> None:
        """Ответ донора разбирают как цену; «взять лидом» его нельзя."""
        domain = await make_donor(
            session, "donor-two.example.test", email="ed@donor-two.example.test"
        )
        campaign = CampaignModel(name="Доноры", stage=Stage.DONORS, status="draft")
        session.add(campaign)
        await session.flush()
        thread = ThreadModel(domain_id=domain.id, campaign_id=campaign.id)
        session.add(thread)
        await session.flush()
        reply = ReplyModel(thread_id=thread.id, kind=ReplyKind.HUMAN, raw_body="$200")
        session.add(reply)
        await session.flush()

        with pytest.raises(LeadError, match="не лид"):
            await ReplyRepository(session).take_lead(reply, by="anna@parsingprices.com")
