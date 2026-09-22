"""Прогон: дедупликация, отсев по сроку годности, смета и кап."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar

import httpx
import pytest
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import Quota, UnitsCost
from backend.features.core.domain import DonorStatus
from backend.features.runs.budget import (
    CapExceededError,
    QuotaUnavailableError,
    units_left,
)
from backend.features.runs.pipeline import RunReport
from backend.features.runs.planning import Candidates, gather_candidates, plan_run
from backend.features.serp.protocol import SerpResult


class FakeSerp:
    name = "fake"

    def __init__(self, answer: dict[str, list[str]]) -> None:
        self._answer = answer

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        return {
            kw: [SerpResult(position=i + 1, url=u) for i, u in enumerate(self._answer.get(kw, []))]
            for kw in keywords
        }


class FakeFreshness:
    def __init__(self, fresh: set[str]) -> None:
        self._fresh = fresh

    async def fresh_hosts(self, hosts: Sequence[str]) -> set[str]:
        return {h for h in hosts if h in self._fresh}


class TestCandidates:
    async def test_subdomains_and_paths_collapse_into_one_host(self) -> None:
        """Дедупликация — это не косметика: каждый схлопнутый адрес
        сэкономил юниты, которые иначе ушли бы на повторную проверку."""
        serp = FakeSerp(
            {
                "a": ["https://www.example.com/1", "https://blog.example.com/2"],
                "b": ["https://example.com/3", "https://other.com"],
            }
        )
        candidates = await gather_candidates(serp, ["a", "b"], "us")

        assert candidates.hosts == ["example.com", "other.com"]
        assert candidates.results == 4
        assert candidates.duplicates == 2

    async def test_order_follows_the_serp(self) -> None:
        serp = FakeSerp({"a": ["https://second.com", "https://first.com"]})
        candidates = await gather_candidates(serp, ["a"], "us")
        assert candidates.hosts == ["second.com", "first.com"]

    async def test_unparsable_urls_are_counted_not_swallowed(self) -> None:
        """Если бы мусор молча пропадал, из отчёта нельзя было бы понять,
        почему из тысячи результатов получилось двести доменов."""
        serp = FakeSerp({"a": ["https://good.com", "не-адрес", ""]})
        candidates = await gather_candidates(serp, ["a"], "us")

        assert candidates.hosts == ["good.com"]
        assert candidates.dropped == 2

    async def test_keywords_without_results_are_reported(self) -> None:
        """Пустой ключ — сигнал о плохом списке, а не о поломке сервиса."""
        serp = FakeSerp({"a": ["https://good.com"], "b": []})
        candidates = await gather_candidates(serp, ["a", "b"], "us")
        assert candidates.empty_keywords == ["b"]


class TestPlanning:
    CANDIDATES = Candidates(
        hosts=[f"d{i}.com" for i in range(100)],
        keywords=10,
        results=200,
        empty_keywords=[],
        dropped=0,
    )

    async def test_fresh_domains_drop_out_before_the_estimate(self) -> None:
        """Главное свойство повторный прогон почти бесплатен.
        Если бы свежие домены попадали в смету, кэш был бы не виден."""
        fresh = {f"d{i}.com" for i in range(90)}
        plan = await plan_run(self.CANDIDATES, FakeFreshness(fresh), units_left=10_000_000)

        assert len(plan.new) == 10
        assert len(plan.fresh) == 90
        assert plan.estimate.domains == 10

    async def test_cache_savings_are_visible(self) -> None:
        fresh = {f"d{i}.com" for i in range(90)}
        plan = await plan_run(self.CANDIDATES, FakeFreshness(fresh), units_left=10_000_000)
        assert plan.savings_from_cache > 0

    async def test_run_is_refused_before_the_first_paid_request(self) -> None:
        """Узнать о превышении на середине прогона — значит уже потратить
        половину. Поэтому кап проверяется до запуска."""
        with pytest.raises(CapExceededError, match="сократите список ключей"):
            await plan_run(self.CANDIDATES, FakeFreshness(set()), units_left=100)

    async def test_everything_fresh_means_nothing_to_pay(self) -> None:
        fresh = set(self.CANDIDATES.hosts)
        plan = await plan_run(self.CANDIDATES, FakeFreshness(fresh), units_left=0)

        assert plan.new == []
        assert plan.estimate.total == 0


class TestReport:
    def test_reject_reasons_are_grouped(self) -> None:
        """Отчёт должен говорить, какой порог отсеивает больше всего, —
        иначе калибровать пороги не по чему."""
        report = RunReport(plan=None)  # type: ignore[arg-type]
        report.record(DonorStatus.UNSUITABLE, "DR 5 ниже 20")
        report.record(DonorStatus.UNSUITABLE, "DR 12 ниже 20")
        report.record(DonorStatus.UNSUITABLE, "органический трафик 10 ниже 500")
        report.record(DonorStatus.SUITABLE, "us на 1-м месте")

        assert report.reject_reasons == {"DR": 2, "органический": 1}
        assert report.by_status[DonorStatus.SUITABLE] == 1

    def test_spent_units_accumulate_from_the_client(self) -> None:
        report = RunReport(plan=None)  # type: ignore[arg-type]
        report.add_usage("screen", UnitsCost(actual=200, estimated=200, per_row=2))
        report.add_usage("by_country", UnitsCost(actual=55, estimated=55, per_row=11))
        assert report.spent_units == 255


class TestQuota:
    """Остаток берётся у провайдера: ключ общий, и своя таблица знает только
    про наши траты."""

    PAYLOAD: ClassVar[dict] = {
        "units_limit_workspace": 8_000_000,
        "units_usage_workspace": 4_479_085,
        "units_limit_api_key": 2_000_000,
        "units_usage_api_key": 542_276,
        "usage_reset_date": "2026-09-21T00:00:00Z",
    }

    def test_binding_limit_is_the_smaller_remainder(self) -> None:
        """Лимита два и действуют одновременно: упереться можно в любой."""
        quota = Quota.from_payload(self.PAYLOAD)
        assert quota.available == 1_457_724  # ключ, а не пространство

    def test_exhausted_quota_is_zero_not_negative(self) -> None:
        quota = Quota.from_payload({**self.PAYLOAD, "units_usage_api_key": 3_000_000})
        assert quota.available == 0

    def test_missing_fields_do_not_pretend_there_is_budget(self) -> None:
        assert Quota.from_payload({}).available == 0

    async def test_our_cap_can_only_lower_the_budget(self) -> None:
        """Кап  ограничивает нас добровольно, остаток провайдера — жёстко."""
        client = _quota_client(self.PAYLOAD)
        assert await units_left(client, cap=100_000) == 100_000
        assert await units_left(client, cap=9_000_000) == 1_457_724

    async def test_unreadable_quota_stops_the_run(self) -> None:
        """«Не смогли узнать остаток — не тратим»: иначе можно выжечь лимит
        соседней системы на том же ключе."""
        client = _quota_client(None)
        with pytest.raises(QuotaUnavailableError, match="соседней системы"):
            await units_left(client)


def _quota_client(payload: dict | None) -> AhrefsClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if payload is None:
            return httpx.Response(500, text="oops")
        return httpx.Response(200, json={"limits_and_usage": payload})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return AhrefsClient(api_key="k", http=http)
