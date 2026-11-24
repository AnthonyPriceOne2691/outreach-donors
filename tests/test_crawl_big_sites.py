"""«Домены DR > 80 — не пишем»: строка требования, которую не задать списком.

Живой обход финансового донора (06.10.2026) дал два «куплено», и оба —
крупные сайты: маркетплейс с партнёрскими ссылками донора и рекламный
редиректор с `rel=sponsored`. Проверяется не только отсев, но и цена:
DR спрашивается только у «куплено» и «спорно», только один раз,
а отказ провайдера не выдаёт крупный сайт за маленький и наоборот.
"""

from __future__ import annotations

import functools
import json
from collections.abc import Sequence
from datetime import UTC, datetime

import httpx
import pytest
from backend.features.ahrefs.client import AhrefsClient, AhrefsError, Response
from backend.features.ahrefs.units import UnitsCost
from backend.features.core.domain import CrawlOutcome, StopReason, Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.crawl.big_sites import (
    MAX_DR,
    MAX_REFUSED_NAMES,
    PartialRatingsError,
    ahrefs_ratings,
    apply_ratings,
    refused_name,
)
from backend.features.crawl.denylist import DenyReason
from backend.features.crawl.gate import judge_run
from backend.features.crawl.links import OutLink
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.repository import save_crawl
from backend.features.crawl.scoring import Candidate
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


async def _plenty() -> int:
    """Кап с запасом: эти тесты — об отсеве, а не о капе (`test_crawl_dr_cap.py`)."""
    return 1_000_000


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

        found = await ahrefs_ratings(client, roots, allowance=_plenty)  # type: ignore[arg-type]

        assert [len(batch) for batch in client.asked] == [100, 50]
        assert found["site0.com"] == 91
        assert found["site120.com"] == 12
        assert found["site7.com"] is None  # провайдер домена не знает


def _refusal(name: str, status: int = 400) -> AhrefsError:
    """Отказ Ahrefs словами боя: клиент кладёт в текст начало тела (`client._raise_for_status`)."""
    said = f"{{ \"error\": \"bad 'targets': invalid domain or url: '{name}'\" }}"
    return AhrefsError(
        f"batch_metrics: Ahrefs ответил {status}: {said}", permanent=True, status=status
    )


class _RefusingAhrefs(_FakeAhrefs):
    """Ahrefs, который отвергает пакет с кривым именем целиком, как на бою, а с запроса
    `broken_from` (счёт с нуля) отвечает сбоем не об имени."""

    def __init__(
        self, known: dict[str, object], crooked: set[str], *, broken_from: int | None = None
    ) -> None:
        super().__init__(known)
        self.crooked = crooked
        self.broken_from = broken_from

    async def batch_metrics(self, hosts: Sequence[str], select: Sequence[str]) -> Response:
        if self.broken_from is not None and len(self.asked) >= self.broken_from:
            self.asked.append(list(hosts))
            raise AhrefsError("batch_metrics: Ahrefs ответил 402: Payment Required", status=402)
        bad = next((host for host in hosts if host in self.crooked), None)
        if bad is not None:
            self.asked.append(list(hosts))
            raise _refusal(bad)
        return await super().batch_metrics(hosts, select)


@pytest.mark.asyncio
class TestRefusedNames:
    """Кривое имя из чужой ссылки не держит пакет, а оплаченное не выбрасывается (24.11.2026)."""

    async def test_crooked_name_is_dropped_and_the_batch_asked_without_it(self) -> None:
        client = _RefusingAhrefs({"a.com": 10, "b.com": 20}, {"page-review.html"})

        found = await ahrefs_ratings(  # type: ignore[arg-type]
            client, ["a.com", "page-review.html", "b.com"], allowance=_plenty
        )

        assert client.asked == [["a.com", "page-review.html", "b.com"], ["a.com", "b.com"]]
        assert found == {"a.com": 10, "b.com": 20}, "у отвергнутого имени DR не проверен"

    async def test_paid_batches_survive_a_later_failure(self) -> None:
        """До правки сбой второго пакета выбрасывал оплаченный первый, и пересчёт покупал его снова."""
        roots = [f"site{n:03}.com" for n in range(150)]
        client = _RefusingAhrefs({"site000.com": 33}, set(), broken_from=1)

        with pytest.raises(PartialRatingsError) as caught:
            await ahrefs_ratings(client, roots, allowance=_plenty)  # type: ignore[arg-type]

        assert sorted(caught.value.found) == roots[:100]
        assert caught.value.found["site000.com"] == 33
        assert caught.value.status == 402

    async def test_failure_of_the_first_batch_is_the_plain_refusal(self) -> None:
        client = _RefusingAhrefs({}, set(), broken_from=0)

        with pytest.raises(AhrefsError) as caught:
            await ahrefs_ratings(client, ["a.com"], allowance=_plenty)  # type: ignore[arg-type]

        assert not isinstance(caught.value, PartialRatingsError), "оплаченного нет — нечего хранить"

    async def test_too_many_crooked_names_give_the_batch_up(self) -> None:
        crooked = {f"bad{n}.zone-x" for n in range(MAX_REFUSED_NAMES + 1)}
        client = _RefusingAhrefs({"a.com": 10}, crooked)

        with pytest.raises(AhrefsError) as caught:
            await ahrefs_ratings(  # type: ignore[arg-type]
                client, ["a.com", *sorted(crooked)], allowance=_plenty
            )

        assert caught.value.status == 400
        assert len(client.asked) == MAX_REFUSED_NAMES + 1


def test_only_a_named_member_of_the_batch_is_dropped() -> None:
    """Гадать мы не беремся: имя не из пакета или отказ не 400 — выкидывать нечего."""
    empty = AhrefsError("batch_metrics: Ahrefs ответил 400: {}", status=400)
    assert refused_name(_refusal("a.zone-x"), ["a.zone-x", "b.com"]) == "a.zone-x"
    assert refused_name(_refusal("other.zone-x"), ["a.zone-x", "b.com"]) is None
    assert refused_name(_refusal("a.zone-x", status=422), ["a.zone-x"]) is None
    assert refused_name(empty, ["a.zone-x"]) is None


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

    @pytest.mark.parametrize("status", [402, 410, 301, 501])
    async def test_status_outside_both_client_lists_is_the_same_refusal(
        self, session: AsyncSession, status: int
    ) -> None:
        """Код вне обоих списков клиента уходил голым `HTTPStatusError` мимо
        `except AhrefsError` и ронял пересчёт кандидатов целиком. Клиент
        настоящий, связка — как у задачи обхода."""
        run_id = await _run(session)
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(status)),
            base_url="https://ahrefs.example.test",
        )
        client = AhrefsClient(api_key="k", http=http)

        ratings = functools.partial(ahrefs_ratings, client, allowance=_plenty)
        await judge_run(session, run_id, ratings=ratings)
        await session.commit()
        await client.aclose()

        big = (await _rows(session))["marketplace.com"]
        assert big.verdict is Verdict.BOUGHT
        assert big.dr_checked_at is None
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

    async def test_crooked_name_costs_the_donor_nothing_else(self, session: AsyncSession) -> None:
        """Случай боя 21–24.11.2026 целиком, клиент настоящий: кривое имя среди кандидатов донора.
        До правки отказ ронял весь запрос, и каждый пересчёт платил за тех же соседей снова."""
        roots = ["marketplace.com", "page-review.html", "small-brand.com"]
        run_id = await _donor_run(session, roots)
        seen: list[list[str]] = []
        client = _real_client(_ahrefs_like_prod(seen, crooked=frozenset({"page-review.html"})))
        ratings = functools.partial(ahrefs_ratings, client, allowance=_plenty)

        await judge_run(session, run_id, ratings=ratings)
        await session.commit()

        assert seen == [roots, ["marketplace.com", "small-brand.com"]]
        rows = await _rows(session)
        assert rows["marketplace.com"].dr == 23
        assert rows["small-brand.com"].dr_checked_at is not None
        crooked = rows["page-review.html"]
        assert crooked.verdict is Verdict.BOUGHT
        assert crooked.dr_checked_at is None, "отвергнутое имя к письму не идёт"
        assert "DR не проверен" in (crooked.reasons or [])[-1]

        seen.clear()
        await judge_run(session, run_id, ratings=ratings)
        await session.commit()
        await client.aclose()

        assert seen == [["page-review.html"]], "купленное не покупается снова, отказ — бесплатный"

    async def test_paid_batch_is_kept_when_the_next_one_fails(self, session: AsyncSession) -> None:
        roots = [f"brand{n:03}.com" for n in range(150)]
        run_id = await _donor_run(session, roots)
        seen: list[list[str]] = []
        client = _real_client(_ahrefs_like_prod(seen, broken_from=1))
        ratings = functools.partial(ahrefs_ratings, client, allowance=_plenty)

        await judge_run(session, run_id, ratings=ratings)
        await session.commit()

        rows = await _rows(session)
        rated = sorted(root for root, row in rows.items() if row.dr_checked_at is not None)
        assert rated == roots[:100]

        seen.clear()
        await judge_run(session, run_id, ratings=ratings)
        await session.commit()
        await client.aclose()

        assert seen == [roots[100:]], "следующий пересчёт спрашивает только неоплаченное"


async def _donor_run(session: AsyncSession, roots: Sequence[str]) -> int:
    """Обход донора, на котором у каждого домена из `roots` — оплаченная ссылка («куплено»)."""
    donor = "donor-crooked.com"
    links = [_sponsored(donor, root, page) for page, root in enumerate(roots, start=1)]
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


def _ahrefs_like_prod(
    seen: list[list[str]], *, crooked: frozenset[str] = frozenset(), broken_from: int | None = None
) -> httpx.MockTransport:
    """Ahrefs, как он отвечал на бою: пакет с кривым именем — 400 без заголовков цены, с
    запроса `broken_from` (счёт с нуля) — 402, остальное — DR 23 и цена пакета."""

    def answer(request: httpx.Request) -> httpx.Response:
        targets = [target["url"] for target in json.loads(request.content)["targets"]]
        seen.append(targets)
        if broken_from is not None and len(seen) > broken_from:
            return httpx.Response(402)
        bad = next((target for target in targets if target in crooked), None)
        if bad is not None:
            return httpx.Response(
                400, json={"error": f"bad 'targets': invalid domain or url: '{bad}'"}
            )
        rows = [{"url": f"https://{target}/", "domain_rating": 23} for target in targets]
        cost = str(max(50, 2 * len(targets)))
        return httpx.Response(
            200, json={"targets": rows}, headers={"x-api-units-cost-total-actual": cost}
        )

    return httpx.MockTransport(answer)


def _real_client(transport: httpx.MockTransport) -> AhrefsClient:
    http = httpx.AsyncClient(transport=transport, base_url="https://ahrefs.example.test")
    return AhrefsClient(api_key="k", http=http)


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
