"""Бизнесы ниши из выдачи прогона — в кандидаты в рекламодатели.

Решение Anthony 04.10.2026: сайт, который сам продаёт в нише прогона (судья:
«продаёт своё»), — не донор, а кандидат в рекламодатели. Проверяется на
настоящей базе то, чего не видно по зелёному прогону: слово человека главнее
судьи в обе стороны; принятый донор и уже известный рекламодатель не
попадают; повторный сбор не дублирует; до «пишем» человека поиск адреса
бизнес не трогает; сбор в конце прогона пишет итог в его статистику.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from backend.features.core.domain import RunStatus, Stage
from backend.features.core.models.advertisers import AdvertiserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel
from backend.features.crawl import niche
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.donors.verdict import Thresholds
from backend.features.runs.pipeline import RunRequest, execute_run
from backend.features.runs.repository import RunRepository
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_execute_run import GOOD, FakeSerp, T, _ahrefs, _deps, _settings_id


async def _run(session: AsyncSession, hosts: list[str]) -> int:
    settings = await RunRepository(session).create_settings(
        Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = RunModel(
        stage=Stage.DONORS,
        settings_id=settings.id,
        status=RunStatus.DONE,
        keywords=["sports betting"],
        country="de",
        candidates={"hosts": hosts, "found_by": {host: ["sports betting"] for host in hosts}},
    )
    session.add(run)
    await session.flush()
    return run.id


async def _domain(
    session: AsyncSession, host: str, *, judge: str | None = None, human: str | None = None
) -> DomainModel:
    domain = DomainModel(host=host, site_intent=judge, human_intent=human)
    session.add(domain)
    await session.flush()
    return domain


async def _advertisers(session: AsyncSession) -> dict[str, AdvertiserModel]:
    rows = await session.execute(
        select(DomainModel.host, AdvertiserModel).join(
            AdvertiserModel, AdvertiserModel.domain_id == DomainModel.id
        )
    )
    return dict(rows.tuples().all())


class TestWhoIsANicheBusiness:
    async def test_human_word_beats_the_judge_both_ways(self, session: AsyncSession) -> None:
        await _domain(session, "bookie.example.test", judge="sells_own")
        await _domain(session, "blog.example.test", judge="sells_own", human="publisher")
        await _domain(session, "shop.example.test", judge="editorial_ads", human="sells_own")
        await _domain(session, "news.example.test", judge="editorial_ads")
        run_id = await _run(
            session,
            ["bookie.example.test", "blog.example.test", "shop.example.test", "news.example.test"],
        )

        report = await niche.collect(session, run_id)

        found = await _advertisers(session)
        assert set(found) == {"bookie.example.test", "shop.example.test"}
        assert report == niche.NicheReport(run_id=run_id, found=2, added=2)
        bookie = found["bookie.example.test"]
        assert (bookie.source, bookie.found_run_id) == (niche.NICHE, run_id)
        assert bookie.confirmed_by_human is False
        assert bookie.decided_at is None

    async def test_our_donor_and_known_advertiser_stay_as_they_are(
        self, session: AsyncSession
    ) -> None:
        donor = await _domain(session, "ours.example.test", judge="sells_own")
        session.add(DonorModel(domain_id=donor.id, review="accepted"))
        known = await _domain(session, "known.example.test", judge="sells_own")
        session.add(AdvertiserModel(domain_id=known.id, points=4, donors=1, links=2))
        await session.flush()
        run_id = await _run(session, ["ours.example.test", "known.example.test"])

        report = await niche.collect(session, run_id)
        again = await niche.collect(session, run_id)

        found = await _advertisers(session)
        assert set(found) == {"known.example.test"}
        assert found["known.example.test"].source == niche.LINKS  # не перетёрт
        assert (report.found, report.added) == (1, 0)
        assert again.added == 0

    async def test_unknown_run_says_so(self, session: AsyncSession) -> None:
        with pytest.raises(niche.UnknownNicheRunError, match="№999999"):
            await niche.collect(session, 999_999)


class TestHumanDecision:
    async def test_contact_search_waits_for_write(self, session: AsyncSession) -> None:
        await _domain(session, "bookie.example.test", judge="sells_own")
        run_id = await _run(session, ["bookie.example.test"])
        await niche.collect(session, run_id)
        bookie = (await _advertisers(session))["bookie.example.test"]
        queue = AdvertiserContactRepository(session)

        assert await queue.pending_hosts(limit=10) == []  # ещё не решили — адрес не ищем
        await niche.decide(session, bookie.id, write=True, by="anthony@site.test")
        assert await queue.pending_hosts(limit=10) == ["bookie.example.test"]

    async def test_skip_is_remembered(self, session: AsyncSession) -> None:
        await _domain(session, "bookie.example.test", judge="sells_own")
        run_id = await _run(session, ["bookie.example.test"])
        await niche.collect(session, run_id)
        bookie = (await _advertisers(session))["bookie.example.test"]

        decided = await niche.decide(session, bookie.id, write=False, by="anthony@site.test")

        assert decided.confirmed_by_human is False
        assert decided.decided_by == "anthony@site.test"
        assert decided.decided_at is not None
        assert await AdvertiserContactRepository(session).pending_hosts(limit=10) == []

    async def test_only_niche_businesses_are_decided_here(self, session: AsyncSession) -> None:
        linked = await _domain(session, "linked.example.test")
        advertiser = AdvertiserModel(domain_id=linked.id, points=4, donors=1, links=2)
        session.add(advertiser)
        await session.flush()

        with pytest.raises(niche.UnknownNicheAdvertiserError):
            await niche.decide(session, advertiser.id, write=True, by="anthony@site.test")


async def test_run_ends_by_collecting_niche_businesses(session: AsyncSession) -> None:
    """Сбор зовётся в конце прогона, и его итог виден в записи прогона."""
    asked: list[int] = []

    async def collector(run_id: int) -> niche.NicheReport:
        asked.append(run_id)
        return niche.NicheReport(run_id=run_id, found=3, added=2)

    serp = FakeSerp(["https://good.com/a"])
    deps = replace(await _deps(session, serp, _ahrefs({"good.com": GOOD})), niche=collector)

    report = await execute_run(deps, RunRequest(["crm"], "us", T, await _settings_id(session)))

    run = (await session.execute(select(RunModel))).scalar_one()
    assert asked == [run.id]
    assert report.niche == niche.NicheReport(run_id=run.id, found=3, added=2)
    assert run.stats["niche"] == {"found": 3, "added": 2}
