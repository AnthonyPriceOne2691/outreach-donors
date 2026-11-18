"""Найденное копится: кандидаты донора — по последнему обходу и цепочке обхода 24/7.

Проверяется то, ради чего объединение (`crawl/gate_union.py`): свежий ночной обход не стирает
кандидатов, найденных частями цепочки, а часть — найденных ночью; страница берётся у самого нового
обхода, где она есть; кандидат помнит обход своей лучшей ссылки; решения человека и память донора
работают, как работали; продающие страницы меряются по ссылкам обхода, а у части — всей цепочки, и
доля продающих страниц цепочки после первых 10 000 решает пометку «мало рекламодателей». И потолок
памяти: судья читает части от новой к старой, пока ссылок не больше потолка, старые — заморожены,
а не потеряны; доля цепочки считается запросом и сходится с правилом в памяти.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from backend.config import crawl_always as always_cfg
from backend.features.core.domain import CrawlOutcome, CrawlStatus, StopReason, Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlDepthModel, CrawlRunModel
from backend.features.crawl import always_depth, gate_union
from backend.features.crawl.gate import SELLING_LINKS, SELLING_PAGES, bought_here, judge_run
from backend.features.crawl.gate import selling_stats as in_memory
from backend.features.crawl.gate_union import gather, runs_of
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
    assert (found.chained, found.frozen, found.loaded) == (True, set(), 3)


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


# --- потолок памяти судьи ---------------------------------------------------------------------------


def _pages(first: int, count: int, target: str) -> list[OutLink]:
    return [_link(target, page) for page in range(first, first + count)]


async def test_the_judge_reads_the_newest_parts_up_to_the_cap(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Судимый и последний обычный — всегда; части — от новой к старой, пока ссылок не больше
    потолка; старше первой не влезшей — заморожены, даже если влезли бы сами."""
    small = await _run(session, _pages(1, 1, "s.test"), part=1)
    second = await _run(session, _pages(10, 3, "b.test"), part=2)
    third = await _run(session, _pages(30, 3, "c.test"), part=3)
    night = await _run(session, _pages(50, 2, "n.test"))
    judged = await _run(session, _pages(60, 2, "d.test"), part=4)
    monkeypatch.setattr(always_cfg, "UNION_LINKS", 8)

    plan = await runs_of(session, judged)
    found = await gather(session, judged)

    assert [number for number, _ in plan.runs] == [judged.id, night.id, third.id]
    assert plan.frozen == {second.id, small.id}, "старше первой не влезшей — все, и малая тоже"
    assert (plan.links, found.loaded) == (7, 7)
    assert "заморожено 2" in caplog.text
    monkeypatch.setattr(always_cfg, "UNION_LINKS", 3)
    tight = await runs_of(session, judged)
    assert [number for number, _ in tight.runs] == [judged.id, night.id], (
        "судимый и последний обычный — сверх потолка, но всегда"
    )


async def test_frozen_candidates_survive_a_recompute_with_their_decision(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await _run(session, _pages(1, 3, "frozen-adv.test"), part=1)
    await judge_run(session, first.id)
    kept = (await _candidates(session))["frozen-adv.test"]
    kept.confirmed, kept.decided_by, kept.decided_at = False, "человек@site.test", NOW
    await session.flush()
    await _run(session, _pages(10, 3, "middle-adv.test"), part=2)
    last = await _run(session, _pages(20, 2, "new-adv.test"), part=3)
    monkeypatch.setattr(always_cfg, "UNION_LINKS", 5)

    await judge_run(session, last.id)

    found = await _candidates(session)
    assert sorted(found) == ["frozen-adv.test", "middle-adv.test", "new-adv.test"]
    frozen = found["frozen-adv.test"]
    assert (frozen.crawl_run_id, frozen.verdict, frozen.links) == (first.id, Verdict.BOUGHT, 3)
    assert (frozen.confirmed, frozen.decided_by) == (False, "человек@site.test")
    depth = await session.scalar(select(CrawlDepthModel).where(CrawlDepthModel.host == DONOR))
    assert depth is None, "строки цепочки нет — писать заморожено некуда"


async def test_a_frozen_target_found_again_is_replaced(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await _run(session, _pages(1, 3, "back-adv.test"), part=1)
    await judge_run(session, first.id)
    old = (await _candidates(session))["back-adv.test"]
    old.confirmed, old.decided_by, old.decided_at = True, "человек@site.test", NOW
    await session.flush()
    session.add(CrawlDepthModel(host=DONOR, pages=6, parts=2))
    last = await _run(session, _pages(10, 2, "back-adv.test"), part=2)
    monkeypatch.setattr(always_cfg, "UNION_LINKS", 2)

    await judge_run(session, last.id)

    rows = await session.scalars(
        select(CandidateModel).where(CandidateModel.target_root == "back-adv.test")
    )
    [row] = rows.all()
    assert (row.crawl_run_id, row.links) == (last.id, 2), "новая строка — по прочитанным ссылкам"
    assert (row.confirmed, row.decided_by) == (True, "человек@site.test"), "решение перенесено"
    depth = await session.scalar(select(CrawlDepthModel).where(CrawlDepthModel.host == DONOR))
    assert depth is not None
    await session.refresh(depth)
    assert depth.frozen_parts == 1


async def test_the_chain_yield_by_query_equals_the_rule_in_memory(session: AsyncSession) -> None:
    """Доля цепочки считается запросом по всем частям, без ссылок в памяти, — и на простых ссылках
    сходится с правилом скоринга (`gate.selling_stats`) по тем же ссылкам."""
    chain = [
        [_link("a.test", 1), _link("a.test", 2), _link("src.test", 2, paid=False)],
        [_link("b.test", 3), _link("src.test", 4, paid=False), _link("src.test", 5, paid=False)],
        [_link("a.test", 6), _link("b.test", 6), _link("src.test", 7, paid=False)],
    ]
    session.add(CrawlDepthModel(host=DONOR, pages=20, parts=3, first_done_at=NOW))
    runs = [await _run(session, links, part=number) for number, links in enumerate(chain, 1)]
    await judge_run(session, runs[-1].id)

    links = [link for part in chain for link in part]
    roots = {"a.test", "b.test"}
    expected = in_memory(links, bought_here(links, roots))
    depth = await session.scalar(select(CrawlDepthModel).where(CrawlDepthModel.host == DONOR))
    assert depth is not None
    await session.refresh(depth)
    await session.refresh(runs[-1])

    assert depth.selling_pages == expected[SELLING_PAGES] == 4
    assert depth.yield_pct == pytest.approx(100 * 4 / 20)
    assert runs[-1].stats is not None
    assert runs[-1].stats[SELLING_LINKS] == expected[SELLING_LINKS] == 5


async def test_gather_never_builds_more_links_than_the_cap(
    session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Сколько ссылок судья держит в памяти — по ссылкам, которые он построил, а не по его счёту."""
    for number in range(1, 6):
        await _run(session, _pages(number * 100, 40, f"adv{number}.test"), part=number)
    judged = await _run(session, _pages(900, 10, "last.test"), part=6)
    built: list[int] = []

    class Counted(OutLink):
        def __init__(self, *values: object) -> None:
            built.append(1)
            super().__init__(*values)  # type: ignore[arg-type]

    monkeypatch.setattr(gate_union, "OutLink", Counted)
    monkeypatch.setattr(always_cfg, "UNION_LINKS", 95)

    found = await gather(session, judged)

    assert len(built) == found.loaded == 90 <= always_cfg.UNION_LINKS
    assert len(found.frozen) == 3
