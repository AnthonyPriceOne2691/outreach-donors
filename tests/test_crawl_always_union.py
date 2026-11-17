"""Найденное копится: кандидаты донора — по последнему обходу и всей цепочке обхода 24/7.

Проверяется то, ради чего объединение (`crawl/gate_union.py`): свежий ночной обход не стирает
кандидатов, найденных частями цепочки, а часть — найденных ночью; страница берётся у самого нового
обхода, где она есть; кандидат помнит обход своей лучшей ссылки; решения человека и память донора
работают, как работали; продающие страницы меряются по ссылкам обхода, а у части — всей цепочки, и
доля продающих страниц цепочки после первых 10 000 решает пометку «мало рекламодателей».
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason, Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlDepthModel, CrawlRunModel
from backend.features.crawl import always_depth
from backend.features.crawl.gate import SELLING_LINKS, SELLING_PAGES, judge_run
from backend.features.crawl.gate_union import gather
from backend.features.crawl.links import OutLink
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.repository import save_crawl
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

DONOR = "donor-chain.test"
NOW = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)


def _link(target: str, page: int, *, paid: bool = True) -> OutLink:
    """Ссылка страницы `page` донора: оплаченная (`rel=sponsored`) — «куплено», иначе — мимо."""
    return OutLink(
        page_url=f"https://{DONOR}/post/{page}",
        url=f"https://{target}/offer/{page}",
        target_host=target,
        target_root=target,
        anchor="Claim bonus" if paid else "источник",
        anchor_key="claim bonus" if paid else "источник",
        nofollow=paid,
        sponsored=paid,
        ugc=False,
        in_body=True,
    )


async def _run(
    session: AsyncSession,
    links: list[OutLink],
    *,
    part: int | None = None,
    pages: int | None = None,
    outcome: CrawlOutcome = CrawlOutcome.OK,
) -> CrawlRunModel:
    """Законченный обход донора: часть цепочки 24/7 (`part`) или обычный. `pages` — страниц у строки:
    у части — всей цепочки."""
    opened = list(dict.fromkeys(link.page_url for link in links)) or [f"https://{DONOR}/"]
    run = await save_crawl(
        session,
        CrawlReport(
            host=DONOR,
            outcome=outcome,
            stop_reason=StopReason.MAX_PAGES,
            pages=opened,
            links=links,
            articles=len(opened),
        ),
    )
    run.depth_part = part
    if pages is not None:
        run.pages_opened = pages
    await session.flush()
    return run


async def _candidates(session: AsyncSession) -> dict[str, CandidateModel]:
    rows = await session.scalars(select(CandidateModel).where(CandidateModel.donor_host == DONOR))
    return {row.target_root: row for row in rows.all()}


async def test_the_night_does_not_wipe_what_the_chain_found(session: AsyncSession) -> None:
    part = await _run(session, [_link("chain-adv.test", 1), _link("chain-adv.test", 2)], part=1)
    await judge_run(session, part.id)
    night = await _run(session, [_link("night-adv.test", 50)])

    await judge_run(session, night.id)

    found = await _candidates(session)
    assert sorted(found) == ["chain-adv.test", "night-adv.test"]
    assert found["chain-adv.test"].verdict is Verdict.BOUGHT
    assert found["chain-adv.test"].crawl_run_id == part.id, "кандидат — у обхода лучшей ссылки"
    assert found["night-adv.test"].crawl_run_id == night.id


async def test_a_part_does_not_wipe_what_the_night_found(session: AsyncSession) -> None:
    night = await _run(session, [_link("night-adv.test", 50)])
    await judge_run(session, night.id)
    part = await _run(session, [_link("chain-adv.test", 1)], part=1)

    await judge_run(session, part.id)

    assert sorted(await _candidates(session)) == ["chain-adv.test", "night-adv.test"]


async def test_a_page_is_read_from_the_newest_crawl_that_opened_it(session: AsyncSession) -> None:
    """Страницу открыли и часть, и ночь позже: на ней теперь другой рекламодатель — прежний уходит."""
    part = await _run(session, [_link("gone-adv.test", 7), _link("kept-adv.test", 8)], part=1)
    night = await _run(session, [_link("new-adv.test", 7)])

    found = await gather(session, night)
    await judge_run(session, night.id)

    assert sorted(await _candidates(session)) == ["kept-adv.test", "new-adv.test"]
    assert found.owner[f"https://{DONOR}/post/7"] == night.id
    assert found.owner[f"https://{DONOR}/post/8"] == part.id
    assert [link.target_root for link in found.chain] == ["gone-adv.test", "kept-adv.test"], (
        "цепочка меряется своими ссылками целиком"
    )


async def test_only_the_latest_crawl_outside_the_chain_joins_it(session: AsyncSession) -> None:
    await _run(session, [_link("old-adv.test", 90)])
    newer = await _run(session, [_link("fresh-adv.test", 91)])
    await _run(session, [_link("chain-adv.test", 1)], part=1)
    refused = await _run(session, [], part=2, outcome=CrawlOutcome.BLOCKED)
    going = await _run(session, [_link("half-adv.test", 3)], part=3)
    going.status = CrawlStatus.RUNNING
    await session.flush()

    await judge_run(session, newer.id)
    assert await _candidates(session) == {}, "у донора есть обход новее — судит он"
    await judge_run(session, going.id)
    assert await _candidates(session) == {}, "незаконченная часть не судит"
    latest = await _run(session, [_link("chain-adv.test", 4)], part=4)
    await judge_run(session, latest.id)

    assert sorted(await _candidates(session)) == ["chain-adv.test", "fresh-adv.test"]
    assert refused.id not in {row.crawl_run_id for row in (await _candidates(session)).values()}


async def test_the_decision_of_a_human_survives_the_union(session: AsyncSession) -> None:
    part = await _run(session, [_link("chain-adv.test", 1)], part=1)
    await judge_run(session, part.id)
    row = (await _candidates(session))["chain-adv.test"]
    row.confirmed, row.decided_by, row.decided_at = False, "человек@site.test", NOW
    await session.flush()

    night = await _run(session, [_link("night-adv.test", 50)])
    await judge_run(session, night.id)

    kept = (await _candidates(session))["chain-adv.test"]
    assert (kept.confirmed, kept.decided_by) == (False, "человек@site.test")


async def test_selling_pages_are_the_crawls_own_and_the_parts_are_the_chains(
    session: AsyncSession,
) -> None:
    """Обычный обход меряется своими ссылками, часть — ссылками всей цепочки: её строка говорит о
    цепочке. «Куплено» — по объединению: один вердикт на донора."""
    await _run(session, [_link("chain-adv.test", 1), _link("chain-adv.test", 2)], part=1, pages=4)
    second = await _run(
        session, [_link("chain-adv.test", 3), _link("source.test", 4, paid=False)], part=2, pages=8
    )
    await judge_run(session, second.id)
    night = await _run(session, [_link("chain-adv.test", 50), _link("source.test", 51, paid=False)])
    await judge_run(session, night.id)

    await session.refresh(second)
    await session.refresh(night)
    assert second.stats is not None
    assert night.stats is not None
    assert (second.stats[SELLING_PAGES], second.stats[SELLING_LINKS]) == (3, 3)
    assert (night.stats[SELLING_PAGES], night.stats[SELLING_LINKS]) == (1, 1)


async def test_the_chain_yield_is_set_after_the_first_pass_and_refreshed(
    session: AsyncSession,
) -> None:
    depth = CrawlDepthModel(host=DONOR, pages=4, parts=1)
    session.add(depth)
    first = await _run(session, [_link("chain-adv.test", 1)], part=1, pages=4)
    await judge_run(session, first.id)
    await session.refresh(depth)
    assert (depth.selling_pages, depth.yield_pct) == (1, None), "первые 10 000 не пройдены"

    depth.pages, depth.first_done_at = 10, NOW
    second = await _run(session, [_link("chain-adv.test", 2)], part=2, pages=10)
    await judge_run(session, second.id)
    await session.refresh(depth)

    assert depth.selling_pages == 2
    assert depth.yield_pct == pytest.approx(20.0)
    assert always_depth.weak_of(depth, 25.0) == (True, always_depth.AUTO)
    assert always_depth.weak_of(depth, 20.0) == (False, None), "порог — «меньше», не «не больше»"


async def test_a_mark_by_hand_beats_the_threshold() -> None:
    row = CrawlDepthModel(host=DONOR, pages=10_000, first_done_at=NOW, yield_pct=0.02)

    assert always_depth.weak_of(row, 0.1) == (True, always_depth.AUTO)
    row.weak_override = False
    assert always_depth.weak_of(row, 0.1) == (False, always_depth.HUMAN)
    row.weak_override, row.yield_pct = True, 50.0
    assert always_depth.weak_of(row, 0.1) == (True, always_depth.HUMAN)
    row.weak_override, row.first_done_at = None, None
    assert always_depth.weak_of(row, 99.0) == (False, None), "до первых 10 000 порог молчит"
    assert always_depth.weak_of(None, 0.1) == (False, None)
