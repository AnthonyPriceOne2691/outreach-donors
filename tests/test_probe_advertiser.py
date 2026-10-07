"""Пробный рекламодатель на настоящей базе: заводится в зоне .invalid со своим ящиком
и настоящей ссылкой обхода, сборка офферов с лимитом 1 берёт его первым, письмо уходит
своему ящику, чистка `--probes` уносит его целиком.

Адреса и домены выдуманные (`*.example.test`), предохранитель подменяется в каждом
тесте: тест не должен зависеть от того, что лежит в `.env` машины.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from backend.cli.main import build_parser
from backend.cli.probe_advertiser import EXIT_OK, EXIT_REFUSED, run_probe_advertiser
from backend.config import outreach as outreach_cfg
from backend.features.core.domain import (
    AuditAction,
    ContactSource,
    ContactStatus,
    CrawlOutcome,
    ReplyKind,
    SenderStatus,
    Stage,
    StopReason,
    SuppressionReason,
    Verdict,
)
from backend.features.core.models.access import AuditLogModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.crawl import CrawlRunModel, OutLinkModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.core.models.outreach import (
    CampaignModel,
    MessageModel,
    ReplyModel,
    SenderModel,
)
from backend.features.crawl.probe_advertiser import ProbeAdvertiser, make_probe_advertiser
from backend.features.donors.probe import ProbeError
from backend.features.letters.building import BuildRequest, QueueBuilder
from backend.features.letters.recipients import Recipients
from backend.features.letters.rewrite import Personalization, RewriteResult
from backend.features.letters.sending import Sending
from backend.features.letters.transport import NullTransport
from backend.features.runs.prune import apply_prune, plan_prune
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import make_donor

MINE = "checker@ours.example.test"
DONOR = "donor.example.test"
PAGE = f"https://{DONOR}/best-crm-tools-2026/"
ANCHOR = "best CRM for small teams"
PROBE = "probe-advertiser.invalid"


@pytest.fixture(autouse=True)
def _allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    """Предохранитель Этапа 2 — общий список со своим ящиком."""
    monkeypatch.setattr(outreach_cfg, "ALLOWED_RECIPIENTS", (MINE,))
    for stage in Stage:
        monkeypatch.delenv(f"OUTREACH_{stage.value.upper()}_ALLOWED_RECIPIENTS", raising=False)
        monkeypatch.delenv(f"OUTREACH_{stage.value.upper()}_SENDGRID_API_KEY", raising=False)


class FakeRewriter:
    """Модель, которая переписывает приветствие и больше ничего."""

    async def rewrite(self, rendered: object, about: Personalization) -> RewriteResult:
        return RewriteResult(zones={"greeting": "Good afternoon,"}, tokens_spent=0)


async def _count(session: AsyncSession, model: type) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def _donor(session: AsyncSession, host: str = DONOR, *, days_ago: int | None = 10) -> None:
    """Донор с ценой, полученной `days_ago` дней назад. `None` — цены нет."""
    domain = await make_donor(session, host)
    if days_ago is not None:
        await session.execute(
            update(DonorModel)
            .where(DonorModel.domain_id == domain.id)
            .values(
                last_price=Decimal("150"),
                last_price_currency="USD",
                last_price_at=datetime.now(UTC) - timedelta(days=days_ago),
            )
        )


async def _crawl(session: AsyncSession, host: str = DONOR) -> CrawlRunModel:
    run = CrawlRunModel(host=host, outcome=CrawlOutcome.OK, stop_reason=StopReason.EXHAUSTED)
    session.add(run)
    await session.flush()
    return run


def _candidate(
    run: CrawlRunModel, target: str, points: int, page: str | None, anchor: str | None
) -> CandidateModel:
    return CandidateModel(
        crawl_run_id=run.id,
        donor_host=run.host,
        target_root=target,
        points=points,
        verdict=Verdict.BOUGHT,
        best_page_url=page,
        best_anchor=anchor,
    )


def _outlink(run: CrawlRunModel, anchor: str, *, in_body: bool) -> OutLinkModel:
    return OutLinkModel(
        crawl_run_id=run.id,
        page_url=f"https://{run.host}/post-{len(anchor)}",
        url="https://brand.example.test/offer",
        target_host="brand.example.test",
        target_root="brand.example.test",
        anchor=anchor,
        anchor_key=anchor.lower(),
        in_body=in_body,
    )


async def _crawled(session: AsyncSession) -> CrawlRunModel:
    """Донор со свежей ценой и обходом: два кандидата, лучший — по баллу."""
    await _donor(session)
    run = await _crawl(session)
    session.add_all(
        [
            _candidate(run, "weak.example.test", 3, f"https://{DONOR}/old", "old anchor"),
            _candidate(run, "brand.example.test", 7, f" {PAGE} ", f" {ANCHOR} "),
            _candidate(run, "nolink.example.test", 9, None, None),
        ]
    )
    await session.flush()
    return run


async def _real_advertiser(session: AsyncSession, points: int = 9) -> DomainModel:
    """Настоящий рекламодатель, готовый к офферу: ссылка на доноре и адрес."""
    domain = DomainModel(host="real-brand.example.test")
    session.add(domain)
    await session.flush()
    session.add(
        AdvertiserModel(
            domain_id=domain.id,
            points=points,
            donors=3,
            links=1,
            best_donor_host=DONOR,
            best_page_url=PAGE,
            best_anchor=ANCHOR,
        )
    )
    session.add(
        ContactModel(
            domain_id=domain.id,
            email="marketing@real-brand.example.test",
            source=ContactSource.PAGE,
        )
    )
    await session.flush()
    return domain


async def _probe(
    session: AsyncSession, *, host: str = PROBE, email: str = MINE, donor: str = DONOR
) -> ProbeAdvertiser:
    return await make_probe_advertiser(session, host=host, email=email, donor=donor, author="тест")


async def _offers(session: AsyncSession, limit: int = 1) -> list[tuple[str, str | None]]:
    """Собрать офферы, как экран «Письма → Рекламодателям», — кому и на какой адрес."""
    builder = QueueBuilder(session, FakeRewriter())  # type: ignore[arg-type]
    await builder.build(
        BuildRequest(campaign_name="Проверка оффера", stage=Stage.ADVERTISERS, limit=limit)
    )
    await session.flush()
    rows = await session.execute(
        select(DomainModel.host, ContactModel.email)
        .join(MessageModel, MessageModel.domain_id == DomainModel.id)
        .outerjoin(ContactModel, ContactModel.id == MessageModel.contact_id)
        .order_by(MessageModel.id)
    )
    return [(host, email) for host, email in rows.all()]


# --- заведение -------------------------------------------------------------------------------


async def test_probe_gets_the_best_link_of_the_crawl_and_tops_the_build(
    session: AsyncSession, filled_legal: None
) -> None:
    run = await _crawled(session)
    await _real_advertiser(session, points=9)

    probe = await make_probe_advertiser(
        session,
        host=" Probe-Advertiser.INVALID",
        email=f" {MINE.upper()}",
        donor=f"https://www.{DONOR}/x",
        author="тест",
    )

    assert (probe.host, probe.email, probe.donor_host, probe.created) == (PROBE, MINE, DONOR, True)
    assert (probe.link.crawl_id, probe.link.page_url, probe.link.anchor) == (run.id, PAGE, ANCHOR)
    assert probe.link.target == "brand.example.test"
    row = await session.scalar(
        select(AdvertiserModel).where(AdvertiserModel.id == probe.advertiser_id)
    )
    assert row is not None
    assert (row.points, row.source, row.contact_status) == (10, "links", ContactStatus.FOUND)
    assert (row.best_donor_host, row.best_page_url, row.best_anchor) == (DONOR, PAGE, ANCHOR)
    journal = await session.scalar(
        select(AuditLogModel.details).where(AuditLogModel.action == AuditAction.PROBE_CREATED)
    )
    assert journal == {
        "домен": PROBE,
        "адрес": MINE,
        "донор": DONOR,
        "страница": PAGE,
        "анкор": ANCHOR,
        "обход": run.id,
        "кто": "тест",
    }
    # Сборка с лимитом 1 берёт пробного, а не настоящего с баллом ниже.
    assert await _offers(session) == [(PROBE, MINE)]


async def test_the_offer_goes_to_own_inbox_from_a_stage_two_box(
    session: AsyncSession, filled_legal: None
) -> None:
    await _crawled(session)
    await _probe(session)
    await _offers(session)
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
    letter = await session.scalar(select(MessageModel.id))
    assert letter is not None

    sent = await Sending(session, NullTransport()).send(letter)

    assert sent.sender_email == "max@offers.example.test"
    body = await session.scalar(select(MessageModel.body).where(MessageModel.id == letter))
    assert body is not None
    assert PAGE in body
    assert ANCHOR in body


async def test_without_candidates_a_crawl_link_from_the_article_body_is_taken(
    session: AsyncSession,
) -> None:
    await _donor(session)
    old = await _crawl(session)
    session.add(_outlink(old, "older crawl", in_body=True))
    run = await _crawl(session)
    session.add_all(
        [
            _outlink(run, "  ", in_body=True),
            _outlink(run, "sidebar brand", in_body=False),
            _outlink(run, "brand in text", in_body=True),
        ]
    )
    await session.flush()

    probe = await _probe(session)

    assert (probe.link.crawl_id, probe.link.anchor) == (run.id, "brand in text")


async def test_a_second_call_doubles_nothing_and_moves_the_link(session: AsyncSession) -> None:
    await _crawled(session)
    other = "other-donor.example.test"
    await _donor(session, other)
    later = await _crawl(session, other)
    session.add(_candidate(later, "shop.example.test", 4, f"https://{other}/p", "shop"))
    await session.flush()
    first = await _probe(session)

    again = await _probe(session, donor=other)

    assert (first.created, again.created) == (True, False)
    assert again.advertiser_id == first.advertiser_id
    assert (await _count(session, AdvertiserModel), await _count(session, ContactModel)) == (1, 1)
    row = await session.scalar(select(AdvertiserModel.best_donor_host))
    assert row == other


# --- отказы до заведения -----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("changed", "says"),
    [
        ({"host": "brand.example.test"}, "не липовый домен"),
        ({"email": "not-an-address"}, "не адрес почты"),
        ({"email": "stranger@elsewhere.example.test"}, "нет в списке разрешённых"),
        ({"donor": "???"}, "не домен донора"),
        ({"donor": "unknown.example.test"}, "Донора unknown.example.test в базе нет"),
        ({"donor": "noprice.example.test"}, "цены нет"),
        ({"donor": "stale.example.test"}, "старше 150 дней"),
        ({"donor": "uncrawled.example.test"}, "не обходили"),
        ({"donor": "bare.example.test"}, "не нашёл ни одной ссылки с анкором"),
    ],
)
async def test_refusals_name_the_reason_and_leave_nothing(
    session: AsyncSession, changed: dict[str, str], says: str
) -> None:
    await _crawled(session)
    await _donor(session, "noprice.example.test", days_ago=None)
    await _donor(session, "stale.example.test", days_ago=151)
    await _donor(session, "uncrawled.example.test")
    await _donor(session, "bare.example.test")
    bare = await _crawl(session, "bare.example.test")
    session.add(_candidate(bare, "x.example.test", 5, "https://bare.example.test/p", " "))
    session.add(_outlink(bare, "", in_body=True))
    await session.flush()

    with pytest.raises(ProbeError, match=says):
        await _probe(session, **changed)

    assert await _count(session, AdvertiserModel) == 0


async def test_without_the_safety_list_there_is_no_probe(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Пробный рекламодатель — только на ящик из списка: без списка опечатка в адресе
    отправила бы проверочный оффер живому человеку."""
    await _crawled(session)
    monkeypatch.setattr(outreach_cfg, "ALLOWED_RECIPIENTS", ())

    with pytest.raises(ProbeError, match="Предохранитель снят"):
        await _probe(session)


async def test_a_list_of_the_stage_is_the_one_that_counts(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Свой список Этапа 2 сильнее общего: отправку оффера судит он."""
    await _crawled(session)
    monkeypatch.setenv("OUTREACH_ADVERTISERS_ALLOWED_RECIPIENTS", "stage2@ours.example.test")

    with pytest.raises(ProbeError, match="OUTREACH_ADVERTISERS_ALLOWED_RECIPIENTS"):
        await _probe(session)
    probe = await _probe(session, email="stage2@ours.example.test")
    assert probe.email == "stage2@ours.example.test"


# --- отказ сборки: заведённое откатывается -----------------------------------------------------


async def test_a_probe_already_written_is_refused_and_keeps_its_link(
    session: AsyncSession, filled_legal: None
) -> None:
    await _crawled(session)
    other = "other-donor.example.test"
    await _donor(session, other)
    session.add(_outlink(await _crawl(session, other), "elsewhere", in_body=True))
    await _probe(session)
    await _offers(session)

    with pytest.raises(ProbeError, match="Пробному уже писали"):
        await _probe(session, donor=other)

    row = await session.scalar(select(AdvertiserModel.best_donor_host))
    assert row == DONOR


async def test_a_suppressed_inbox_is_refused(session: AsyncSession) -> None:
    await _crawled(session)
    session.add(SuppressionModel(email=MINE, reason=SuppressionReason.UNSUBSCRIBED))
    await session.flush()

    with pytest.raises(ProbeError, match="в стоп-листе"):
        await _probe(session)

    assert await _count(session, AdvertiserModel) == 0
    assert await session.scalar(select(DomainModel.id).where(DomainModel.host == PROBE)) is None


async def test_when_the_build_takes_another_the_refusal_says_so(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _crawled(session)

    async def nobody(self: Recipients, *, limit: int) -> list[object]:
        return []

    monkeypatch.setattr(Recipients, "advertiser_candidates", nobody)

    with pytest.raises(ProbeError, match="первым берёт не пробного"):
        await _probe(session)


# --- чистка ------------------------------------------------------------------------------------


async def test_prune_probes_takes_the_probe_advertiser_whole(
    session: AsyncSession, filled_legal: None
) -> None:
    await _crawled(session)
    real = await _real_advertiser(session, points=2)
    probe = await _probe(session)
    await _offers(session)
    letter = await session.scalar(select(MessageModel))
    assert letter is not None
    session.add(
        ReplyModel(
            thread_id=letter.thread_id,
            message_id=letter.id,
            kind=ReplyKind.HUMAN,
            raw_body="Interested, tell me more.",
            from_email=MINE,
        )
    )
    await session.flush()

    plan = await plan_prune(session, run_ids=[], probes=True)
    assert plan.probes is not None
    assert (plan.probes.domains, plan.probes.advertisers) == ([probe.domain_id], 1)
    assert (plan.probes.letters, plan.probes.threads, plan.probes.replies) == (1, 1, 1)
    await apply_prune(session, plan, author="тест")
    await session.flush()

    advertisers = await session.scalars(select(AdvertiserModel.domain_id))
    assert list(advertisers.all()) == [real.id]
    assert await session.scalar(select(DomainModel.id).where(DomainModel.host == PROBE)) is None
    assert (await _count(session, MessageModel), await _count(session, ReplyModel)) == (0, 0)
    assert await _count(session, CampaignModel) == 0
    assert (await _count(session, CrawlRunModel), await _count(session, CandidateModel)) == (1, 3)


# --- консоль -----------------------------------------------------------------------------------


async def test_console_tells_the_next_steps_and_refuses_in_words(
    session: AsyncSession, capsys: pytest.CaptureFixture[str]
) -> None:
    await _crawled(session)

    code = await run_probe_advertiser(
        session,
        build_parser().parse_args(["probe-advertiser", "--email", MINE, "--donor", DONOR]),
    )
    out = capsys.readouterr().out
    assert code == EXIT_OK
    assert f"Пробный рекламодатель {PROBE}" in out
    assert f"анкор «{ANCHOR}»" in out
    assert "--stage advertisers --limit 1" in out
    assert "outreach prune --probes" in out

    code = await run_probe_advertiser(
        session,
        build_parser().parse_args(
            ["probe-advertiser", "--email", MINE, "--donor", DONOR, "--host", "x.com"]
        ),
    )
    assert code == EXIT_REFUSED
    assert "Пробный рекламодатель не заведён: «x.com» — не липовый домен" in capsys.readouterr().out
