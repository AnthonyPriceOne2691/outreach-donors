"""«Не проверен»: причина хранится, а без стран домен добирается следующим прогоном.

До 11.10.2026 причина «не проверен» не сохранялась (`reject_reason` — только у отказа),
а домен, у которого упал запрос по странам, получал отметку метрик и 90 дней числился
свежим: обещание «повторный прогон доберёт страны» (okf/unchecked-vs-unsuitable.md)
не исполнялось. Теперь он не свежий, и прогон платит только за страны — метрики куплены.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunModel
from backend.features.donors import export
from backend.features.donors.browse import DonorBrowser, DonorFilters
from backend.features.donors.collect import (
    COUNTRIES_FAILED,
    DomainResult,
    PaidMetrics,
    collect,
)
from backend.features.donors.geo import GeoVerdict
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Metrics, Thresholds
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.planning import Candidates
from backend.features.runs.repository import RunRepository
from backend.features.serp.protocol import SerpResult
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
GOOD = {"domain_rating": 40, "org_traffic": 9000, "refdomains": 500, "org_keywords": 2000}
QUOTA = {
    "units_limit_workspace": 6_400_000,
    "units_usage_workspace": 0,
    "units_limit_api_key": 1_600_000,
    "units_usage_api_key": 0,
}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", instant)


class Ahrefs:
    """Провайдер-заглушка: метрики по доменам, страны — пока `countries` не выключат.

    Запросы записываются по ступеням и доменам: только по ним видно, за что
    прогон заплатил, — и что за метрики второй раз не платил."""

    def __init__(self, metrics: dict[str, dict[str, Any]]) -> None:
        self.metrics = metrics
        self.countries = True
        self.calls: list[tuple[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        cost = {"x-api-units-cost-total-actual": "10"}
        path = request.url.path
        if path.endswith("limits-and-usage"):
            return httpx.Response(200, json={"limits_and_usage": QUOTA})
        if path.endswith("batch-analysis"):
            payload = json.loads(request.content)
            stage = "screen" if len(payload["select"]) == 2 else "full"
            hosts = [t["url"] for t in payload["targets"]]
            self.calls.extend((stage, host) for host in hosts)
            rows = [{"url": f"{h}/", **self.metrics[h]} for h in hosts if h in self.metrics]
            return httpx.Response(200, json={"domains": rows}, headers=cost)
        host = dict(request.url.params)["target"]
        self.calls.append(("country", host))
        if not self.countries:
            return httpx.Response(500)
        return httpx.Response(
            200, json={"metrics": [{"country": "us", "org_traffic": 8000}]}, headers=cost
        )

    def client(self) -> AhrefsClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self), base_url="https://api.test")
        return AhrefsClient(api_key="k", http=http)


def _countries_failed(host: str) -> DomainResult:
    """Метрики пройдены и куплены, страны не пришли — как пишет `collect`."""
    return DomainResult(
        host,
        DonorStatus.UNCHECKED,
        COUNTRIES_FAILED,
        Metrics(dr=40, org_traffic=9000, refdomains=500, org_keywords=2000),
        raw={"url": f"{host}/", **GOOD},
    )


async def _donor(session: AsyncSession, host: str) -> DonorModel:
    return (
        await session.execute(
            select(DonorModel)
            .join(DomainModel, DomainModel.id == DonorModel.domain_id)
            .where(DomainModel.host == host)
        )
    ).scalar_one()


class TestReasonIsKept:
    async def test_unchecked_keeps_why_and_suitable_stays_without(
        self, session: AsyncSession
    ) -> None:
        """«Не знает домен» добирать незачем, «страны не получены» — нужно: без причины
        их не отличить ни человеку, ни отчёту."""
        repo = DonorRepository(session)
        ghost = DomainResult(
            "ghost.example.test", DonorStatus.UNCHECKED, "Ahrefs не знает домен", Metrics()
        )
        good = DomainResult(
            "good.example.test", DonorStatus.SUITABLE, "US на 1-м месте", Metrics(dr=40)
        )
        await repo.save_results([ghost, good, _countries_failed("half.example.test")], now=NOW)

        assert (
            await _donor(session, "ghost.example.test")
        ).reject_reason == "Ahrefs не знает домен"
        assert (await _donor(session, "half.example.test")).reject_reason == COUNTRIES_FAILED
        assert (await _donor(session, "good.example.test")).reject_reason is None

    async def test_the_file_says_why_in_its_own_column(self, session: AsyncSession) -> None:
        """В выгрузке причина «не проверен» — колонкой «почему не проверен»: «причина
        отсева» у домена, которого никто не отсеивал, читалась бы приговором."""
        await DonorRepository(session).save_results(
            [_countries_failed("half.example.test")], now=NOW
        )
        donor = await _donor(session, "half.example.test")
        donor.review = "accepted"
        await session.flush()

        page = await DonorBrowser(session).page(DonorFilters())
        header, row = csv.reader(
            io.StringIO(export.to_csv(page.rows).decode("utf-8-sig")), delimiter=";"
        )
        cells = dict(zip(header, row, strict=True))

        assert (cells["причина отсева"], cells["почему не проверен"]) == ("", COUNTRIES_FAILED)


class TestWithoutCountriesIsNotFresh:
    async def test_countries_failed_is_picked_up_again_with_its_metrics(
        self, session: AsyncSession
    ) -> None:
        repo = DonorRepository(session)
        await repo.save_results([_countries_failed("half.example.test")], now=NOW)

        assert await repo.fresh_hosts(["half.example.test"], now=NOW) == set()
        paid = await repo.paid_metrics(["half.example.test"], now=NOW)
        assert paid["half.example.test"].bought_at == NOW
        assert paid["half.example.test"].raw["org_keywords"] == 2000

    async def test_ahrefs_answering_no_countries_stays_fresh(self, session: AsyncSession) -> None:
        """Ответ «стран нет» — «проверяли, данных нет»: отметка стран стоит, и платить
        за тот же ответ второй раз незачем."""
        repo = DonorRepository(session)
        empty = _countries_failed("empty.example.test")
        empty.reason = "нет данных по странам"
        empty.geo = GeoVerdict(False, "нет данных по странам", None, [])
        await repo.save_results([empty], now=NOW)

        assert await repo.fresh_hosts(["empty.example.test"], now=NOW) == {"empty.example.test"}
        assert await repo.paid_metrics(["empty.example.test"], now=NOW) == {}

    @pytest.mark.parametrize("missing", ["org_keywords", "refdomains"])
    async def test_a_metric_ahrefs_did_not_return_stays_fresh(
        self, session: AsyncSession, missing: str
    ) -> None:
        """До стран такой домен не дошёл: «Ahrefs не вернул» — тоже «данных нет»."""
        repo = DonorRepository(session)
        thin = _countries_failed("thin.example.test")
        thin.reason = f"Ahrefs не вернул: {missing}"
        thin.raw = {**(thin.raw or {}), missing: None}
        await repo.save_results([thin], now=NOW)

        assert await repo.fresh_hosts(["thin.example.test"], now=NOW) == {"thin.example.test"}

    async def test_a_row_from_before_the_reason_is_recognised(self, session: AsyncSession) -> None:
        """Записи до 11.10.2026 — без причины: признак берётся по колонкам."""
        repo = DonorRepository(session)
        await repo.save_results([_countries_failed("old.example.test")], now=NOW)
        donor = await _donor(session, "old.example.test")
        donor.reject_reason = None
        await session.flush()

        assert await repo.fresh_hosts(["old.example.test"], now=NOW) == set()
        assert set(await repo.paid_metrics(["old.example.test"], now=NOW)) == {"old.example.test"}

    async def test_expired_metrics_are_bought_again_in_full(self, session: AsyncSession) -> None:
        repo = DonorRepository(session)
        await repo.save_results([_countries_failed("stale.example.test")], now=NOW)

        later = NOW + timedelta(days=91)
        assert await repo.fresh_hosts(["stale.example.test"], ttl_days=90, now=later) == set()
        assert await repo.paid_metrics(["stale.example.test"], ttl_days=90, now=later) == {}


async def _collect(
    ahrefs: Ahrefs, hosts: list[str], paid: dict[str, PaidMetrics]
) -> list[DomainResult]:
    out: list[DomainResult] = []
    async for batch in collect(hosts, ahrefs.client(), T, "us", date="2026-10-11", paid=paid):
        out.extend(batch)
    return out


class TestOnlyCountriesArePaid:
    async def test_paid_domain_asks_for_countries_only(self) -> None:
        ahrefs = Ahrefs({"new.example.test": GOOD})
        paid = {
            "half.example.test": PaidMetrics(
                raw={"url": "half.example.test/", **GOOD}, bought_at=NOW
            )
        }

        results = await _collect(ahrefs, ["new.example.test", "half.example.test"], paid)

        by_host = {result.host: result for result in results}
        assert ("screen", "half.example.test") not in ahrefs.calls
        assert ("full", "half.example.test") not in ahrefs.calls
        assert ("country", "half.example.test") in ahrefs.calls
        assert by_host["half.example.test"].status is DonorStatus.SUITABLE
        assert by_host["half.example.test"].metrics_at == NOW, "метрики — прошлой покупки"
        assert by_host["new.example.test"].metrics_at is None

    async def test_top_country_in_the_paid_row_costs_nothing(self) -> None:
        ahrefs = Ahrefs({})
        raw = {"url": "half.example.test/", **GOOD, "org_traffic_top_by_country": [["us", 8000]]}

        (result,) = await _collect(
            ahrefs,
            ["half.example.test"],
            {"half.example.test": PaidMetrics(raw=raw, bought_at=NOW)},
        )

        assert ahrefs.calls == []
        assert result.status is DonorStatus.SUITABLE

    async def test_stricter_thresholds_need_no_countries(self) -> None:
        """Пороги — этого прогона: не прошедшему их страны не нужны."""
        ahrefs = Ahrefs({})
        raw = {"url": "half.example.test/", **GOOD, "org_keywords": 100}

        (result,) = await _collect(
            ahrefs,
            ["half.example.test"],
            {"half.example.test": PaidMetrics(raw=raw, bought_at=NOW)},
        )

        assert ahrefs.calls == []
        assert result.status is DonorStatus.UNSUITABLE
        assert result.reason == "ключей 100 ниже 300"


class NoSerp:
    """Выдача уже куплена (`RunRequest.candidates`): источник звать незачем."""

    name = "none"

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        raise AssertionError("выдача уже куплена — источник не зовут")


async def _run_twice(
    session: AsyncSession,
    ahrefs: Ahrefs,
    *,
    countries_again: bool = True,
    between: Callable[[], Awaitable[None]] | None = None,
) -> tuple[Any, Any]:
    settings = await RunRepository(session).create_settings(
        T,
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    deps = RunDeps(
        provider=NoSerp(),
        client=ahrefs.client(),
        donors=DonorRepository(session),
        runs=RunRepository(session),
        exclusions=Exclusions(session),
    )
    # Выдача — готовая: `*.example.test` нормализация адресов выдачи не пропустила бы.
    bought = Candidates(
        hosts=["half.example.test"], keywords=1, results=1, empty_keywords=[], dropped=0
    )
    request = RunRequest(["crm"], "us", T, settings.id, candidates=bought)
    ahrefs.countries = False
    first = await execute_run(deps, request)
    if between is not None:
        await between()
    ahrefs.countries = countries_again
    ahrefs.calls.clear()
    second = await execute_run(deps, request)
    return first, second


class TestTheNextRunFinishesTheJob:
    async def test_second_run_buys_only_the_countries(self, session: AsyncSession) -> None:
        """Сквозной путь на базе: первый прогон — «страны не получены», второй
        добирает страны, а за метрики не платит."""
        ahrefs = Ahrefs({"half.example.test": GOOD})

        first, second = await _run_twice(session, ahrefs)

        assert first.unchecked_reasons == {COUNTRIES_FAILED: 1}
        assert second.plan.new == ["half.example.test"], "без стран — не свежий"
        assert ahrefs.calls == [("country", "half.example.test")], "метрики уже куплены"
        assert second.countries_refetched == 1
        donor = await _donor(session, "half.example.test")
        assert donor.status is DonorStatus.SUITABLE
        assert donor.reject_reason is None
        assert donor.geo_refreshed_at is not None
        assert donor.metrics_refreshed_at < donor.geo_refreshed_at, "срок метрик — от покупки"

    async def test_run_record_names_why_unchecked(self, session: AsyncSession) -> None:
        ahrefs = Ahrefs({"half.example.test": GOOD})

        await _run_twice(session, ahrefs)

        runs = (await session.execute(select(RunModel).order_by(RunModel.id))).scalars().all()
        assert runs[0].stats["unchecked_reasons"] == {COUNTRIES_FAILED: 1}
        assert runs[0].stats["countries_refetched"] == 0
        assert runs[1].stats["countries_refetched"] == 1

    async def test_countries_failing_again_keep_the_purchase(self, session: AsyncSession) -> None:
        """Страны упали и во второй раз: метрики не куплены заново, срок их — от первой
        покупки, домен по-прежнему «не проверен» без стран — его доберёт следующий."""
        ahrefs = Ahrefs({"half.example.test": GOOD})
        bought_at = datetime.now(UTC) - timedelta(days=30)

        async def bought_a_month_ago() -> None:
            await session.execute(update(DonorModel).values(metrics_refreshed_at=bought_at))
            await session.flush()

        first, second = await _run_twice(
            session, ahrefs, countries_again=False, between=bought_a_month_ago
        )
        donor = await _donor(session, "half.example.test")
        await session.refresh(donor)

        # Отказ 500 клиент повторяет — запросы только по странам, ступени 1–2 не платятся.
        assert set(ahrefs.calls) == {("country", "half.example.test")}
        assert second.countries_refetched == 1, "добирали — не значит добрали"
        assert (donor.status, donor.reject_reason) == (DonorStatus.UNCHECKED, COUNTRIES_FAILED)
        assert donor.geo_refreshed_at is None
        assert donor.metrics_refreshed_at == bought_at, "срок метрик — от первой покупки"
        bought = await DonorRepository(session).paid_metrics(["half.example.test"])
        assert bought["half.example.test"].bought_at == bought_at, "следующий доберёт"
        assert first.plan.new == second.plan.new == ["half.example.test"]
