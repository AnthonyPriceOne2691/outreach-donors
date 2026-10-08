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
from backend.features.runs.failures import is_permanent
from backend.features.runs.planning import Candidates, gather_candidates, plan_run
from backend.features.runs.report import RunReport
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


class LosingSerp:
    """Источник, который чистит ключи, как DataForSEO, и теряет часть из них:
    ключа, задачу по которому не дождались, в ответе нет вовсе."""

    name = "losing"

    def __init__(self, answer: dict[str, list[str]], *, lost: set[str]) -> None:
        self._answer = answer
        self._lost = lost

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        clean = dict.fromkeys(k.strip() for k in keywords if k.strip())
        return {
            kw: [SerpResult(position=i + 1, url=u) for i, u in enumerate(self._answer.get(kw, []))]
            for kw in clean
            if kw not in self._lost
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
        serp = FakeSerp({"a": ["https://good.com", "не-адрес", "", "https://exa[mple.com/"]})
        candidates = await gather_candidates(serp, ["a"], "us")

        assert candidates.hosts == ["good.com"]
        assert candidates.dropped == 3

    async def test_each_host_remembers_its_keywords(self) -> None:
        """Какие ключи дают доноров, а какие вендоров, видно только по этой
        связи: прогон 23.09.2026 пришлось разбирать по адресам страниц."""
        serp = FakeSerp(
            {
                "saas write for us": ["https://blog.example.com/write-for-us", "https://b.com"],
                "best crm": ["https://www.example.com/crm", "https://example.com/other"],
            }
        )

        candidates = await gather_candidates(serp, ["saas write for us", "best crm"], "us")

        assert candidates.found_by == {
            "example.com": ["saas write for us", "best crm"],
            "b.com": ["saas write for us"],
        }

    async def test_keywords_survive_a_resumed_run(self) -> None:
        """Продолжение прогона не покупает выдачу заново — и не должно
        терять связь, за которую уже заплачено."""
        serp = FakeSerp({"a": ["https://good.com"]})
        candidates = await gather_candidates(serp, ["a"], "us")

        restored = Candidates.restored(candidates.as_dict())

        assert restored.found_by == {"good.com": ["a"]}

    async def test_keywords_without_results_are_reported(self) -> None:
        """Пустой ключ — сигнал о плохом списке, а не о поломке сервиса."""
        serp = FakeSerp({"a": ["https://good.com"], "b": []})
        candidates = await gather_candidates(serp, ["a", "b"], "us")
        assert candidates.empty_keywords == ["b"]
        assert candidates.lost_keywords == []

    async def test_keyword_the_source_never_returned_is_lost_not_empty(self) -> None:
        """Выдача оплачена, а не пришла: источник не дождался задачи и ключа
        в ответе нет. Это не «ничего не нашлось» — до 25.09.2026 такие ключи
        считались пустыми, и прогон, у которого провайдер не успел, выглядел
        прогоном по неудачным ключам."""
        serp = LosingSerp({"a": ["https://good.com"], "b": []}, lost={"c"})

        candidates = await gather_candidates(serp, ["a", "b", " c ", "a"], "us")

        assert candidates.empty_keywords == ["b"]
        assert candidates.lost_keywords == [" c "]
        restored = Candidates.restored(candidates.as_dict())
        assert restored.lost_keywords == [" c "]

    async def test_source_that_trims_keywords_loses_nothing(self) -> None:
        """Источник чистит ключ от пробелов: « a » в ответе — «a». Сверка по
        сырому ключу записала бы полную выдачу в потерянные."""
        serp = LosingSerp({"a": ["https://good.com"]}, lost=set())
        candidates = await gather_candidates(serp, [" a "], "us")
        assert candidates.lost_keywords == []


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

    # Числа выдуманы: лимиты и расход настоящего аккаунта в публичный
    # репозиторий не идут. Соотношение то же — упирается ключ.
    PAYLOAD: ClassVar[dict] = {
        "units_limit_workspace": 6_400_000,
        "units_usage_workspace": 3_300_000,
        "units_limit_api_key": 1_600_000,
        "units_usage_api_key": 432_100,
        "usage_reset_date": "2026-09-21T00:00:00Z",
    }

    def test_binding_limit_is_the_smaller_remainder(self) -> None:
        """Лимита два и действуют одновременно: упереться можно в любой."""
        quota = Quota.from_payload(self.PAYLOAD)
        assert quota.available == 1_167_900  # ключ, а не пространство

    def test_exhausted_quota_is_zero_not_negative(self) -> None:
        quota = Quota.from_payload({**self.PAYLOAD, "units_usage_api_key": 3_000_000})
        assert quota.available == 0

    def test_missing_fields_do_not_pretend_there_is_budget(self) -> None:
        assert Quota.from_payload({}).available == 0

    async def test_our_cap_can_only_lower_the_budget(self) -> None:
        """Кап  ограничивает нас добровольно, остаток провайдера — жёстко."""
        client = _quota_client(self.PAYLOAD)
        assert await units_left(client, cap=100_000) == 100_000
        assert await units_left(client, cap=9_000_000) == 1_167_900

    async def test_unreadable_quota_stops_the_run(self) -> None:
        """«Не смогли узнать остаток — не тратим»: иначе можно выжечь лимит
        соседней системы на том же ключе."""
        client = _quota_client(None)
        with pytest.raises(QuotaUnavailableError, match="соседней системы"):
            await units_left(client)

    async def test_a_page_instead_of_the_quota_stops_the_run_in_words(self) -> None:
        """Страница вместо остатка (посредник, заглушка на время работ) — тот же
        отказ «остаток неизвестен», а не техническая ошибка разбора JSON."""

        def page(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, text="<!doctype html><title>Maintenance</title>")

        http = httpx.AsyncClient(transport=httpx.MockTransport(page), base_url="https://api.test")
        with pytest.raises(QuotaUnavailableError, match="соседней системы"):
            await units_left(AhrefsClient(api_key="k", http=http))

    async def test_a_refused_key_stops_the_run_for_good_and_says_why(self) -> None:
        """Ключ отозван (401) — повтор не поможет: признак у отказа, причина в тексте.
        До 08.10.2026 прогон с таким ключом шёл «сбой, будет продолжен» до конца
        продолжений и не говорил, почему."""

        def refused(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, text="invalid api key")

        http = httpx.AsyncClient(
            transport=httpx.MockTransport(refused), base_url="https://api.test"
        )
        key = "made-up-key"  # pragma: allowlist secret
        with pytest.raises(QuotaUnavailableError) as caught:
            await units_left(AhrefsClient(api_key=key, http=http))

        assert is_permanent(caught.value)
        assert str(caught.value).endswith(
            "Причина: Остаток квоты недоступен: Ahrefs ответил 401: invalid api key"
        )
        assert key not in str(caught.value)

    async def test_a_provider_failure_on_the_quota_leaves_the_run_to_continue(self) -> None:
        """5xx на остатке — сбой провайдера: прогон продолжат позже, а не закроют."""
        with pytest.raises(QuotaUnavailableError) as caught:
            await units_left(_quota_client(None))

        assert not is_permanent(caught.value)
        assert str(caught.value).endswith(
            "Причина: Остаток квоты недоступен: Ahrefs ответил 500: oops"
        )


def _quota_client(payload: dict | None) -> AhrefsClient:
    def handler(request: httpx.Request) -> httpx.Response:
        if payload is None:
            return httpx.Response(500, text="oops")
        return httpx.Response(200, json={"limits_and_usage": payload})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return AhrefsClient(api_key="k", http=http)
