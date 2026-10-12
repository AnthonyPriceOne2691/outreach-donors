"""«Не пишем» по кандидату — до рекламодателя, сборки и отправки (сверка ТЗ 11.10.2026).

Ошибка, которую закрывает срез: решение ставило кандидату `confirmed=False`, и только.
Рекламодатель, уже заведённый переводом, оставался, а сборка и отправка смотрят на него,
а не на кандидата, — оффер ложному рекламодателю собирался и уходил, в том числе пачкой.

Проверяется весь путь на настоящей базе: перевод → «не пишем» → сборка не собирает,
отправка отказывает, добивка не уходит. Отдельно — домен, на который ссылается второй
наш донор: решение по одной ссылке рекламодателя не снимает, но письмо под снятую
ссылку не уходит.
"""

from __future__ import annotations

import argparse
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import pytest
from backend.cli import advertisers as cli
from backend.config import storage
from backend.features.core.domain import (
    AuditAction,
    ContactSource,
    CrawlOutcome,
    MessageStatus,
    StopReason,
    UserRole,
    Verdict,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel, SupplierDonorModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.outreach import ThreadModel
from backend.features.crawl import review
from backend.features.crawl.niche import NICHE
from backend.features.crawl.promote import Withdrawn, promote
from backend.features.letters.attempts import LINK_WITHDRAWN
from backend.features.letters.followups import send_due
from backend.features.letters.review import skip
from backend.features.letters.sending import NotQueuedError, RemovedAdvertiserError, Sending
from backend.features.letters.transport import NullTransport
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import TEST_DSN, bearer
from tests.test_advertisers_cli import _crawl, _judge
from tests.test_letters_advertisers import (
    ANCHOR,
    DONOR,
    build_offers,
    offers,
    priced_donor,
    stage_two_sender,
)
from tests.test_send_race import committed_sessions

NOW = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
BRAND = "brand.example.test"
OTHER = "other-donor.example.test"
BY = "оператор@site.example.test"


async def _bought(session: AsyncSession, *, donor: str = DONOR, points: int = 6) -> CandidateModel:
    """«Куплено» на `BRAND` с площадки `donor` — со своей страницей и анкором."""
    run = CrawlRunModel(
        host=donor,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages_opened=4,
        articles=4,
    )
    session.add(run)
    await session.flush()
    row = CandidateModel(
        crawl_run_id=run.id,
        donor_host=donor,
        target_root=BRAND,
        points=points,
        verdict=Verdict.BOUGHT,
        reasons=["rel=sponsored +5"],
        links=2,
        pages=2,
        best_page_url=f"https://{donor}/best-crm-tools-2026/",
        best_anchor=ANCHOR,
    )
    session.add(row)
    await session.flush()
    return row


async def _promoted(session: AsyncSession) -> None:
    """Перевод — и адрес рекламодателю, как после поиска адресов."""
    await promote(session)
    domain = await session.scalar(select(DomainModel).where(DomainModel.host == BRAND))
    assert domain is not None
    session.add(ContactModel(domain_id=domain.id, email=f"ads@{BRAND}", source=ContactSource.PAGE))
    await session.flush()


async def _advertiser(session: AsyncSession) -> AdvertiserModel | None:
    return await session.scalar(
        select(AdvertiserModel)
        .join(DomainModel, DomainModel.id == AdvertiserModel.domain_id)
        .where(DomainModel.host == BRAND)
    )


@pytest.fixture
async def token(
    make_user: Callable[..., Awaitable[UserModel]], sign_in: Callable[..., Awaitable[str]]
) -> str:
    await make_user(BY, role=UserRole.OPERATOR)
    return await sign_in(BY)


class TestTheWholePath:
    async def test_rejected_after_promotion_is_neither_built_nor_sent(
        self, session: AsyncSession, client: AsyncClient, token: str, filled_legal: None
    ) -> None:
        """Перевод → письмо в очереди → «Не пишем» с экрана: рекламодатель снят,
        письмо остановлено, вторая сборка ему не собирает, отправка отказывает."""
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)
        await stage_two_sender(session)
        await session.commit()

        response = await client.post(
            f"/api/advertisers/{candidate.id}/decide",
            json={"confirmed": False},
            headers=bearer(token),
        )

        assert response.status_code == 200, response.text
        assert await _advertiser(session) is None
        await session.refresh(letter)
        assert letter.status is MessageStatus.STOPPED
        again = await build_offers(session)
        assert again.prepared == 0  # type: ignore[attr-defined]
        assert len(await offers(session)) == 1
        with pytest.raises(NotQueuedError):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)
        journal = await session.scalar(
            select(AuditLogModel.details).where(
                AuditLogModel.action == AuditAction.ADVERTISER_REVIEWED
            )
        )
        assert journal is not None
        assert journal["решение"] == "не пишем"
        assert journal["рекламодатель снят"] is True
        assert journal["снято писем"] == 1

    async def test_rejected_before_the_build_gets_no_letter(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)

        decided = await review.decide(session, candidate.id, confirmed=False, by=BY)
        report = await build_offers(session)

        assert decided.withdrawn == Withdrawn(removed=True, stopped=0)
        assert report.prepared == 0  # type: ignore[attr-defined]
        assert await offers(session) == []

    async def test_a_letter_left_in_the_queue_is_refused_at_sending(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Второй рубеж — отправка: письмо, оставшееся в очереди (решение записано
        в обход `withdraw`), уходит отказом «рекламодателя сняли», а не оффером."""
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)
        await stage_two_sender(session)
        candidate.confirmed = False
        await session.flush()
        await promote(session)  # снимает рекламодателя по решению, а не по отсутствию

        assert await _advertiser(session) is None
        await session.refresh(letter)
        assert letter.status is MessageStatus.STOPPED
        letter.status = MessageStatus.QUEUED
        await session.flush()
        with pytest.raises(RemovedAdvertiserError, match="«не пишем» человека"):
            await Sending(session, NullTransport(), now=NOW).send(letter.id)

    async def test_rejection_after_the_letter_went_ends_its_chain(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Письмо уже ушло: «не пишем» снимает срок добивки, и цепочка не идёт."""
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)
        await build_offers(session, followup_days=(1, 2))
        (letter,) = await offers(session)
        await stage_two_sender(session)
        await Sending(session, NullTransport(), now=NOW).send(letter.id)
        await session.refresh(letter)
        assert letter.next_action_at is not None

        decided = await review.decide(session, candidate.id, confirmed=False, by=BY)
        await session.refresh(letter)
        report = await send_due(
            session, transport=NullTransport(), limit=5, now=NOW + timedelta(days=2)
        )

        assert decided.withdrawn == Withdrawn(removed=True, stopped=1)
        assert letter.status is MessageStatus.SENT
        assert letter.next_action_at is None
        assert report.sent == 0

    async def test_a_letter_being_sent_is_left_to_its_outcome(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """«Отправляется» — исход неизвестен, и «остановлено» поверх врало бы о письме,
        которое, может быть, ушло. Его решает событие платформы или человек."""
        await priced_donor(session)
        candidate = await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)
        letter.status = MessageStatus.SENDING
        await session.flush()

        decided = await review.decide(session, candidate.id, confirmed=False, by=BY)

        await session.refresh(letter)
        assert decided.withdrawn == Withdrawn(removed=True, stopped=0)
        assert letter.status is MessageStatus.SENDING


class TestAnotherDonorStillLinks:
    async def test_the_letter_is_rebuilt_under_the_other_donors_link(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Решение по кандидату — про его ссылку: второй донор держит рекламодателя,
        и лучшая ссылка пересобирается из него. Письмо под снятую ссылку остановлено
        своей причиной и сборку не держит: она собирает письмо под ссылку второго
        донора — на тот же адрес и в новый диалог (решение 12.10.2026)."""
        await priced_donor(session)
        await priced_donor(session, OTHER)
        strong = await _bought(session, points=9)
        weak = await _bought(session, donor=OTHER, points=5)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)
        old_thread = letter.thread_id
        assert strong.best_page_url is not None
        assert weak.best_page_url is not None
        assert strong.best_page_url in (letter.body or "")

        decided = await review.decide(session, strong.id, confirmed=False, by=BY)
        rebuilt = await build_offers(session)

        advertiser = await _advertiser(session)
        assert advertiser is not None
        assert (advertiser.best_donor_host, advertiser.best_page_url, advertiser.donors) == (
            OTHER,
            weak.best_page_url,
            1,
        )
        assert decided.withdrawn == Withdrawn(removed=False, stopped=1)
        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, LINK_WITHDRAWN)
        assert letter.thread_id is None, "письмо не уходило — диалога с ним не было"
        assert await session.get(ThreadModel, old_thread) is None
        assert rebuilt.prepared == 1  # type: ignore[attr-defined]
        (_, fresh) = await offers(session)
        assert fresh.status is MessageStatus.QUEUED
        assert weak.best_page_url in (fresh.body or "")
        assert strong.best_page_url not in (fresh.body or "")
        assert fresh.contact_id == letter.contact_id, "адрес снятое письмо не занимало"
        assert fresh.idempotency_key == f"advertisers:{BRAND}:0:a2"
        assert fresh.thread_id is not None
        assert fresh.thread_id != old_thread

    async def test_a_letter_stopped_by_a_human_still_holds(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Сборку не держит только письмо со своей причиной. «Не писать» человека
        у письма — решение по адресату: следующая сборка его не воскрешает."""
        await priced_donor(session)
        await _bought(session)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)

        await skip(session, letter)
        again = await build_offers(session)

        await session.refresh(letter)
        assert (letter.status, letter.failure_reason) == (MessageStatus.STOPPED, None)
        assert again.prepared == 0  # type: ignore[attr-defined]

    async def test_a_new_link_later_gets_a_letter(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        """Единственную ссылку сняли — рекламодатель снят вместе с письмом. Новый
        обход нашёл его «купленным» у другого донора — письмо собирается: снятое
        ссылкой письмо адресата не держит."""
        await priced_donor(session)
        await priced_donor(session, OTHER)
        first = await _bought(session)
        await _promoted(session)
        await build_offers(session)
        await review.decide(session, first.id, confirmed=False, by=BY)
        assert await _advertiser(session) is None

        later = await _bought(session, donor=OTHER, points=5)
        await promote(session)
        rebuilt = await build_offers(session)

        assert rebuilt.prepared == 1  # type: ignore[attr-defined]
        assert later.best_page_url is not None
        assert later.best_page_url in ((await offers(session))[-1].body or "")

    async def test_rejecting_the_weaker_link_changes_nothing(
        self, session: AsyncSession, filled_legal: None
    ) -> None:
        await priced_donor(session)
        await priced_donor(session, OTHER)
        await _bought(session, points=9)
        weak = await _bought(session, donor=OTHER, points=5)
        await _promoted(session)
        await build_offers(session)
        (letter,) = await offers(session)

        decided = await review.decide(session, weak.id, confirmed=False, by=BY)

        advertiser = await _advertiser(session)
        assert advertiser is not None
        assert advertiser.best_donor_host == DONOR
        assert decided.withdrawn == Withdrawn()
        await session.refresh(letter)
        assert letter.status is MessageStatus.QUEUED

    async def test_a_supplier_donor_does_not_keep_the_advertiser(
        self, session: AsyncSession
    ) -> None:
        """Оставшийся кандидат — с донора-поставщика: перевод его не взял бы,
        и «не пишем» по второму снимает рекламодателя."""
        strong = await _bought(session, points=9)
        await _bought(session, donor=OTHER, points=5)
        await _promoted(session)
        session.add(SupplierDonorModel(host=OTHER, note="стали партнёром"))
        await session.flush()

        decided = await review.decide(session, strong.id, confirmed=False, by=BY)

        assert decided.withdrawn.removed is True
        assert await _advertiser(session) is None


class TestOldDecisionsAndNiche:
    async def test_promotion_takes_off_those_rejected_before_the_fix(
        self, session: AsyncSession
    ) -> None:
        """«Не пишем», записанное до 11.10.2026, рекламодателя не снимало. Перевод
        снимает таких — по явному решению человека, а не по отсутствию кандидатов."""
        candidate = await _bought(session)
        await promote(session)
        candidate.confirmed = False
        await session.flush()

        first = await promote(session)
        second = await promote(session)

        assert (first.removed, second.removed) == (1, 0)
        assert await _advertiser(session) is None

    async def test_a_niche_business_keeps_its_own_decision(self, session: AsyncSession) -> None:
        """Бизнес ниши — решение человека о нём самом (`niche.decide`): «не пишем»
        по ссылке снимает только ссылку, под которую писал бы оффер по ссылке."""
        domain = DomainModel(host=BRAND)
        session.add(domain)
        await session.flush()
        session.add(
            AdvertiserModel(
                domain_id=domain.id,
                source=NICHE,
                confirmed_by_human=True,
                decided_at=NOW,
                decided_by=BY,
                best_donor_host=DONOR,
                best_page_url=f"https://{DONOR}/best-crm-tools-2026/",
                best_anchor=ANCHOR,
            )
        )
        candidate = await _bought(session)

        decided = await review.decide(session, candidate.id, confirmed=False, by=BY)

        advertiser = await _advertiser(session)
        assert advertiser is not None
        assert (advertiser.source, advertiser.confirmed_by_human) == (NICHE, True)
        assert (advertiser.best_page_url, advertiser.best_anchor) == (None, None)
        assert decided.withdrawn.removed is True


class TestPromotionKeepsTheNicheDecision:
    @pytest.mark.parametrize(("niche_says", "link_decision"), [(True, None), (False, True)])
    async def test_promotion_does_not_rewrite_the_niche_decision(
        self, session: AsyncSession, niche_says: bool, link_decision: bool | None
    ) -> None:
        """Тот же домен — бизнес ниши и рекламодатель по ссылке. Перевод переписывал
        `confirmed_by_human` решением по ссылке: «пишем» бизнесу ниши становилось
        «не пишем», а «не пишем» — «пишем», и письмо ниши уходило без согласия."""
        domain = DomainModel(host=BRAND)
        session.add(domain)
        await session.flush()
        session.add(
            AdvertiserModel(
                domain_id=domain.id,
                source=NICHE,
                confirmed_by_human=niche_says,
                decided_at=NOW,
                decided_by=BY,
            )
        )
        candidate = await _bought(session)
        candidate.confirmed = link_decision
        await session.flush()

        report = await promote(session)

        advertiser = await _advertiser(session)
        assert advertiser is not None
        assert advertiser.confirmed_by_human is niche_says
        assert advertiser.best_donor_host == DONOR, "ссылка ложится, как и раньше"
        assert report.by_human == int(link_decision is True)


class TestConsole:
    @pytest.fixture(autouse=True)
    def _test_base(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(storage, "DSN", TEST_DSN)

    async def test_console_decision_takes_the_advertiser_off_too(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Консоль решает тем же путём, что экран: до среза она писала решение
        кандидату сама, мимо рекламодателя."""
        async with committed_sessions() as factory:
            run_id = await _crawl(factory)
            await cli.cmd_advertisers(_judge())
            await cli.cmd_advertisers_promote(
                argparse.Namespace(contacts=False, limit=10, no_paid=True)
            )
            no = argparse.Namespace(
                run=run_id, domain="small-brand.example", by=BY, yes=False, no=True
            )

            code = await cli.cmd_advertiser_decide(no)

            async with factory() as session:
                hosts = (
                    await session.scalars(
                        select(DomainModel.host).join(
                            AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id
                        )
                    )
                ).all()

        out = capsys.readouterr().out
        assert code == 0
        assert "small-brand.example" not in hosts
        assert "Рекламодатель снят" in out
