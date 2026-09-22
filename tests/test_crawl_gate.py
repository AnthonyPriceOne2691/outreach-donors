"""Гейт против настоящей базы: ссылки обхода становятся кандидатами.

Проверяется то, что по зелёному скорингу не видно: что пересчёт
с новыми весами заменяет вердикт, а не кладёт второй рядом; что решение
человека пересчёт переживает; и что усилитель «домен у двух доноров»
считает доноров, а не строки.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.features.core.domain import CrawlOutcome, StopReason, Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.gate import donors_per_root, judge_run
from backend.features.crawl.links import OutLink
from backend.features.crawl.repository import save_crawl
from backend.features.crawl.walk import CrawlReport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

pytestmark = pytest.mark.asyncio


def _link(donor: str, root: str, *, anchor: str = "Bet now", page: int = 1) -> OutLink:
    return OutLink(
        page_url=f"https://{donor}/post/{page}",
        url=f"https://{root}/offer/{page}",
        target_host=root,
        target_root=root,
        anchor=anchor,
        anchor_key=anchor.lower(),
        nofollow=True,
        sponsored=False,
        ugc=False,
        in_body=False,
    )


async def _crawl(session: AsyncSession, donor: str, links: list[OutLink]) -> int:
    report = CrawlReport(
        host=donor,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages=[link.page_url for link in links] or [f"https://{donor}/"],
        links=links,
        articles=len(links),
    )
    run = await save_crawl(session, report)
    await session.flush()
    return run.id


async def test_candidates_are_written_for_a_run(session: AsyncSession) -> None:
    run_id = await _crawl(
        session,
        "donor-one.com",
        [_link("donor-one.com", "advertiser.com", page=n) for n in range(4)],
    )

    await judge_run(session, run_id)
    await session.commit()

    rows = (await session.execute(select(CandidateModel))).scalars().all()
    assert len(rows) == 1
    assert rows[0].target_root == "advertiser.com"
    assert rows[0].verdict is Verdict.BOUGHT
    assert rows[0].pages == 4
    assert rows[0].best_page_url is not None


async def test_blocked_candidates_are_written_too(session: AsyncSession) -> None:
    """Домен из списка «кому не пишем» и домен с одним баллом — разные
    ответы. Записав только тех, кому пишем, мы получили бы базу,
    по которой скоринг всегда прав."""
    run_id = await _crawl(
        session,
        "donor-two.com",
        [_link("donor-two.com", "facebook.com", anchor="Follow us", page=n) for n in range(3)],
    )

    await judge_run(session, run_id)
    await session.commit()

    row = (await session.execute(select(CandidateModel))).scalars().one()
    assert row.verdict is Verdict.BLOCKED
    assert any("кому не пишем" in reason for reason in row.reasons or [])


async def test_rescoring_replaces_the_verdict_instead_of_adding_one(
    session: AsyncSession,
) -> None:
    """Две строки про один домен с разными баллами — это вопрос «какая
    из них правда», на который никто не ответит."""
    run_id = await _crawl(session, "donor-three.com", [_link("donor-three.com", "ad.com")])

    await judge_run(session, run_id)
    await judge_run(session, run_id)
    await session.commit()

    rows = (await session.execute(select(CandidateModel))).scalars().all()
    assert len(rows) == 1


async def test_human_decision_survives_rescoring(session: AsyncSession) -> None:
    """Подтверждение человека сильнее любого веса и теряться при правке
    весов не должно."""
    run_id = await _crawl(session, "donor-four.com", [_link("donor-four.com", "ad.com")])
    await judge_run(session, run_id)
    row = (await session.execute(select(CandidateModel))).scalars().one()
    row.confirmed = True
    row.decided_by = "operator@example.test"
    row.decided_at = datetime.now(UTC)
    await session.flush()

    await judge_run(session, run_id)
    await session.commit()

    again = (await session.execute(select(CandidateModel))).scalars().one()
    assert again.confirmed is True
    assert again.decided_by == "operator@example.test"


async def test_the_boost_counts_donors_not_links(session: AsyncSession) -> None:
    """Сто ссылок с одного донора — это один донор. Усилитель требования
    говорит про двух разных."""
    await _crawl(
        session,
        "donor-five.com",
        [_link("donor-five.com", "shared.com", page=n) for n in range(10)],
    )
    await session.flush()

    seen = await donors_per_root(session, ["shared.com"])
    assert seen == {"shared.com": 1}

    await _crawl(session, "donor-six.com", [_link("donor-six.com", "shared.com")])
    await session.flush()

    seen = await donors_per_root(session, ["shared.com"])
    assert seen == {"shared.com": 2}


async def test_a_crawl_without_links_leaves_no_candidates_but_keeps_the_run(
    session: AsyncSession,
) -> None:
    run_id = await _crawl(session, "donor-seven.com", [])

    candidates = await judge_run(session, run_id)
    await session.commit()

    assert candidates == []
    rows = (await session.execute(select(CandidateModel))).scalars().all()
    assert rows == []
