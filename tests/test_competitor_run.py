"""Прогон по ссылкам конкурентов: смета до запуска и шаг задачи до воронки — на настоящей базе.

Платного нет: Ahrefs — подменённым транспортом в форме провода, модель — выключенным судьёй,
выдача — подделкой, которая падает, если её позвали: у этого прогона выдачи нет.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.ahrefs import refdomains as links
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import estimate_run
from backend.features.core.domain import DonorStatus, RunStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.core.models.run import RunModel
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds
from backend.features.runs.budget import LIMIT_WORDS, CapExceededError
from backend.features.runs.competitor_estimate import (
    JUDGE_TOKENS_PER_DOMAIN,
    TRIM,
    forecast,
    judge_tokens,
    judge_tokens_from,
    lists_units,
)
from backend.features.runs.competitor_run import prepare
from backend.features.runs.competitors import Ask, CompetitorLinksError
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.repository import RunRepository, unique_share_from
from backend.features.serp.protocol import SerpResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.migration_helpers import columns_down_and_up

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
ASK = Ask(targets=("example.com", "example.net"), depth=10)
PER_ROW = 14
#: Числа выдуманы: лимиты настоящего аккаунта в публичный репозиторий не идут.
PLENTY = {
    "units_limit_workspace": 6_400_000,
    "units_usage_workspace": 0,
    "units_limit_api_key": 1_600_000,
    "units_usage_api_key": 0,
}
GOOD = {"domain_rating": 40, "org_traffic": 9000, "refdomains": 500, "org_keywords": 2000}
LIST_COST = {
    "x-api-units-cost-total-actual": "50",
    "x-api-units-cost-total": "50",
    "x-api-units-cost-row": str(PER_ROW),
}


@pytest.fixture(autouse=True)
def _quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """Судья выключен — модель не зовётся; паузы повторов не ждутся."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.OFF)
    monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", instant)


class Ahrefs:
    """Подменённый Ahrefs: остаток, списки по конкуренту, пакеты метрик и страны. Помнит,
    о каких списках его спросили, — так видно, что уже полученный не покупается снова."""

    def __init__(
        self,
        lists: dict[str, list[dict[str, Any]]],
        *,
        quota: dict[str, int] | None = None,
        refuse: dict[str, int] | None = None,
    ) -> None:
        self.lists = lists
        self.quota = quota or PLENTY
        self.refuse = refuse or {}
        self.asked: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("limits-and-usage"):
            return httpx.Response(200, json={"limits_and_usage": self.quota})
        if path == links.PATH:
            target = request.url.params["target"]
            self.asked.append(target)
            if target in self.refuse:
                return httpx.Response(self.refuse[target], text="refused", headers=LIST_COST)
            return httpx.Response(
                200, json={"refdomains": self.lists.get(target, [])}, headers=LIST_COST
            )
        cost = {"x-api-units-cost-total-actual": "10"}
        if path.endswith("batch-analysis"):
            hosts = [t["url"] for t in json.loads(request.content)["targets"]]
            rows = [{"url": f"{host}/", **GOOD} for host in hosts]
            return httpx.Response(200, json={"domains": rows}, headers=cost)
        return httpx.Response(
            200, json={"metrics": [{"country": "us", "org_traffic": 8000}]}, headers=cost
        )

    def client(self) -> AhrefsClient:
        http = httpx.AsyncClient(
            transport=httpx.MockTransport(self.handler), base_url="https://api.test"
        )
        return AhrefsClient(api_key="k", http=http)


class NoSerp:
    """Выдачи у прогона по конкурентам нет: позвали — тест падает."""

    name = "none"

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:  # pragma: no cover
        raise AssertionError("прогон по конкурентам выдачу не покупает")


async def _run(session: AsyncSession, **stored: Any) -> RunModel:
    runs = RunRepository(session)
    settings = await runs.create_settings(
        T, geo_top_n=5, geo_min_share=0.2, metrics_ttl_days=90, price_ttl_days=150, units_cap=0
    )
    run = await runs.create_run(
        stage=Stage.DONORS, settings_id=settings.id, keywords=[], country="us"
    )
    run.competitors = stored or ASK.as_dict()
    await session.flush()
    return run


def _rows(*hosts: str, dr: float = 40.0) -> list[dict[str, Any]]:
    return [{"domain": host, "domain_rating": dr} for host in hosts]


async def _usage(session: AsyncSession, run_id: int) -> list[tuple[str, int | None]]:
    rows = await session.scalars(
        select(UsageRecordModel)
        .where(UsageRecordModel.run_id == run_id)
        .order_by(UsageRecordModel.id)
    )
    return [(row.operation, row.units) for row in rows.all()]


class TestForecast:
    def test_lists_and_funnel_are_counted_for_the_worst_case(self) -> None:
        """Списки — каждый до глубины, сайты списков — все новые, и страны — каждому."""
        made = forecast(ASK, per_row=PER_ROW, units_left=1_000_000, units_cap=1_000_000)

        assert made.lists_units == 2 * max(50, 10 * PER_ROW) == 280
        assert made.expected_domains == 20
        assert made.estimate == estimate_run(20, country_share=1.0)
        assert made.units_total == 280 + made.estimate.total
        assert made.affordable

    def test_lists_alone_can_be_what_does_not_fit(self) -> None:
        """Сайты помещаются, а со списками — нет: списки платятся тем же ключом."""
        funnel = estimate_run(20, country_share=1.0).total
        made = forecast(ASK, per_row=PER_ROW, units_left=funnel + 100, units_cap=10**6)

        assert not made.affordable
        assert made.shortfall == 180
        assert made.short_of == ["provider"]

    def test_ceiling_is_one_of_the_limits(self) -> None:
        made = forecast(ASK, per_row=PER_ROW, units_left=10**6, units_cap=10**6, ceiling=100)

        assert made.short_of == ["ceiling"]
        assert made.budget == 100

    def test_already_fetched_lists_are_not_counted_again(self) -> None:
        assert lists_units(ASK, PER_ROW, skip=["example.com"]) == 140


class TestJudgeTokens:
    def test_worst_share_of_recent_runs_counts(self) -> None:
        history = [
            {"judge": {"judged": 100, "tokens": 130_000}},
            {"judge": {"judged": 50, "tokens": 85_000}},
            {"judge": {"judged": 3, "tokens": 90_000}},  # мало судили — шум
            {},
        ]

        assert judge_tokens_from(history, 999) == 1_700

    def test_without_history_the_measured_default(self) -> None:
        assert judge_tokens_from([], JUDGE_TOKENS_PER_DOMAIN) == JUDGE_TOKENS_PER_DOMAIN

    async def test_judge_off_spends_no_tokens(self, session: AsyncSession) -> None:
        assert await judge_tokens(session, 200) == 0

    async def test_judge_on_counts_every_site(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)

        assert await judge_tokens(session, 200) == 200 * JUDGE_TOKENS_PER_DOMAIN


class TestPrepare:
    async def test_keyword_run_goes_as_is(self, session: AsyncSession) -> None:
        run = await _run(session)
        run.competitors = None
        ahrefs = Ahrefs({})

        prepared = await prepare(session, ahrefs.client(), run, T, cap=5000)

        assert (prepared.candidates, prepared.cap, prepared.spent) == (None, 5000, 0)
        assert ahrefs.asked == []

    async def test_resumed_run_with_saved_candidates_buys_no_lists(
        self, session: AsyncSession
    ) -> None:
        run = await _run(session)
        run.candidates = {"hosts": ["example.org"], "keywords": 0, "results": 1}
        ahrefs = Ahrefs({})

        prepared = await prepare(session, ahrefs.client(), run, T, cap=5000)

        assert prepared.candidates is None
        assert ahrefs.asked == []

    async def test_lists_become_candidates_and_their_cost_is_journaled(
        self, session: AsyncSession
    ) -> None:
        domain = DomainModel(host="example.org")
        session.add(domain)
        await session.flush()
        session.add(
            DonorModel(
                domain_id=domain.id,
                status=DonorStatus.SUITABLE,
                metrics_refreshed_at=datetime.now(UTC),
                review="accepted",
            )
        )
        run = await _run(session)
        ahrefs = Ahrefs(
            {
                "example.com": _rows("alpha.github.io", "example.org", "example.net"),
                "example.net": _rows("alpha.github.io", "beta.github.io"),
            }
        )

        prepared = await prepare(session, ahrefs.client(), run, T, cap=5000)

        assert prepared.candidates is not None
        assert prepared.candidates.hosts == ["alpha.github.io", "beta.github.io"]
        assert prepared.spent == 100
        assert prepared.cap == 4900, "потолок воронки — за вычетом списков"
        source = prepared.candidates.source
        assert source["dropped"] == {"competitor": 1, "repeat": 1, "donor": 1}
        assert [t["kept"] for t in source["targets"]] == [1, 1]
        assert await _usage(session, run.id) == [("refdomains", 50), ("refdomains", 50)]
        stored = (await session.get(RunModel, run.id)).competitors  # type: ignore[union-attr]
        assert set(stored["fetched"]) == {"example.com", "example.net"}
        row = await session.scalar(
            select(UsageRecordModel).where(UsageRecordModel.run_id == run.id)
        )
        assert row is not None
        assert row.stage is Stage.DONORS

    async def test_worst_case_over_the_key_is_refused_before_the_first_list(
        self, session: AsyncSession
    ) -> None:
        tight = {**PLENTY, "units_limit_api_key": 1_000, "units_usage_api_key": 0}
        run = await _run(session)
        ahrefs = Ahrefs({}, quota=tight)

        with pytest.raises(CapExceededError) as caught:
            await prepare(session, ahrefs.client(), run, T, cap=100_000)

        assert LIMIT_WORDS["key"] in str(caught.value)
        assert TRIM in str(caught.value)
        assert ahrefs.asked == [], "ни одного списка — ни одного юнита"
        assert await _usage(session, run.id) == []

    async def test_resumed_run_buys_only_the_lists_it_has_not_got(
        self, session: AsyncSession
    ) -> None:
        fetched = {"rows": [["gamma.github.io", 30.0]], "units": 50, "per_row": PER_ROW}
        run = await _run(session, **ASK.as_dict(), fetched={"example.com": fetched})
        ahrefs = Ahrefs({"example.net": _rows("delta.github.io")})

        prepared = await prepare(session, ahrefs.client(), run, T, cap=5000)

        assert ahrefs.asked == ["example.net"]
        assert prepared.candidates is not None
        assert prepared.candidates.hosts == ["gamma.github.io", "delta.github.io"]
        assert prepared.spent == 100, "смета прогона — со списком прошлой попытки"

    async def test_refused_list_keeps_what_was_got_and_what_it_cost(
        self, session: AsyncSession
    ) -> None:
        run = await _run(session)
        ahrefs = Ahrefs({"example.com": _rows("alpha.github.io")}, refuse={"example.net": 403})

        with pytest.raises(CompetitorLinksError) as caught:
            await prepare(session, ahrefs.client(), run, T, cap=5000)

        assert caught.value.permanent
        assert "Ссылки конкурента example.net не получены" in str(caught.value)
        stored = (await session.get(RunModel, run.id)).competitors  # type: ignore[union-attr]
        assert list(stored["fetched"]) == ["example.com"]
        assert await _usage(session, run.id) == [("refdomains", 50), ("refdomains", 50)]


async def test_competitor_run_goes_through_the_same_funnel(session: AsyncSession) -> None:
    """Списки → кандидаты → воронка: пороги, «Гео», журнал. Смета прогона — со списками,
    выдача не покупается, отчёт называет источник."""
    run = await _run(session)
    # Не поддомены платформы: `github.io` отсёк бы гейт исключений (`runs/exclusions.py`).
    ahrefs = Ahrefs({"example.com": _rows("example.org"), "example.net": _rows("example.co.uk")})
    client = ahrefs.client()
    prepared = await prepare(session, client, run, T, cap=50_000)

    report = await execute_run(
        RunDeps(
            provider=NoSerp(),
            client=client,
            donors=DonorRepository(session),
            runs=RunRepository(session),
            exclusions=Exclusions(session),
        ),
        RunRequest(
            keywords=[],
            country="us",
            thresholds=T,
            settings_id=run.settings_id,
            cap=prepared.cap,
            run=run,
            candidates=prepared.candidates,
            spent_before=prepared.spent,
        ),
    )

    assert sorted(report.plan.new) == ["example.co.uk", "example.org"]
    stored = await session.get(RunModel, run.id)
    assert stored is not None
    assert stored.status is RunStatus.DONE
    assert stored.estimated_units == 100 + report.plan.estimate.total
    assert stored.candidates is not None
    assert stored.candidates["source"]["kind"] == "competitors"
    assert stored.stats is not None
    assert stored.stats["source"]["targets"][0]["target"] == "example.com"
    assert "linked_to" not in stored.stats["source"]
    operations = {operation for operation, _ in await _usage(session, run.id)}
    assert {"refdomains", "batch_metrics"} <= operations


async def test_competitor_runs_do_not_move_the_share_of_unique_serp_domains(
    session: AsyncSession,
) -> None:
    """Доля дублей выдачи — только у прогонов по выдаче: у списков конкурентов свои повторы,
    и их сто из ста подняли бы смету ключей до «все результаты — разные домены»."""
    serp = await _run(session)
    serp.competitors = None
    rivals = await _run(session)
    for run, stats in [
        (serp, {"serp_results": 100, "unique_hosts": 40}),
        (rivals, {"serp_results": 100, "unique_hosts": 100, "source": {"kind": "competitors"}}),
    ]:
        run.status, run.stats = RunStatus.DONE, stats
    await session.flush()

    assert await RunRepository(session).unique_share(0.85) == 0.4
    assert unique_share_from([rivals.stats or {}], 0.85) == 1.0, "без отбора — доля списка"


async def test_revision_goes_down_and_up(session: AsyncSession) -> None:
    connection = await session.connection()

    down, up = await connection.run_sync(
        columns_down_and_up, "2b9c4561c381_run_competitors.py", "runs", {"competitors"}
    )

    assert (down, up) == (set(), {"competitors"})
