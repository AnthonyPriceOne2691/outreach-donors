"""«Домены DR > 80 — не пишем»: строка требования, которую не задать списком.

Живой обход финансового донора (06.10.2026) дал два «куплено», и оба —
крупные сайты: маркетплейс с партнёрскими ссылками донора и рекламный
редиректор с `rel=sponsored`. Проверяется не только отсев, но и цена:
DR спрашивается только у «куплено» и «спорно», только один раз,
а отказ провайдера не выдаёт крупный сайт за маленький и наоборот.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest
from backend.features.ahrefs.client import AhrefsError, Response
from backend.features.ahrefs.units import UnitsCost
from backend.features.core.domain import CrawlOutcome, StopReason, Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.big_sites import MAX_DR, ahrefs_ratings, apply_ratings
from backend.features.crawl.denylist import DenyReason
from backend.features.crawl.gate import judge_run
from backend.features.crawl.links import OutLink
from backend.features.crawl.repository import save_crawl
from backend.features.crawl.scoring import Candidate
from backend.features.crawl.walk import CrawlReport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up


def _candidate(root: str, verdict: Verdict = Verdict.BOUGHT) -> Candidate:
    return Candidate(target_root=root, points=5, verdict=verdict, reasons=["rel=sponsored +5"])


class TestApplyRatings:
    def test_big_site_is_blocked_with_the_reason(self) -> None:
        candidate = _candidate("amazon.com")

        assert apply_ratings([candidate], {"amazon.com": 96}) == 1

        assert candidate.verdict is Verdict.BLOCKED
        assert candidate.denial is not None
        assert candidate.denial.reason is DenyReason.BIG_SITE
        assert candidate.reasons[-1] == f"кому не пишем: DR 96 > {MAX_DR}"
        assert candidate.dr == 96

    def test_threshold_itself_is_not_big(self) -> None:
        """Требование говорит «DR > 80»: восемьдесят — ещё пишем."""
        candidate = _candidate("edge.com")

        apply_ratings([candidate], {"edge.com": MAX_DR})

        assert candidate.verdict is Verdict.BOUGHT

    def test_domain_unknown_to_the_provider_is_not_big(self) -> None:
        candidate = _candidate("tiny-shop.com", Verdict.PENDING)

        apply_ratings([candidate], {"tiny-shop.com": None})

        assert candidate.verdict is Verdict.PENDING
        assert candidate.dr is None

    def test_unchecked_rating_is_said_not_hidden(self) -> None:
        """Нет DR — вердикт остаётся, а в причинах так и написано: молча
        пропущенный крупный сайт выглядел бы рекламодателем."""
        candidate = _candidate("unknown.com")

        apply_ratings([candidate], {})

        assert candidate.verdict is Verdict.BOUGHT
        assert "DR не проверен" in candidate.reasons[-1]

    @pytest.mark.parametrize("verdict", [Verdict.SKIPPED, Verdict.BLOCKED])
    def test_nobody_else_needs_a_rating(self, verdict: Verdict) -> None:
        candidate = _candidate("plain.com", verdict)

        apply_ratings([candidate], {"plain.com": 99})

        assert candidate.verdict is verdict
        assert candidate.reasons == ["rel=sponsored +5"]


class _FakeAhrefs:
    """Пакетный запрос: помнит, о чём спросили, отвечает DR из словаря."""

    def __init__(self, known: dict[str, object]) -> None:
        self.known = known
        self.asked: list[list[str]] = []

    async def batch_metrics(self, hosts: Sequence[str], select: Sequence[str]) -> Response:
        self.asked.append(list(hosts))
        rows = [
            {"url": f"https://{host}/", "domain_rating": self.known[host]}
            for host in hosts
            if host in self.known
        ]
        return Response(rows=rows, cost=UnitsCost(actual=50, estimated=50, per_row=2))


@pytest.mark.asyncio
class TestAhrefsRatings:
    async def test_batches_of_a_hundred_and_both_shapes_of_dr(self) -> None:
        roots = [f"site{n}.com" for n in range(150)]
        known: dict[str, object] = {"site0.com": 91.0, "site120.com": {"domain_rating": 12.4}}
        client = _FakeAhrefs(known)

        found = await ahrefs_ratings(client, roots)  # type: ignore[arg-type]

        assert [len(batch) for batch in client.asked] == [100, 50]
        assert found["site0.com"] == 91
        assert found["site120.com"] == 12
        assert found["site7.com"] is None  # провайдер домена не знает


def _sponsored(donor: str, root: str, page: int = 1) -> OutLink:
    return OutLink(
        page_url=f"https://{donor}/post/{page}",
        url=f"https://{root}/offer",
        target_host=root,
        target_root=root,
        anchor="Acme",
        anchor_key="acme",
        nofollow=False,
        sponsored=True,
        ugc=False,
    )


def _plain(donor: str, root: str) -> OutLink:
    return OutLink(
        page_url=f"https://{donor}/post/9",
        url=f"https://{root}/",
        target_host=root,
        target_root=root,
        anchor="a long editorial mention of the source",
        anchor_key="a long editorial mention of the source",
        nofollow=False,
        sponsored=False,
        ugc=False,
    )


async def _run(session: AsyncSession) -> int:
    donor = "donor-big.com"
    links = [_sponsored(donor, "marketplace.com"), _sponsored(donor, "small-brand.com")]
    links.append(_plain(donor, "plain-source.com"))
    report = CrawlReport(
        host=donor,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages=[link.page_url for link in links],
        links=links,
        articles=len(links),
    )
    run = await save_crawl(session, report)
    await session.flush()
    return run.id


class _Ratings:
    def __init__(self, known: dict[str, int | None], *, fail: bool = False) -> None:
        self.known = known
        self.fail = fail
        self.asked: list[list[str]] = []

    async def __call__(self, roots: Sequence[str]) -> dict[str, int | None]:
        self.asked.append(list(roots))
        if self.fail:
            raise AhrefsError("ключ отклонён", permanent=True)
        return {root: self.known.get(root) for root in roots}


async def _rows(session: AsyncSession) -> dict[str, CandidateModel]:
    rows = (await session.execute(select(CandidateModel))).scalars().all()
    return {row.target_root: row for row in rows}


@pytest.mark.asyncio
class TestJudgeRun:
    async def test_only_those_we_would_write_to_are_asked(self, session: AsyncSession) -> None:
        run_id = await _run(session)
        ratings = _Ratings({"marketplace.com": 96, "small-brand.com": 23})

        await judge_run(session, run_id, ratings=ratings)
        await session.commit()

        assert ratings.asked == [["marketplace.com", "small-brand.com"]]
        rows = await _rows(session)
        assert rows["marketplace.com"].verdict is Verdict.BLOCKED
        assert rows["marketplace.com"].dr == 96
        assert rows["small-brand.com"].verdict is Verdict.BOUGHT
        assert rows["small-brand.com"].dr == 23
        assert rows["plain-source.com"].dr_checked_at is None

    async def test_recompute_does_not_pay_twice(self, session: AsyncSession) -> None:
        """Пересчёт весов не должен стоить юнитов: спрошенный DR переносится
        по домену, как решение человека, — и решение тоже остаётся."""
        run_id = await _run(session)
        await judge_run(session, run_id, ratings=_Ratings({"marketplace.com": 96}))
        await session.commit()
        rows = await _rows(session)
        rows["small-brand.com"].confirmed = True
        rows["small-brand.com"].decided_by = "op@team.test"
        rows["small-brand.com"].decided_at = datetime.now(UTC)
        await session.commit()

        again = _Ratings({})
        await judge_run(session, run_id, ratings=again)
        await session.commit()

        assert again.asked == []
        rows = await _rows(session)
        assert rows["marketplace.com"].verdict is Verdict.BLOCKED
        assert rows["small-brand.com"].dr_checked_at is not None
        assert rows["small-brand.com"].dr is None  # спросили — провайдер не знает
        assert rows["small-brand.com"].confirmed is True

    async def test_provider_refusal_keeps_the_verdict_and_says_so(
        self, session: AsyncSession
    ) -> None:
        run_id = await _run(session)

        await judge_run(session, run_id, ratings=_Ratings({}, fail=True))
        await session.commit()

        rows = await _rows(session)
        big = rows["marketplace.com"]
        assert big.verdict is Verdict.BOUGHT
        assert big.dr_checked_at is None, "отказ — не ответ: следующий пересчёт спросит снова"
        assert "DR не проверен" in (big.reasons or [])[-1]

    async def test_no_source_of_ratings_changes_nothing_silently(
        self, session: AsyncSession
    ) -> None:
        run_id = await _run(session)

        await judge_run(session, run_id)
        await session.commit()

        big = (await _rows(session))["marketplace.com"]
        assert big.verdict is Verdict.BOUGHT
        assert "DR не проверен" in (big.reasons or [])[-1]


@pytest.mark.asyncio
async def test_rating_migration_goes_down_and_up(session: AsyncSession) -> None:
    """Ревизия, которую выкатка применит к проду, — вниз и вверх на тестовой базе."""
    connection = await session.connection()
    columns = {"dr", "dr_checked_at"}

    down, up = await connection.run_sync(
        columns_down_and_up,
        "c41f7a2e9b86_candidate_domain_rating.py",
        "advertiser_candidates",
        columns,
    )

    assert (down, up) == (set(), columns)
