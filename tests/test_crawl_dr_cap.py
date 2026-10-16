"""DR кандидатов Этапа 2 — под капом Ahrefs, правилом прогона Этапа 1.

До 12.10.2026 пакет DR кандидатов капа не спрашивал вовсе: шёл той же операцией, что
метрики прогона, и после выбранного месяца пересчёт кандидатов тратил дальше — на ключе,
общем с соседней системой. Теперь смета пакета сверяется с тем же, с чем прогон: остаток
капа цикла ключа и остаток у провайдера, оба — за вычетом обещанного идущими прогонами.
Кап — от ключа (его называет ответ Ahrefs, 16.10.2026), сузить его — строкой смены с
экрана «Расход» (`AhrefsCapModel`).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from backend.cli import advertisers
from backend.config import ahrefs as ahrefs_cfg
from backend.config import storage
from backend.features.ahrefs.client import Response
from backend.features.ahrefs.units import UnitsCost
from backend.features.core import usage
from backend.features.core.domain import CrawlOutcome, RunStatus, Stage, StopReason, Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.ops import AhrefsCapModel, UsageRecordModel
from backend.features.crawl.big_sites import (
    RatingsRefusedError,
    ahrefs_ratings,
    capped_ratings,
    dr_cost,
)
from backend.features.crawl.gate import judge_run
from backend.features.crawl.links import OutLink
from backend.features.crawl.report import CrawlReport
from backend.features.crawl.repository import save_crawl
from backend.features.donors.verdict import Thresholds
from backend.features.runs.budget import QuotaUnavailableError, units_free
from backend.features.runs.repository import RunRepository
from backend.workers import crawl_jobs
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import TEST_DSN
from tests.test_advertisers_cli import _FakeAhrefs as _CliAhrefs
from tests.test_advertisers_cli import _judge
from tests.test_send_race import committed_sessions

DONOR = "donor-cap.example.test"


class _Ahrefs:
    """Провайдер с остатком: помнит, о чём спросили, и сообщает расход, как настоящий."""

    def __init__(self, *, available: int = 1_000_000, on_usage: Any = None) -> None:
        self.available = available
        self.on_usage = on_usage
        self.asked: list[list[str]] = []

    async def limits_and_usage(self) -> dict[str, int]:
        return {
            "units_limit_workspace": self.available,
            "units_usage_workspace": 0,
            "units_limit_api_key": self.available,
            "units_usage_api_key": 0,
        }

    async def batch_metrics(self, hosts: Sequence[str], select: Sequence[str]) -> Response:
        self.asked.append(list(hosts))
        cost = UnitsCost(actual=50, estimated=50, per_row=2)
        if self.on_usage is not None:
            self.on_usage("batch_metrics", cost)
        known = {"marketplace.example.test": 96}
        rows = [{"url": f"https://{h}/", "domain_rating": known[h]} for h in hosts if h in known]
        return Response(rows=rows, cost=cost)

    async def aclose(self) -> None:
        return None


class _Blind(_Ahrefs):
    async def limits_and_usage(self) -> dict[str, int]:
        raise OSError("тест: провайдер не ответил")


class TestCost:
    @pytest.mark.parametrize(
        ("domains", "units"),
        [(1, 50), (25, 50), (26, 52), (100, 200), (101, 250), (250, 500)],
    )
    def test_batches_of_a_hundred_with_the_minimum_each(self, domains: int, units: int) -> None:
        """2 юнита на домен, минимум 50 на запрос: пакет на одного донора почти всегда
        упирается в минимум (`okf/stage2-economy.md`)."""
        assert dr_cost(domains) == units


class TestAllowance:
    async def test_batch_within_the_allowance_is_asked(self) -> None:
        client = _Ahrefs()

        async def allowance() -> int:
            return 50

        found = await ahrefs_ratings(
            client,  # type: ignore[arg-type]
            ["marketplace.example.test", "small.example.test"],
            allowance=allowance,
        )

        assert client.asked == [["marketplace.example.test", "small.example.test"]]
        assert found["marketplace.example.test"] == 96

    async def test_batch_over_the_allowance_asks_nothing(self) -> None:
        client = _Ahrefs()

        async def allowance() -> int:
            return 49

        with pytest.raises(RatingsRefusedError, match="кап юнитов Ahrefs: пакет стоит до 50"):
            await ahrefs_ratings(client, ["a.example.test"], allowance=allowance)  # type: ignore[arg-type]

        assert client.asked == []

    async def test_unknown_balance_is_a_refusal_not_a_guess(self) -> None:
        client = _Ahrefs()

        async def allowance() -> int:
            raise QuotaUnavailableError("остаток неизвестен")

        with pytest.raises(RatingsRefusedError, match="тратить вслепую нельзя"):
            await ahrefs_ratings(client, ["a.example.test"], allowance=allowance)  # type: ignore[arg-type]

        assert client.asked == []


async def _promise(session: AsyncSession, estimated: int) -> None:
    """Идущий прогон, обещавший `estimated` юнитов и не потративший ничего."""
    repo = RunRepository(session)
    settings = await repo.create_settings(
        Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    await repo.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=["crm"],
        country="us",
        estimated_units=estimated,
        status=RunStatus.RUNNING,
    )
    await session.flush()


class TestUnitsFree:
    async def test_month_cap_minus_spent_and_minus_promised(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Правило прогона: кап за вычетом потраченного и обещанного идущими."""
        session.add(AhrefsCapModel(units_cap=12_000))
        usage.record(session, operation="batch_metrics", units=8_000, stage=Stage.DONORS)
        await _promise(session, 3_000)

        free = await units_free(session, _Ahrefs())  # type: ignore[arg-type]

        assert free == 1_000

    async def test_provider_balance_minus_promised_when_it_is_lower(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Остаток у провайдера включает траты соседней системы на общем ключе."""
        await _promise(session, 300)

        free = await units_free(session, _Ahrefs(available=500))  # type: ignore[arg-type]

        assert free == 200

    async def test_unknown_balance_is_said(self, session: AsyncSession) -> None:
        with pytest.raises(QuotaUnavailableError):
            await units_free(session, _Blind())  # type: ignore[arg-type]


def _sponsored(root: str) -> OutLink:
    return OutLink(
        page_url=f"https://{DONOR}/post/1",
        url=f"https://{root}/offer",
        target_host=root,
        target_root=root,
        anchor="Acme",
        anchor_key="acme",
        nofollow=False,
        sponsored=True,
        ugc=False,
    )


async def _crawl(session: AsyncSession) -> int:
    links = [_sponsored("marketplace.example.test"), _sponsored("small-brand.example.test")]
    report = CrawlReport(
        host=DONOR,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages=[links[0].page_url],
        links=links,
        articles=1,
    )
    run = await save_crawl(session, report)
    await session.flush()
    return run.id


class TestJudgeUnderTheCap:
    async def test_spent_month_keeps_verdicts_and_says_why(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Месяц выбран — DR не спрашивается, вердикты остаются, а в причинах — кап,
        а не «провайдер не ответил»: следующий пересчёт спросит, когда юниты будут."""
        session.add(AhrefsCapModel(units_cap=1_000))
        usage.record(session, operation="batch_metrics", units=990, stage=Stage.DONORS)
        run_id = await _crawl(session)
        client = _Ahrefs()

        await judge_run(session, run_id, ratings=capped_ratings(client, session))  # type: ignore[arg-type]
        await session.flush()

        assert client.asked == []
        rows = (await session.execute(select(CandidateModel))).scalars().all()
        big = next(row for row in rows if row.target_root == "marketplace.example.test")
        assert big.verdict is Verdict.BOUGHT
        assert big.dr_checked_at is None
        assert "кап юнитов Ahrefs" in (big.reasons or [])[-1]

    async def test_room_in_the_month_asks_and_blocks_the_big_site(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session.add(AhrefsCapModel(units_cap=1_000))
        run_id = await _crawl(session)
        client = _Ahrefs()

        await judge_run(session, run_id, ratings=capped_ratings(client, session))  # type: ignore[arg-type]
        await session.flush()

        assert len(client.asked) == 1
        rows = (await session.execute(select(CandidateModel))).scalars().all()
        big = next(row for row in rows if row.target_root == "marketplace.example.test")
        assert big.verdict is Verdict.BLOCKED


async def test_judge_job_writes_the_dr_spend_as_stage_two(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Задача пересчёта пишет DR кандидатов расходом Этапа 2 — тем, что отличает его
    на экране «Расход» от метрик прогона той же операцией."""
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(ahrefs_cfg, "API_KEY", "test-key")
    monkeypatch.setattr(crawl_jobs, "AhrefsClient", _Ahrefs)
    async with committed_sessions() as factory:
        async with factory() as session:
            run_id = await _crawl(session)
            await session.commit()

        judged = await crawl_jobs._judge(run_id)

        async with factory() as session:
            spent = (await session.execute(select(UsageRecordModel))).scalars().all()
    assert judged["by_verdict"].get("blocked") == 1
    assert [(row.operation, row.units, row.stage) for row in spent] == [
        ("batch_metrics", 50, Stage.ADVERTISERS)
    ]


async def _crawl_of(factory: async_sessionmaker[AsyncSession], donor: str) -> None:
    """Обход донора с одним «куплено», которому нужен DR."""
    link = OutLink(
        page_url=f"https://{donor}/post/1",
        url="https://marketplace.example.test/offer",
        target_host="marketplace.example.test",
        target_root="marketplace.example.test",
        anchor="Acme",
        anchor_key="acme",
        nofollow=False,
        sponsored=True,
        ugc=False,
    )
    report = CrawlReport(
        host=donor,
        outcome=CrawlOutcome.OK,
        stop_reason=StopReason.EXHAUSTED,
        pages=[link.page_url],
        links=[link],
        articles=1,
    )
    async with factory() as session:
        await save_crawl(session, report)
        await session.commit()


async def test_console_without_run_sees_what_it_bought_on_the_previous_donor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`outreach advertisers` без `--run` пересчитывает всех доноров одним запуском. До правки
    расход DR писался раз в конце, и кап второго пакета не видел купленного первым: при капе
    на один пакет уходили оба."""
    monkeypatch.setattr(storage, "DSN", TEST_DSN)
    monkeypatch.setattr(ahrefs_cfg, "API_KEY", "test-key")
    monkeypatch.setattr(advertisers, "AhrefsClient", _CliAhrefs)
    _CliAhrefs.asked = []
    async with committed_sessions() as factory:
        async with factory() as session:
            # Кап на один пакет — сменой с экрана «Расход»: ключ у подделки с запасом.
            session.add(AhrefsCapModel(units_cap=99))
            await session.commit()
        await _crawl_of(factory, "first-donor.example.test")
        await _crawl_of(factory, "second-donor.example.test")

        await advertisers.cmd_advertisers(_judge(no_dr=False))

        async with factory() as session:
            spent = (await session.execute(select(UsageRecordModel))).scalars().all()
            rows = (await session.execute(select(CandidateModel))).scalars().all()
    assert len(_CliAhrefs.asked) == 1, "второй пакет не влез в остаток капа"
    assert [(row.units, row.stage) for row in spent] == [(50, Stage.ADVERTISERS)]
    reasons = [" ".join(row.reasons or []) for row in rows if row.dr_checked_at is None]
    assert len(reasons) == 1
    assert "кап юнитов Ahrefs" in reasons[0]
