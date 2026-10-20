"""Вердикт по сохранённым данным — страной и порогами прогона, который спрашивает.

Судят те же функции, что и замер: поэтому сначала сверка с самим замером — купленные
и записанные домены, пересуженные той же страной и теми же порогами, получают тот же
вердикт слово в слово. Дальше — чего замер не купил: пересуд не выдумывает, а говорит
«не проверен» и почему.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core.domain import DonorStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.donors.collect import COUNTRIES_FAILED, DomainResult, collect
from backend.features.donors.geo import NO_COUNTRY_DATA, CountryShare, GeoRule, GeoVerdict
from backend.features.donors.rejudge import NO_DR, NOT_ASKED, ONLY_DR, PARTIAL, Stored, rejudge
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Metrics, Thresholds, big_site_bound, big_site_reason
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
RULE = GeoRule(top_n=5, min_share=0.2)
COST = {"x-api-units-cost-total-actual": "10"}
METRICS = {"domain_rating": 40, "org_traffic": 9000, "refdomains": 500, "org_keywords": 2000}
#: Пять стран по убыванию трафика — так их записывает замер.
TOP5 = [
    {"country": "us", "org_traffic": 5000, "share": 0.5},
    {"country": "gb", "org_traffic": 2000, "share": 0.2},
    {"country": "in", "org_traffic": 1000, "share": 0.1},
    {"country": "au", "org_traffic": 600, "share": 0.06},
    {"country": "de", "org_traffic": 400, "share": 0.04},
]


def _full(**changes: Any) -> Stored:
    """Домен, прошедший все ступени замера: метрики и топ-5 стран."""
    stored = Stored(
        dr=40.0,
        org_traffic=9000,
        metrics=METRICS,
        breakdown=TOP5,
        countries_asked=True,
    )
    return replace(stored, **changes)


class TestTheSameRulesAsTheMeasurement:
    @pytest.fixture(autouse=True)
    def _no_backoff(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def instant(_seconds: float) -> None:
            return None

        monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", instant)

    @pytest.mark.parametrize("country", ["us", "gb", "ca"])
    async def test_stored_data_judged_again_gives_the_measured_verdict(
        self, session: AsyncSession, country: str
    ) -> None:
        """Замер и пересуд — одно правило: те же страна и пороги дают тот же вердикт.
        Домены — на каждую ступень: отсев по DR, крупный, отсев по трафику, регион."""
        metrics = {
            "weak.test": {"domain_rating": 5},
            "huge.test": {"domain_rating": 92},
            "quiet.test": {**METRICS, "org_traffic": 90},
            "wide.test": METRICS,
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("batch-analysis"):
                hosts = [t["url"] for t in json.loads(request.content)["targets"]]
                rows = [{"url": f"{h}/", **metrics[h]} for h in hosts if h in metrics]
                return httpx.Response(200, json={"domains": rows}, headers=COST)
            rows = [{"country": row["country"], "org_traffic": row["org_traffic"]} for row in TOP5]
            return httpx.Response(200, json={"metrics": rows}, headers=COST)

        http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://a.test")
        results: list[DomainResult] = []
        async for batch in collect(list(metrics), AhrefsClient(api_key="k", http=http), T, country):
            results.extend(batch)
        repository = DonorRepository(session)
        await repository.save_results(results)

        for result in results:
            donor = await session.scalar(
                select(DonorModel).join(DomainModel).where(DomainModel.host == result.host)
            )
            assert donor is not None
            again = rejudge(Stored.of(donor), T, country, RULE)
            assert (again.status, again.reason) == (result.status, result.reason), result.host


class TestThresholdsOfTheRun:
    def test_low_dr_is_named_with_the_run_threshold(self) -> None:
        verdict = rejudge(_full(dr=24.0), replace(T, min_dr=25), "us", RULE)

        assert (verdict.status, verdict.reason) == (DonorStatus.UNSUITABLE, "DR 24 ниже 25")

    def test_big_site_is_cut_by_the_run_bound(self) -> None:
        verdict = rejudge(_full(dr=76.0), replace(T, max_dr=75), "us", RULE)

        assert (verdict.status, verdict.reason) == (DonorStatus.UNSUITABLE, big_site_reason(75))

    def test_the_exact_dr_of_the_answer_wins_over_the_rounded_column(self) -> None:
        """Колонка DR — целым, в ответе пакета — с дробью: 80,4 — уже крупный."""
        donor = DonorModel(
            dr=80,
            org_traffic=9000,
            metrics={**METRICS, "domain_rating": 80.4},
            geo_breakdown=TOP5,
            geo_refreshed_at=datetime.now(UTC),
        )

        verdict = rejudge(Stored.of(donor), T, "us", RULE)

        assert verdict.reason == big_site_reason(80)

    def test_a_big_site_cut_at_the_first_step_stays_big(self) -> None:
        """DR 80,4 лежит в колонке числом 80, а ответа пакета у отсеянного первой ступенью
        нет: крупного пересуд узнаёт по причине замера — «DR > 80» (ревью 20.10.2026)."""
        donor = DonorModel(dr=80, measured_status="unsuitable", measured_reason=big_site_reason(80))

        verdict = rejudge(Stored.of(donor), T, "us", RULE)

        assert (verdict.status, verdict.reason) == (DonorStatus.UNSUITABLE, big_site_reason(80))
        assert rejudge(Stored.of(donor), replace(T, max_dr=75), "us", RULE).reason == (
            big_site_reason(75)
        )

    def test_a_higher_bound_than_the_measured_one_cannot_tell(self) -> None:
        """Порог крупного выше того, что видел замер: DR 80,4 при пороге 85 — не крупный, а
        других метрик ему не покупали — «не проверен», не догадка."""
        donor = DonorModel(dr=80, measured_status="unsuitable", measured_reason=big_site_reason(80))

        verdict = rejudge(Stored.of(donor), replace(T, max_dr=85), "us", RULE)

        assert (verdict.status, verdict.reason) == (DonorStatus.UNCHECKED, ONLY_DR)

    def test_bound_is_read_only_from_the_big_site_reason(self) -> None:
        assert big_site_bound(big_site_reason(80)) == 80
        assert big_site_bound("DR 12 ниже 20") is None
        assert big_site_bound("DR > много — крупным сайтам не пишем") is None
        assert big_site_bound(None) is None

    def test_weak_traffic_is_named(self) -> None:
        verdict = rejudge(_full(org_traffic=90), T, "us", RULE)

        assert (verdict.status, verdict.reason) == (
            DonorStatus.UNSUITABLE,
            "органический трафик 90 ниже 500",
        )


class TestCountryOfTheRun:
    def test_second_country_fits_the_top_five(self) -> None:
        verdict = rejudge(_full(), T, "gb", RULE)

        assert verdict.status is DonorStatus.SUITABLE
        assert verdict.reason == "GB на 2-м месте по трафику (20%)"

    def test_a_country_outside_the_top_is_a_region_refusal(self) -> None:
        verdict = rejudge(_full(), T, "ca", RULE)

        assert (verdict.status, verdict.reason) == (
            DonorStatus.UNSUITABLE,
            "CA не входит в топ-5 и даёт меньше 20%",
        )

    def test_a_narrower_top_of_the_run_version_is_respected(self) -> None:
        """Топ-3 версии прогона: четвёртая страна — отказ, если её доля ниже порога."""
        verdict = rejudge(_full(), T, "au", GeoRule(top_n=3, min_share=0.4))

        assert verdict.status is DonorStatus.UNSUITABLE

    def test_case_of_the_country_does_not_matter(self) -> None:
        assert rejudge(_full(), T, "GB", RULE).status is DonorStatus.SUITABLE


class TestWhatTheMeasurementDidNotBuy:
    def test_without_dr_the_measured_reason_stays(self) -> None:
        verdict = rejudge(
            Stored(None, None, None, None, reason="Ahrefs не знает домен"), T, "us", RULE
        )

        assert (verdict.status, verdict.reason) == (DonorStatus.UNCHECKED, "Ahrefs не знает домен")

    def test_without_dr_and_reason_says_so(self) -> None:
        verdict = rejudge(Stored(None, None, None, None), T, "us", RULE)

        assert (verdict.status, verdict.reason) == (DonorStatus.UNCHECKED, NO_DR)

    def test_dr_only_under_softer_thresholds_is_not_guessed(self) -> None:
        """Отсеянному по DR остальных метрик не покупали: мягче порог DR пропускает его
        дальше, а судить там нечем — «не проверен», а не отказ по догадке."""
        verdict = rejudge(
            Stored(dr=18.0, org_traffic=None, metrics=None, breakdown=None),
            replace(T, min_dr=15),
            "us",
            RULE,
        )

        assert (verdict.status, verdict.reason) == (DonorStatus.UNCHECKED, ONLY_DR)

    def test_dr_only_still_low_is_a_refusal(self) -> None:
        verdict = rejudge(
            Stored(dr=12.0, org_traffic=None, metrics=None, breakdown=None), T, "us", RULE
        )

        assert (verdict.status, verdict.reason) == (DonorStatus.UNSUITABLE, "DR 12 ниже 20")

    def test_metrics_without_countries_are_not_a_region_verdict(self) -> None:
        """Отсеянному порогами по метрикам стран не спрашивали."""
        stored = _full(breakdown=None, countries_asked=False, status=DonorStatus.UNSUITABLE)

        verdict = rejudge(stored, T, "us", RULE)

        assert (verdict.status, verdict.reason) == (DonorStatus.UNCHECKED, NOT_ASKED)

    def test_countries_that_failed_are_named_so(self) -> None:
        """Прошедшему все метрики не удался запрос по странам — «страны не получены», а не
        «не спрашивали: прежние пороги отсеяли» (ревью 20.10.2026)."""
        for status in (DonorStatus.UNCHECKED, None):
            stored = _full(breakdown=None, countries_asked=False, status=status)

            verdict = rejudge(stored, T, "us", RULE)

            assert (verdict.status, verdict.reason) == (DonorStatus.UNCHECKED, COUNTRIES_FAILED)

    def test_countries_asked_and_empty_is_no_data(self) -> None:
        verdict = rejudge(_full(breakdown=[]), T, "us", RULE)

        assert (verdict.status, verdict.reason) == (DonorStatus.UNCHECKED, NO_COUNTRY_DATA)

    def test_partial_breakdown_knows_only_its_top_country(self) -> None:
        partial = _full(breakdown=TOP5[:1], partial=True)

        assert rejudge(partial, T, "us", RULE).status is DonorStatus.SUITABLE
        assert (rejudge(partial, T, "gb", RULE).status, rejudge(partial, T, "gb", RULE).reason) == (
            DonorStatus.UNCHECKED,
            PARTIAL,
        )

    def test_unreadable_rows_do_not_break_the_verdict(self) -> None:
        """Строка без страны или с долей-флагом пропускается, а не роняет пересуд."""
        rows: list[Any] = [
            "мусор",
            {"country": None, "share": 0.9},
            {"country": "fr", "share": True},
            {"country": "fr", "share": "много"},
            *TOP5,
        ]

        verdict = rejudge(_full(breakdown=rows), T, "gb", RULE)

        assert verdict.reason == "GB на 2-м месте по трафику (20%)"


class TestWhatTheDonorKeeps:
    def test_the_verdict_of_the_measurement_is_read_not_the_one_for_the_outputs(self) -> None:
        """У принятого донора `status` — для выходов: замер мог сказать «не подходит» с
        причиной, а принятый годен. Пересуд берёт вердикт и причину замера."""
        donor = DonorModel(
            dr=40,
            org_traffic=9000,
            metrics=METRICS,
            status=DonorStatus.SUITABLE,
            reject_reason=None,
            measured_status="unsuitable",
            measured_reason="CA не входит в топ-5 и даёт меньше 20%",
        )

        stored = Stored.of(donor)

        assert (stored.status, stored.reason) == (
            DonorStatus.UNSUITABLE,
            "CA не входит в топ-5 и даёт меньше 20%",
        )
        assert rejudge(stored, T, "us", RULE).reason == NOT_ASKED

    def test_an_unknown_word_in_the_column_is_no_verdict(self) -> None:
        donor = DonorModel(dr=40, metrics=METRICS, org_traffic=9000, measured_status="странное")

        assert Stored.of(donor).status is None

    def test_a_result_of_the_collect_is_judged_like_the_stored_donor(self) -> None:
        """Новый замер судят и для прогона, где донора приняли (`donors/held.py`): те же
        данные, что лягут на донора."""
        shares = [CountryShare(row["country"], row["org_traffic"], row["share"]) for row in TOP5]
        result = DomainResult(
            "wide.test",
            DonorStatus.UNSUITABLE,
            "CA не входит в топ-5 и даёт меньше 20%",
            Metrics(dr=40.0, org_traffic=9000, refdomains=500, org_keywords=2000),
            geo=GeoVerdict(False, "CA не входит в топ-5 и даёт меньше 20%", "us", shares),
            raw=METRICS,
        )

        stored = Stored.of_result(result)

        assert rejudge(stored, T, "gb", RULE).reason == "GB на 2-м месте по трафику (20%)"
        assert stored.breakdown == TOP5
