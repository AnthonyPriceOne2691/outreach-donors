"""Рекламодатели в базе: дедуп, стоп-листы и очередь на контакт.

Главное здесь — правило требования «один рекламодатель — одно письмо,
сколько бы страниц он ни занимал». Оно выражено уникальностью домена
в таблице, а не бережностью кода, и проверяется на настоящей базе:
на моделях такое ограничение выглядит верным ровно до первой вставки.

Второе — порядок отсева. Стоп-листы работают **до** поиска контакта,
потому что платная ступень лестницы стоит денег, и тратить их на того,
кому не напишем, незачем.
"""

from __future__ import annotations

import pytest
from backend.features.contacts.ladder import LadderResult
from backend.features.contacts.quality import Candidate as ContactCandidate
from backend.features.core.domain import (
    ContactSource,
    ContactStatus,
    CrawlOutcome,
    StopReason,
    SuppressionReason,
    Verdict,
)
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import AdvertiserModel, SupplierDonorModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel
from backend.features.core.models.ops import SuppressionModel
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.crawl.promote import promote
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


async def _run(session: AsyncSession, host: str) -> int:
    run = CrawlRunModel(
        host=host,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages_opened=10,
        articles=8,
    )
    session.add(run)
    await session.flush()
    return run.id


async def _candidate(
    session: AsyncSession,
    *,
    donor: str,
    target: str,
    points: int = 6,
    verdict: Verdict = Verdict.BOUGHT,
    confirmed: bool | None = None,
    pages: int = 4,
) -> CandidateModel:
    run_id = await _run(session, donor)
    row = CandidateModel(
        crawl_run_id=run_id,
        donor_host=donor,
        target_root=target,
        points=points,
        verdict=verdict,
        reasons=["коммерческий анкор под nofollow +2"],
        links=pages,
        pages=pages,
        best_page_url=f"https://{donor}/post/1",
        best_anchor="Bet now",
        confirmed=confirmed,
    )
    session.add(row)
    await session.flush()
    return row


async def _hosts(session: AsyncSession) -> list[str]:
    rows = await session.execute(
        select(DomainModel.host).join(AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id)
    )
    return sorted(rows.scalars().all())


class TestDeduplication:
    async def test_one_advertiser_one_row_across_donors(self, session: AsyncSession) -> None:
        """Требование: одно письмо на рекламодателя, сколько бы страниц
        он ни занимал. Здесь он занимает страницы у двух доноров."""
        await _candidate(session, donor="one.example.test", target="advertiser.com", points=6)
        await _candidate(session, donor="two.example.test", target="advertiser.com", points=8)

        report = await promote(session)
        await session.commit()

        assert await _hosts(session) == ["advertiser.com"]
        assert report.promoted == 1

    async def test_the_best_link_wins_and_it_is_the_one_we_write_about(
        self, session: AsyncSession
    ) -> None:
        """Письмо пишется под конкретную найденную ссылку — значит
        сохранить надо самую доказательную, а не первую попавшуюся."""
        await _candidate(session, donor="weak.example.test", target="ad.com", points=4, pages=1)
        await _candidate(session, donor="strong.example.test", target="ad.com", points=9, pages=7)

        await promote(session)
        await session.commit()

        row = (await session.execute(select(AdvertiserModel))).scalars().one()
        assert row.points == 9
        assert row.best_donor_host == "strong.example.test"
        assert row.best_page_url == "https://strong.example.test/post/1"
        assert row.donors == 2

    async def test_second_promotion_updates_instead_of_adding(self, session: AsyncSession) -> None:
        """Скоринг будет меняться, а письмо рекламодателю одно."""
        candidate = await _candidate(session, donor="one.example.test", target="ad.com", points=5)
        await promote(session)
        candidate.points = 11
        await session.flush()

        report = await promote(session)
        await session.commit()

        rows = (await session.execute(select(AdvertiserModel))).scalars().all()
        assert len(rows) == 1
        assert rows[0].points == 11
        assert report.updated == 1


class TestWhoGetsIn:
    async def test_human_confirmation_beats_a_low_verdict(self, session: AsyncSession) -> None:
        await _candidate(
            session,
            donor="one.example.test",
            target="ad.com",
            points=3,
            verdict=Verdict.PENDING,
            confirmed=True,
        )

        await promote(session)
        await session.commit()

        row = (await session.execute(select(AdvertiserModel))).scalars().one()
        assert row.confirmed_by_human is True

    async def test_human_rejection_beats_a_high_score(self, session: AsyncSession) -> None:
        """Решение человека сильнее вердикта скоринга — иначе ручная
        проверка не имеет смысла."""
        await _candidate(
            session,
            donor="one.example.test",
            target="ad.com",
            points=11,
            verdict=Verdict.BOUGHT,
            confirmed=False,
        )

        await promote(session)
        await session.commit()

        assert await _hosts(session) == []

    async def test_borderline_without_a_decision_waits(self, session: AsyncSession) -> None:
        await _candidate(
            session, donor="one.example.test", target="ad.com", points=3, verdict=Verdict.PENDING
        )

        await promote(session)
        await session.commit()

        assert await _hosts(session) == []


class TestStopLists:
    async def test_supplier_donor_advertisers_are_not_touched(self, session: AsyncSession) -> None:
        """Площадка, где агентство уже размещалось: её рекламодатели —
        чужие клиенты и свои же размещения."""
        session.add(SupplierDonorModel(host="partner.example.test", note="партнёр"))
        await _candidate(session, donor="partner.example.test", target="ad.com")

        report = await promote(session)
        await session.commit()

        assert await _hosts(session) == []
        assert report.skipped_supplier == 1

    async def test_common_stop_list_covers_both_stages(self, session: AsyncSession) -> None:
        """Один и тот же адресат не должен получить письмо и как донор,
        и как рекламодатель."""
        domain = DomainModel(host="ad.com")
        session.add(domain)
        await session.flush()
        session.add(SuppressionModel(domain_id=domain.id, reason=SuppressionReason.UNSUBSCRIBED))
        await _candidate(session, donor="one.example.test", target="ad.com")

        report = await promote(session)
        await session.commit()

        assert await _hosts(session) == []
        assert report.skipped_suppressed == 1

    async def test_the_report_names_the_reason(self, session: AsyncSession) -> None:
        """«Ни одного рекламодателя» и «все отсеяны стоп-листом» —
        разные новости, и вторая означает, что список стоит перечитать."""
        session.add(SupplierDonorModel(host="partner.example.test"))
        await _candidate(session, donor="partner.example.test", target="ad.com")

        report = await promote(session)

        assert report.as_dict()["донор в стоп-листе поставщиков"] == 1
        assert report.as_dict()["заведено"] == 0


class TestContactQueue:
    async def test_advertisers_without_an_address_are_queued_by_score(
        self, session: AsyncSession
    ) -> None:
        """Балл первым: если бюджет платной ступени кончится на середине,
        он кончится на самых убедительных."""
        await _candidate(session, donor="one.example.test", target="weak.com", points=4)
        await _candidate(session, donor="one.example.test", target="strong.com", points=12)
        await promote(session)
        await session.commit()

        hosts = await AdvertiserContactRepository(session).pending_hosts(limit=10)

        assert hosts == ["strong.com", "weak.com"]

    async def test_an_address_found_earlier_is_not_looked_for_again(
        self, session: AsyncSession
    ) -> None:
        """Контакт принадлежит домену, а не роли: сайт, у которого адрес
        уже нашли донором, приходит сюда с готовым адресом."""
        await _candidate(session, donor="one.example.test", target="known.com")
        await promote(session)
        await session.flush()

        domain = (
            await session.execute(select(DomainModel).where(DomainModel.host == "known.com"))
        ).scalar_one()
        session.add(
            ContactModel(domain_id=domain.id, email="ads@known.com", source=ContactSource.PAGE)
        )
        await session.commit()

        queue = AdvertiserContactRepository(session)
        assert await queue.pending_hosts(limit=10) == []
        assert await queue.pending_count() == 0

    async def test_outcome_is_written_down_so_we_do_not_pay_twice(
        self, session: AsyncSession
    ) -> None:
        """Без отметки времени «не нашли» и «ещё не искали» выглядят
        одинаково, и повторный проход платит за уже пройденное."""
        await _candidate(session, donor="one.example.test", target="ad.com")
        await promote(session)
        await session.commit()
        queue = AdvertiserContactRepository(session)

        saved = await queue.save(
            [LadderResult(host="ad.com", status=ContactStatus.NOT_FOUND, contact=None)]
        )
        await session.commit()

        row = (await session.execute(select(AdvertiserModel))).scalars().one()
        assert saved == 0
        assert row.contact_status is ContactStatus.NOT_FOUND
        assert row.contact_attempted_at is not None
        assert await queue.pending_hosts(limit=10) == []

    async def test_a_found_address_lands_on_the_domain(self, session: AsyncSession) -> None:
        await _candidate(session, donor="one.example.test", target="ad.com")
        await promote(session)
        await session.commit()

        saved = await AdvertiserContactRepository(session).save(
            [
                LadderResult(
                    host="ad.com",
                    status=ContactStatus.FOUND,
                    contact=ContactCandidate(email="ads@ad.com", source=ContactSource.PAGE),
                )
            ]
        )
        await session.commit()

        contact = (await session.execute(select(ContactModel))).scalars().one()
        assert saved == 1
        assert contact.email == "ads@ad.com"


class TestStopListAddedLater:
    """Стоп-лист пополняют задним числом: донор становится партнёром
    уже после того, как его рекламодатели заведены. Найдено живым
    прогоном — отсев молчал про тех, кто попал в базу раньше него.
    """

    async def test_advertiser_is_removed_when_its_donor_becomes_a_supplier(
        self, session: AsyncSession
    ) -> None:
        await _candidate(session, donor="partner.example.test", target="ad.com")
        await promote(session)
        await session.commit()
        assert await _hosts(session) == ["ad.com"]

        session.add(SupplierDonorModel(host="partner.example.test", note="стали партнёром"))
        await session.flush()
        report = await promote(session)
        await session.commit()

        assert await _hosts(session) == []
        assert report.removed == 1

    async def test_an_advertiser_nobody_scored_this_pass_stays(self, session: AsyncSession) -> None:
        """Отсутствие домена среди кандидатов значит «про него сейчас
        ничего не считали», а не «он больше не рекламодатель». Стереть
        по отсутствию значило бы терять заведённых руками."""
        await _candidate(session, donor="one.example.test", target="ad.com")
        await promote(session)
        await session.commit()

        for row in (await session.execute(select(CandidateModel))).scalars().all():
            await session.delete(row)
        await session.flush()
        report = await promote(session)
        await session.commit()

        assert await _hosts(session) == ["ad.com"]
        assert report.removed == 0

    async def test_another_donor_keeps_the_advertiser(self, session: AsyncSession) -> None:
        """Рекламодатель остаётся, пока на него ссылается хоть один
        донор не из стоп-листа."""
        await _candidate(session, donor="partner.example.test", target="ad.com", points=9)
        await _candidate(session, donor="clean.example.test", target="ad.com", points=5)
        session.add(SupplierDonorModel(host="partner.example.test"))
        await session.flush()

        report = await promote(session)
        await session.commit()

        assert await _hosts(session) == ["ad.com"]
        assert report.removed == 0
