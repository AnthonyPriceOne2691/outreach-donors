"""Кандидаты донора — по его последнему пригодному обходу, а не сумма всех.

Обход стал кнопкой, и у донора их бывает несколько. Здесь проверяется
то, что по одному обходу не видно: новый заменяет кандидатов прежнего
и переносит решения человека; закрывшийся и незаконченный не заменяют
ничего; прежний, пересчитанный после нового, не возвращает старое.
"""

from __future__ import annotations

import pytest
from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason, Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.gate import donors_per_root, judge_run, latest_crawls
from backend.features.crawl.progress import Batch, Checkpoint
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.repository import queue_crawl, save_batch, save_crawl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.test_crawl_gate import _link

pytestmark = pytest.mark.asyncio

DONOR = "donor-latest.com"


async def _crawl(
    session: AsyncSession, roots: list[str], *, outcome: CrawlOutcome = CrawlOutcome.OK
) -> int:
    links = [_link(DONOR, root, page=n) for root in roots for n in range(4)]
    report = CrawlReport(
        host=DONOR,
        outcome=outcome,
        stop_reason=StopReason.EXHAUSTED,
        pages=[link.page_url for link in links] if outcome is not CrawlOutcome.BLOCKED else [],
        links=links,
        articles=len(links),
    )
    run = await save_crawl(session, report)
    await session.flush()
    return run.id


async def _candidates(session: AsyncSession) -> dict[str, CandidateModel]:
    rows = (await session.execute(select(CandidateModel))).scalars().all()
    return {row.target_root: row for row in rows}


async def test_new_crawl_replaces_candidates_and_keeps_the_decision(
    session: AsyncSession,
) -> None:
    old = await _crawl(session, ["kept.com", "gone.com"])
    await judge_run(session, old)
    kept = (await _candidates(session))["kept.com"]
    kept.confirmed, kept.decided_by = False, "op@t.test"
    await session.flush()

    new = await _crawl(session, ["kept.com", "fresh.com"])
    await judge_run(session, new)

    rows = await _candidates(session)
    assert sorted(rows) == ["fresh.com", "kept.com"]
    assert {row.crawl_run_id for row in rows.values()} == {new}
    assert (rows["kept.com"].confirmed, rows["kept.com"].decided_by) == (False, "op@t.test")


async def test_older_crawl_judged_again_does_not_bring_the_past_back(
    session: AsyncSession,
) -> None:
    old = await _crawl(session, ["gone.com"])
    new = await _crawl(session, ["fresh.com"])
    await judge_run(session, new)

    again = await judge_run(session, old)

    assert again == []
    assert sorted(await _candidates(session)) == ["fresh.com"]


async def test_closed_or_unfinished_crawl_replaces_nothing(session: AsyncSession) -> None:
    good = await _crawl(session, ["kept.com"])
    await judge_run(session, good)
    blocked = await _crawl(session, [], outcome=CrawlOutcome.BLOCKED)
    running = await queue_crawl(session, "other-donor.com", by=None)
    await save_batch(
        session,
        running.id,
        Batch(links=[_link("other-donor.com", "kept.com")], checkpoint=Checkpoint("sitemap")),
    )

    assert await judge_run(session, blocked) == []
    assert await judge_run(session, running.id) == []
    assert sorted(await _candidates(session)) == ["kept.com"]
    # Незаконченный обход не делает домен «встреченным у двух доноров».
    assert await donors_per_root(session, ["kept.com"]) == {"kept.com": 1}
    assert [run.id for run in await latest_crawls(session)] == [good]
    assert running.status is CrawlStatus.RUNNING


async def test_latest_is_per_donor(session: AsyncSession) -> None:
    first = await _crawl(session, ["a.com"])
    second = await _crawl(session, ["b.com"])
    other = await save_crawl(
        session,
        CrawlReport(
            host="another.com",
            outcome=CrawlOutcome.PARTIAL,
            stop_reason=StopReason.TIMEOUT,
            pages=["https://another.com/"],
            links=[_link("another.com", "c.com")],
        ),
    )

    latest = [run.id for run in await latest_crawls(session)]

    assert first not in latest
    assert latest == [second, other.id]
    for run_id in latest:
        await judge_run(session, run_id)
    verdicts = {row.target_root: row.verdict for row in (await _candidates(session)).values()}
    assert set(verdicts) == {"b.com", "c.com"}
    assert verdicts["b.com"] in set(Verdict)
