"""Чистка следов проверки на настоящих доменах (`prune --test-traces`) на настоящей базе.

Боевая проверка 06.10.2026 вписала свой ящик в карточки настоящих доноров: ушли
письма, пришёл ответ с ценой, цена легла донору. Тест — всё, что адресовано своим
ящикам из предохранителя. Каждое правило проверяется доменом, который должен
остаться или измениться: испорченное условие роняет тест на нём поимённо.

Домены выдуманные (`*.example.test`, `.invalid`), предохранитель подменяется
в каждом тесте: тест не должен зависеть от `.env` машины.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.cli.main import build_parser
from backend.cli.prune import EXIT_OK, EXIT_REFUSED, run_prune
from backend.config import outreach as outreach_cfg
from backend.features.contacts.manual import status_without_addresses
from backend.features.core.domain import (
    AuditAction,
    ContactSource,
    ContactStatus,
    CrawlOutcome,
    MessageStatus,
    ReplyKind,
    Stage,
    StopReason,
    Verdict,
)
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.attachment import ReplyAttachmentModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    ThreadModel,
)
from backend.features.crawl import targets
from backend.features.donors.manual_price import manual_price, price_donor
from backend.features.donors.probe import make_probe
from backend.features.letters.chain import ANSWER_STEP
from backend.features.outreach.own_inboxes import (
    InboxTrace,
    OwnInboxes,
    own_inboxes,
    remove_inbox_trace,
)
from backend.features.replies.repository import ReplyRepository
from backend.features.runs.prune import PruneRefusedError, apply_prune, plan_prune
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

MINE = "checker@ours.example.test"
TEAM = "@team.example.test"


@pytest.fixture(autouse=True)
def _allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Предохранитель как на проде: свой ящик и строка-домен, которую чистка не берёт."""
    monkeypatch.setattr(outreach_cfg, "ALLOWED_RECIPIENTS", (MINE, TEAM))
    for stage in Stage:
        monkeypatch.delenv(f"OUTREACH_{stage.value.upper()}_ALLOWED_RECIPIENTS", raising=False)


async def _count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _address(
    session: AsyncSession, domain: DomainModel, email: str = MINE, *, source: ContactSource
) -> ContactModel:
    contact = ContactModel(domain_id=domain.id, email=email, source=source)
    session.add(contact)
    await session.flush()
    return contact


async def _campaign(session: AsyncSession, name: str) -> CampaignModel:
    campaign = CampaignModel(stage=Stage.DONORS, name=name, status="running")
    session.add(campaign)
    await session.flush()
    return campaign


async def _thread(
    session: AsyncSession, campaign: CampaignModel, contact: ContactModel
) -> ThreadModel:
    thread = ThreadModel(
        domain_id=contact.domain_id, campaign_id=campaign.id, contact_id=contact.id
    )
    session.add(thread)
    await session.flush()
    return thread


async def _letter(
    session: AsyncSession,
    thread: ThreadModel,
    key: str,
    *,
    status: MessageStatus = MessageStatus.SENT,
) -> MessageModel:
    letter = MessageModel(
        campaign_id=thread.campaign_id,
        thread_id=thread.id,
        domain_id=thread.domain_id,
        contact_id=thread.contact_id,
        step=0,
        status=status,
        subject="Guest post",
        body="Hello",
        idempotency_key=key,
    )
    session.add(letter)
    await session.flush()
    return letter


async def _reply(
    session: AsyncSession,
    letter: MessageModel,
    *,
    price: str | None,
    sender: str = MINE,
) -> ReplyModel:
    reply = ReplyModel(
        thread_id=letter.thread_id,
        message_id=letter.id,
        kind=ReplyKind.HUMAN,
        raw_body="Guest post — 150 USD.",
        from_email=sender,
        price_white=Decimal(price) if price else None,
        currency="USD" if price else None,
        confidence=0.95,
    )
    session.add(reply)
    await session.flush()
    return reply


async def _priced(
    session: AsyncSession, domain: DomainModel, price: str, **domain_fields: object
) -> None:
    """Цена донору — как её кладёт приём (`replies.repository.store_price`)."""
    await session.execute(
        update(DonorModel)
        .where(DonorModel.domain_id == domain.id)
        .values(
            last_price=Decimal(price), last_price_currency="USD", last_price_at=datetime.now(UTC)
        )
    )
    if domain_fields:
        await session.execute(
            update(DomainModel).where(DomainModel.id == domain.id).values(**domain_fields)
        )


async def _found(session: AsyncSession, domain: DomainModel, *, searched: bool) -> None:
    """Адрес вписан руками — «найден» (`manual.add`); `searched` — лестница ходила."""
    await session.execute(
        update(DonorModel)
        .where(DonorModel.domain_id == domain.id)
        .values(
            contact_status=ContactStatus.FOUND,
            contact_attempted_at=datetime.now(UTC) - timedelta(days=3) if searched else None,
        )
    )


@dataclass(frozen=True, slots=True)
class Lab:
    """Проверка на двух настоящих донорах, рядом — настоящий донор и липовый."""

    mixed: DomainModel  # свой ящик рядом с настоящим адресом
    tested: DomainModel  # только свой ящик: письмо, ответ с ценой, ответ продавца
    real: DomainModel  # настоящая переписка с ценой — не трогается
    probe_id: int  # липовый донор со своим ящиком — его берёт только --probes
    check: CampaignModel  # рассылка только из проверки
    pilot: CampaignModel  # рассылка вперемешку с настоящими письмами
    priced_reply: ReplyModel


async def _lab(session: AsyncSession) -> Lab:
    await RunRepository(session).create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    mixed = await make_donor(session, "a.example.test", email="editor@a.example.test")
    tested = await make_donor(session, "b.example.test")
    real = await make_donor(session, "c.example.test", email="sales@c.example.test")
    probe = await make_probe(session, host="probe.invalid", email=MINE, author="тест")
    check, pilot, fake = (
        await _campaign(session, "Проверка"),
        await _campaign(session, "Пилот"),
        await _campaign(session, "Липовый"),
    )

    on_mixed = await _address(session, mixed, source=ContactSource.MANUAL)
    await _found(session, mixed, searched=True)
    session.add(AdvertiserModel(domain_id=mixed.id, contact_status=ContactStatus.FOUND))
    await _letter(session, await _thread(session, pilot, on_mixed), "test:a")
    editor = await session.scalar(
        select(ContactModel).where(ContactModel.email == "editor@a.example.test")
    )
    assert editor is not None
    await _letter(
        session, await _thread(session, pilot, editor), "real:a", status=MessageStatus.QUEUED
    )

    on_tested = await _address(session, tested, source=ContactSource.MANUAL)
    await _found(session, tested, searched=False)
    letter = await _letter(session, await _thread(session, check, on_tested), "test:b")
    priced = await _reply(session, letter, price="150")
    session.add(ReplyAttachmentModel(reply_id=priced.id, name="prices.pdf", accepted=True))
    await _priced(
        session,
        tested,
        "150",
        seller_answer="sells",
        seller_answer_at=datetime.now(UTC),
        seller_answer_reply_id=priced.id,
    )
    crawl = CrawlRunModel(
        host=tested.host, outcome=CrawlOutcome.OK, stop_reason=StopReason.EXHAUSTED
    )
    session.add(crawl)
    await session.flush()
    session.add(
        CandidateModel(
            crawl_run_id=crawl.id,
            donor_host=tested.host,
            target_root="brand.example.test",
            points=5,
            verdict=Verdict.BOUGHT,
        )
    )

    sales = await session.scalar(
        select(ContactModel).where(ContactModel.email == "sales@c.example.test")
    )
    assert sales is not None
    real_letter = await _letter(session, await _thread(session, pilot, sales), "real:c")
    await _reply(session, real_letter, price="180", sender="sales@c.example.test")
    await _priced(session, real, "180")

    probe_contact = await session.scalar(
        select(ContactModel).where(ContactModel.domain_id == probe.domain_id)
    )
    assert probe_contact is not None
    await _letter(session, await _thread(session, fake, probe_contact), "probe:1")
    return Lab(mixed, tested, real, probe.domain_id, check, pilot, priced)


async def _hosts_with_address(session: AsyncSession, email: str) -> list[str]:
    rows = await session.scalars(
        select(DomainModel.host)
        .join(ContactModel, ContactModel.domain_id == DomainModel.id)
        .where(ContactModel.email == email)
        .order_by(DomainModel.host)
    )
    return list(rows.all())


# --- план --------------------------------------------------------------------------------------


async def test_the_plan_names_every_domain_and_what_happens_to_its_card(
    session: AsyncSession,
) -> None:
    lab = await _lab(session)

    plan = await plan_prune(session, run_ids=[], test_traces=True)

    trace = plan.test_traces
    assert trace is not None
    assert (trace.inboxes.addresses, trace.inboxes.domains) == ((MINE,), (TEAM,))
    assert [item.host for item in trace.domains] == ["a.example.test", "b.example.test"]
    mixed, tested = trace.domains
    assert (mixed.emails, mixed.letters, mixed.threads, mixed.replies) == ([MINE], 1, 1, 0)
    # У смешанного донора остаётся настоящий адрес: исход поиска не меняется.
    assert (mixed.price, mixed.seller_answer, mixed.contact_status) == (None, None, {})
    assert (tested.letters, tested.threads, tested.replies) == (1, 1, 1)
    assert tested.price == "150.00 USD"
    assert tested.seller_answer == "продаёт"
    assert tested.contact_status == {"донор": "не искали"}
    assert trace.campaigns == [lab.check.id]
    # Показ ничего не меняет.
    assert await _hosts_with_address(session, MINE) == [
        "a.example.test",
        "b.example.test",
        "probe.invalid",
    ]


async def test_traces_go_and_real_data_stays(session: AsyncSession) -> None:
    lab = await _lab(session)
    chosen = await targets.choose(session)
    assert lab.tested.host in chosen.hosts

    plan = await plan_prune(session, run_ids=[], test_traces=True)
    await apply_prune(session, plan, author="тест")
    await session.flush()

    # Свой ящик ушёл с настоящих доменов; у липового — остался: его уносит --probes.
    assert await _hosts_with_address(session, MINE) == ["probe.invalid"]
    assert await _hosts_with_address(session, "editor@a.example.test") == ["a.example.test"]
    letters = await session.scalars(select(MessageModel.idempotency_key).order_by(MessageModel.id))
    assert list(letters.all()) == ["real:a", "real:c", "probe:1"]
    replies = await session.scalars(select(ReplyModel.from_email))
    assert list(replies.all()) == ["sales@c.example.test"]
    assert await _count(session, ReplyAttachmentModel) == 0
    campaigns = await session.scalars(select(CampaignModel.name).order_by(CampaignModel.id))
    assert list(campaigns.all()) == ["Пилот", "Липовый"]

    # Донор с ценой из тестового ответа снова без цены — и без ответа продавца.
    tested = (
        await session.execute(
            select(
                DonorModel.last_price,
                DonorModel.last_price_at,
                DonorModel.contact_status,
                DonorModel.review,
                DomainModel.seller_answer,
                DomainModel.seller_answer_reply_id,
            )
            .join(DomainModel, DomainModel.id == DonorModel.domain_id)
            .where(DonorModel.domain_id == lab.tested.id)
        )
    ).one()
    assert tuple(tested) == (None, None, None, "accepted", None, None)
    assert lab.tested.host in (await targets.choose(session)).no_price

    # Настоящая цена и настоящий адрес — на месте; обход и кандидат донора — тоже.
    real_price = await session.scalar(
        select(DonorModel.last_price).where(DonorModel.domain_id == lab.real.id)
    )
    assert real_price == Decimal("180.00")
    statuses = await session.execute(
        select(DonorModel.contact_status, AdvertiserModel.contact_status).join(
            AdvertiserModel, AdvertiserModel.domain_id == DonorModel.domain_id
        )
    )
    assert statuses.one() == (ContactStatus.FOUND, ContactStatus.FOUND)
    assert (await _count(session, CrawlRunModel), await _count(session, CandidateModel)) == (1, 1)
    journal = (
        await session.execute(
            select(AuditLogModel.target, AuditLogModel.details).where(
                AuditLogModel.action == AuditAction.DATA_PRUNED
            )
        )
    ).one()
    assert journal.target == "test-traces:2"
    assert journal.details["следы проверки"]["цена снята"] == {"b.example.test": "150.00 USD"}
    assert journal.details["следы проверки"]["ящики"] == [MINE]


async def test_together_with_probes_both_go_in_one_transaction(session: AsyncSession) -> None:
    lab = await _lab(session)

    plan = await plan_prune(session, run_ids=[], probes=True, test_traces=True)
    await apply_prune(session, plan, author="тест")
    await session.flush()

    assert await _hosts_with_address(session, MINE) == []
    assert (
        await session.scalar(select(DomainModel.id).where(DomainModel.id == lab.probe_id)) is None
    )
    letters = await session.scalars(select(MessageModel.idempotency_key).order_by(MessageModel.id))
    assert list(letters.all()) == ["real:a", "real:c"]
    target = await session.scalar(
        select(AuditLogModel.target).where(AuditLogModel.action == AuditAction.DATA_PRUNED)
    )
    assert target == f"run:{plan.runs[0]}, probes:1, test-traces:2"


# --- цена и исход поиска -----------------------------------------------------------------------


async def test_a_price_beside_another_priced_reply_stays_and_is_named(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Какой ответ принёс цену, база не помнит: при настоящем ответе с ценой на том же
    домене цена остаётся и называется, а не затирается."""
    domain = await make_donor(session, "d.example.test", email="editor@d.example.test")
    campaign = await _campaign(session, "Пилот")
    own = await _address(session, domain, source=ContactSource.MANUAL)
    await _reply(
        session, await _letter(session, await _thread(session, campaign, own), "t"), price="100"
    )
    editor = await session.scalar(
        select(ContactModel).where(ContactModel.email == "editor@d.example.test")
    )
    assert editor is not None
    real = await _letter(session, await _thread(session, campaign, editor), "r")
    await _reply(session, real, price="90", sender="editor@d.example.test")
    await _priced(session, domain, "100")

    code = await run_prune(session, build_parser().parse_args(["prune", "--test-traces", "--yes"]))

    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert "цена 100.00 USD остаётся: на домене есть и другие ответы с ценой" in out
    price = await session.scalar(
        select(DonorModel.last_price).where(DonorModel.domain_id == domain.id)
    )
    assert price == Decimal("100.00")


async def test_a_price_entered_by_hand_is_neither_named_nor_removed(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    """Цену, указанную человеком (`donors/manual_price.py`), не приносил ни один ответ:
    тестовый ответ с ценой на том же домене её не уносит, и показ её не называет."""
    domain = await make_donor(session, "m.example.test")
    campaign = await _campaign(session, "Проверка")
    own = await _address(session, domain, source=ContactSource.MANUAL)
    await _reply(
        session, await _letter(session, await _thread(session, campaign, own), "t"), price="150"
    )
    await _priced(session, domain, "150")
    donor_id = await session.scalar(select(DonorModel.id).where(DonorModel.domain_id == domain.id))
    assert donor_id is not None
    by = "anna@ours.example.test"
    await price_donor(session, donor_id, manual_price("120", "EUR", "прайс агентства", by=by))

    shown = await run_prune(session, build_parser().parse_args(["prune", "--test-traces"]))
    out = capsys.readouterr().out
    assert shown == EXIT_OK
    assert f"  m.example.test: адрес {MINE}; писем 1, переписок 1, ответов 1\n" in out
    done = await run_prune(session, build_parser().parse_args(["prune", "--test-traces", "--yes"]))
    assert done == EXIT_OK

    price = (
        await session.execute(
            select(
                DonorModel.last_price,
                DonorModel.last_price_currency,
                DonorModel.last_price_source,
                DonorModel.last_price_note,
                DonorModel.last_price_by,
            ).where(DonorModel.domain_id == domain.id)
        )
    ).one()
    assert tuple(price) == (Decimal("120.00"), "EUR", "manual", "прайс агентства", by)
    assert await _hosts_with_address(session, MINE) == []
    assert domain.host in (await targets.choose(session)).hosts


async def test_with_a_reply_price_go_its_list_and_its_source(session: AsyncSession) -> None:
    """Цена тестового ответа уходит со списком цен того же ответа и источником: без цены
    они говорили бы о цене, которой нет."""
    domain = await make_donor(session, "n.example.test")
    campaign = await _campaign(session, "Проверка")
    own = await _address(session, domain, source=ContactSource.MANUAL)
    await _reply(
        session, await _letter(session, await _thread(session, campaign, own), "t"), price="150"
    )
    await ReplyRepository(session).store_price(
        domain_id=domain.id,
        price=Decimal("150"),
        currency="USD",
        offers=[{"product": "guest post", "price": "150"}],
    )

    plan = await plan_prune(session, run_ids=[], test_traces=True)
    assert plan.test_traces is not None
    assert plan.test_traces.domains[0].price == "150.00 USD"
    await apply_prune(session, plan, author="тест")
    await session.flush()

    left = (
        await session.execute(
            select(
                DonorModel.last_price, DonorModel.last_offers, DonorModel.last_price_source
            ).where(DonorModel.domain_id == domain.id)
        )
    ).one()
    assert tuple(left) == (None, None, None)


async def test_our_answer_inside_the_thread_with_own_inbox_goes_too(
    session: AsyncSession,
) -> None:
    """Ответили с другого ящика, и наш ответ ушёл на него (`letters/answers.py`) — но в той
    же переписке со своим ящиком: уходит и он. Сам тот адрес не свой и остаётся."""
    domain = await make_donor(session, "k.example.test")
    campaign = await _campaign(session, "Проверка")
    thread = await _thread(
        session, campaign, await _address(session, domain, source=ContactSource.MANUAL)
    )
    reply = await _reply(
        session, await _letter(session, thread, "t"), price=None, sender="boss@k.example.test"
    )
    boss = await _address(session, domain, "boss@k.example.test", source=ContactSource.MANUAL)
    session.add(
        MessageModel(
            campaign_id=campaign.id,
            thread_id=thread.id,
            domain_id=domain.id,
            contact_id=boss.id,
            step=ANSWER_STEP,
            status=MessageStatus.QUEUED,
            idempotency_key="answer",
            answers_reply_id=reply.id,
        )
    )
    await session.flush()

    plan = await plan_prune(session, run_ids=[], test_traces=True)
    assert plan.test_traces is not None
    assert plan.test_traces.domains[0].letters == 2
    await apply_prune(session, plan, author="тест")
    await session.flush()

    assert (await _count(session, MessageModel), await _count(session, ThreadModel)) == (0, 0)
    assert await _hosts_with_address(session, "boss@k.example.test") == ["k.example.test"]


async def test_a_reply_without_a_price_leaves_the_price_alone(session: AsyncSession) -> None:
    domain = await make_donor(session, "e.example.test")
    campaign = await _campaign(session, "Проверка")
    own = await _address(session, domain, source=ContactSource.MANUAL)
    await _reply(
        session, await _letter(session, await _thread(session, campaign, own), "t"), price=None
    )
    await _priced(session, domain, "120")

    plan = await plan_prune(session, run_ids=[], test_traces=True)
    assert plan.test_traces is not None
    assert (plan.test_traces.domains[0].price, plan.test_traces.domains[0].price_kept) == (
        None,
        None,
    )
    await apply_prune(session, plan, author="тест")
    await session.flush()

    price = await session.scalar(
        select(DonorModel.last_price).where(DonorModel.domain_id == domain.id)
    )
    assert price == Decimal("120.00")


async def test_searched_roles_become_no_address_both_donor_and_advertiser(
    session: AsyncSession,
) -> None:
    """Лестница по домену ходила — «адреса нет»; правило одно на донора и рекламодателя
    (`manual.status_without_addresses`)."""
    domain = await make_donor(session, "f.example.test")
    await _address(session, domain, source=ContactSource.MANUAL)
    await _found(session, domain, searched=True)
    session.add(
        AdvertiserModel(
            domain_id=domain.id,
            contact_status=ContactStatus.FOUND,
            contact_attempted_at=datetime.now(UTC),
        )
    )
    await session.flush()

    plan = await plan_prune(session, run_ids=[], test_traces=True)
    assert plan.test_traces is not None
    assert plan.test_traces.domains[0].contact_status == {
        "донор": "адреса нет",
        "рекламодатель": "адреса нет",
    }
    await apply_prune(session, plan, author="тест")
    await session.flush()

    statuses = await session.execute(
        select(DonorModel.contact_status, AdvertiserModel.contact_status).join(
            AdvertiserModel, AdvertiserModel.domain_id == DonorModel.domain_id
        )
    )
    assert statuses.one() == (ContactStatus.NOT_FOUND, ContactStatus.NOT_FOUND)


def test_the_rule_without_addresses_touches_only_found() -> None:
    """Исход «не ответил» или «адреса нет» удаление адреса не трогает: он не про адрес."""
    waiting = DonorModel(contact_status=ContactStatus.NO_ANSWER, contact_attempted_at=None)
    assert status_without_addresses(waiting) is ContactStatus.NO_ANSWER


# --- какие адреса свои -------------------------------------------------------------------------


async def test_case_does_not_hide_an_own_inbox(session: AsyncSession) -> None:
    domain = await make_donor(session, "g.example.test")
    await _address(session, domain, " Checker@Ours.Example.TEST", source=ContactSource.MANUAL)

    plan = await plan_prune(session, run_ids=[], test_traces=True)

    assert plan.test_traces is not None
    assert [item.host for item in plan.test_traces.domains] == ["g.example.test"]


async def test_a_list_of_its_own_stage_counts_too(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OUTREACH_ADVERTISERS_ALLOWED_RECIPIENTS", "stage2@ours.example.test")
    domain = await make_donor(session, "h.example.test")
    await _address(session, domain, "stage2@ours.example.test", source=ContactSource.MANUAL)

    inboxes = own_inboxes()
    plan = await plan_prune(session, run_ids=[], test_traces=True)

    assert inboxes.addresses == (MINE, "stage2@ours.example.test")
    assert "OUTREACH_ADVERTISERS_ALLOWED_RECIPIENTS" in inboxes.settings
    assert plan.test_traces is not None
    assert plan.test_traces.domains[0].emails == ["stage2@ours.example.test"]


@pytest.mark.parametrize(
    ("allowlist", "says"),
    [
        ((), "предохранитель отправки не задан (OUTREACH_ALLOWED_RECIPIENTS пуст)"),
        ((TEAM, "ours.example.test"), "только домены — @team.example.test, ours.example.test"),
    ],
    ids=["пуст", "только домены"],
)
async def test_without_own_inboxes_it_is_a_refusal_not_all_clean(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    allowlist: tuple[str, ...],
    says: str,
) -> None:
    monkeypatch.setattr(outreach_cfg, "ALLOWED_RECIPIENTS", allowlist)

    with pytest.raises(PruneRefusedError, match="Тестовых ящиков нет"):
        await plan_prune(session, run_ids=[], test_traces=True)
    code = await run_prune(session, build_parser().parse_args(["prune", "--test-traces"]))

    assert code == EXIT_REFUSED
    assert says in capsys.readouterr().out


# --- консоль -----------------------------------------------------------------------------------


async def test_console_shows_by_domain_and_deletes_only_with_yes(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _lab(session)

    shown = await run_prune(session, build_parser().parse_args(["prune", "--test-traces"]))
    out = capsys.readouterr().out
    assert shown == EXIT_OK
    assert f"Следы проверки — адресованное своим ящикам: {MINE}" in out
    assert f"строки-домены предохранителя не берутся ({TEAM})" in out
    assert (
        f"  b.example.test: адрес {MINE}; писем 1, переписок 1, ответов 1; "
        "цена 150.00 USD уходит — донор снова без цены; ответ «продаёт» снимается; "
        "адресов не останется, исход поиска: донор — «не искали»"
    ) in out
    assert f"  a.example.test: адрес {MINE}; писем 1, переписок 1, ответов 0\n" in out
    assert (
        "Уйдёт: адресов 2, писем 2, переписок 2, ответов 1; рассылок без них не останется: 1" in out
    )
    assert "в базе ничего не изменилось" in out
    assert len(await _hosts_with_address(session, MINE)) == 3

    done = await run_prune(session, build_parser().parse_args(["prune", "--test-traces", "--yes"]))
    out = capsys.readouterr().out
    assert done == EXIT_OK
    assert "Удалено: адресов 2, писем 2, переписок 2, ответов 1" in out
    assert await _hosts_with_address(session, MINE) == ["probe.invalid"]


async def test_nothing_to_clean_is_said_in_words(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await make_donor(session, "clean.example.test", email="editor@clean.example.test")

    code = await run_prune(session, build_parser().parse_args(["prune", "--test-traces", "--yes"]))

    assert code == EXIT_OK
    assert "на настоящих доменах своих ящиков нет — убирать нечего" in capsys.readouterr().out


async def test_probes_line_names_probe_advertisers(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _lab(session)

    await run_prune(session, build_parser().parse_args(["prune", "--probes"]))

    assert "Липовые домены: 1 (из них пробных рекламодателей: 0)" in capsys.readouterr().out


# --- запрос удаления сам проверяет условие -----------------------------------------------------


async def test_a_real_address_smuggled_into_the_plan_survives(session: AsyncSession) -> None:
    """Что бы ни лежало в плане, удаляется только свой ящик на настоящем домене."""
    domain = await make_donor(session, "i.example.test", email="editor@i.example.test")
    editor = await session.scalar(
        select(ContactModel.id).where(ContactModel.domain_id == domain.id)
    )
    probe_domain = DomainModel(host="other.invalid")
    session.add(probe_domain)
    await session.flush()
    probe_address = await _address(session, probe_domain, source=ContactSource.MANUAL)
    assert editor is not None
    trace = InboxTrace(inboxes=OwnInboxes(addresses=(MINE,)), contacts=[editor, probe_address.id])

    await remove_inbox_trace(session, trace)
    await session.flush()

    assert await _hosts_with_address(session, "editor@i.example.test") == ["i.example.test"]
    assert await _hosts_with_address(session, MINE) == ["other.invalid"]


async def test_an_empty_trace_removes_nothing(session: AsyncSession) -> None:
    await make_donor(session, "j.example.test", email=MINE)

    await remove_inbox_trace(session, InboxTrace(inboxes=OwnInboxes(addresses=(MINE,))))

    assert await _hosts_with_address(session, MINE) == ["j.example.test"]


# --- схема -------------------------------------------------------------------------------------

#: Откуда чистка следов удаляет сама. Каскад добавит то, что уходит вместе с ними.
_REMOVED = frozenset({"replies", "messages", "threads", "contacts", "campaigns"})

#: Ссылка на удаляемое → что с ней при чистке следов (`outreach/own_inboxes.py`).
REVIEWED = {
    "messages.answers_reply_id → replies SET NULL": "наш ответ на тестовый — в той же переписке",
    "reply_attachments.reply_id → replies CASCADE": "вложения тестового ответа уходят с ним",
    "replies.message_id → messages SET NULL": "ответ на письмо своему ящику уходит раньше письма",
    "messages.thread_id → threads CASCADE": "письма переписки со своим ящиком уходят раньше неё",
    "replies.thread_id → threads CASCADE": "ответы переписки со своим ящиком уходят раньше неё",
    "messages.campaign_id → campaigns CASCADE": "рассылка уходит только пустой",
    "threads.campaign_id → campaigns CASCADE": "рассылка уходит только пустой",
    "messages.contact_id → contacts SET NULL": "письма своему ящику уходят раньше адреса",
    "threads.contact_id → contacts SET NULL": "переписка со своим ящиком уходит раньше адреса",
    "sales_leads.contact_id → contacts SET NULL": "лид знает адрес по email (sales/models.py)",
}


def test_every_reference_to_what_the_trace_cleanup_deletes_is_decided() -> None:
    """Новая ссылка на адреса, письма, переписку, ответы или рассылки — вопрос к чистке
    следов: уходит ли она раньше, гаснет или держит. Тест красный, пока ответ не вписан."""
    links = [
        (table.name, fk.parent.name, fk.target_fullname.split(".")[0], fk.ondelete or "NO ACTION")
        for table in DomainModel.metadata.tables.values()
        for fk in table.foreign_keys
    ]
    removed = set(_REMOVED)
    grown = True
    while grown:
        cascaded = {
            src for src, _, target, rule in links if target in removed and rule == "CASCADE"
        }
        grown = not cascaded <= removed
        removed |= cascaded
    found = {
        f"{src}.{column} → {target} {rule.upper()}"
        for src, column, target, rule in links
        if target in removed
    }
    assert found == set(REVIEWED), (
        f"не разобраны: {sorted(found - set(REVIEWED))}; больше нет: {sorted(set(REVIEWED) - found)}"
    )
