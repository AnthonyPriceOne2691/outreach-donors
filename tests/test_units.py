"""Учёт расхода и предрасчёт сметы — то, что не даёт выжечь месячный лимит."""

from __future__ import annotations

import pytest
from backend.features.ahrefs.units import (
    COUNTRY_CALL_SHARE,
    MIN_REQUEST_UNITS,
    UNITS_BY_COUNTRY,
    UNITS_DR_SCREEN,
    UNITS_FULL_METRICS,
    UnitsCost,
    batch_cost,
    estimate_run,
)
from backend.features.core.domain import DonorStatus, RunStatus, Stage
from backend.features.donors.collect import COUNTRIES_FAILED
from backend.features.donors.geo import NO_COUNTRY_DATA, CountryShare, check_geo
from backend.features.donors.verdict import Thresholds
from backend.features.runs.planning import Candidates, RunPlan
from backend.features.runs.report import RunReport, run_stats
from backend.features.runs.repository import RunRepository, country_share_from, reached_countries
from sqlalchemy.ext.asyncio import AsyncSession


class TestUnitsCost:
    def test_reads_all_three_headers(self) -> None:
        cost = UnitsCost.from_headers(
            {
                "x-api-units-cost-total-actual": "55",
                "x-api-units-cost-total": "55",
                "x-api-units-cost-row": "11",
            }
        )
        assert cost.actual == 55
        assert cost.per_row == 11
        assert cost.billable == 55

    def test_missing_headers_give_none_not_zero(self) -> None:
        """Ноль означал бы «бесплатно». Отсутствие означает «неизвестно»,
        и это разные вещи для учёта."""
        cost = UnitsCost.from_headers({})
        assert cost.actual is None
        assert cost.estimated is None

    def test_garbage_headers_do_not_crash(self) -> None:
        cost = UnitsCost.from_headers({"x-api-units-cost-total-actual": "не число"})
        assert cost.actual is None

    def test_falls_back_to_estimate_when_actual_is_absent(self) -> None:
        cost = UnitsCost.from_headers({"x-api-units-cost-total": "42"})
        assert cost.billable == 42


class TestBatchCost:
    def test_minimum_applies_to_small_batches(self) -> None:
        """Замер: пачка из 10 доменов на просеве стоит 50 юнитов, а не 20."""
        assert batch_cost(10, UNITS_DR_SCREEN) == MIN_REQUEST_UNITS
        assert batch_cost(10, UNITS_DR_SCREEN) / 10 == 5.0

    def test_full_batch_escapes_the_minimum(self) -> None:
        """А пачка из 100 — ровно 2 юнита на домен, как и замерено."""
        assert batch_cost(100, UNITS_DR_SCREEN) == 200
        assert batch_cost(100, UNITS_DR_SCREEN) / 100 == 2.0

    def test_empty_batch_is_free(self) -> None:
        assert batch_cost(0, UNITS_DR_SCREEN) == 0


class TestRunEstimate:
    def test_estimate_is_an_upper_bound_on_metrics(self) -> None:
        """Полные метрики считаются на все новые домены: просев по DR
        отсекает 0–15%, а заниженная смета значит, что кап не держит трату.
        Было 83% — и смета занижала трату в 14 прогонах из 14, до +132%."""
        estimate = estimate_run(1000, country_share=0.0)

        assert estimate.metrics == 10 * 100 * UNITS_FULL_METRICS
        assert estimate.by_country == 0

    def test_battle_run_is_not_underestimated(self) -> None:
        """Прогон 23.09.2026: 508 новых доменов США, списано 16 998. Запрос по
        странам тогда шёл 8% доменов — тех, у кого верхняя страна пакета чужая;
        арифметика сметы с этой долей покрывает списанное."""
        estimate = estimate_run(508, country_share=0.079)

        assert estimate.total >= 16_998
        assert estimate.total < 16_998 * 1.1

    def test_more_country_calls_cost_more(self) -> None:
        """Страна, где пороги проходит больше доменов, дороже: с 16.10.2026
        запрос по странам — каждому прошедшему."""
        weak = estimate_run(10_000, country_share=0.62)
        strong = estimate_run(10_000, country_share=0.96)
        assert strong.total > weak.total

    def test_only_new_domains_are_billed(self) -> None:
        """За домены со свежими данными уже заплачено, в смету
        они не входят — иначе сервис будет пугать ценой повторных прогонов."""
        assert estimate_run(0).total == 0

    @pytest.mark.parametrize("domains", [1, 50, 99, 100, 101, 1000])
    def test_estimate_never_below_the_request_minimum(self, domains: int) -> None:
        """Даже один домен стоит не меньше минимума на запрос."""
        assert estimate_run(domains).screen >= MIN_REQUEST_UNITS

    def test_by_country_has_no_batch_discount(self) -> None:
        """Пакетного аналога у запроса по странам нет — цена линейна."""
        estimate = estimate_run(1000, country_share=0.5)

        assert estimate.by_country == 500 * UNITS_BY_COUNTRY


class TestCachedResponses:
    """Ahrefs кэширует ответы у себя и за повторный запрос берёт ноль.

    Ловушка в том, что «бесплатно» и «неизвестно» выглядят одинаково, если
    смотреть только на billable. Моки в остальных тестах всегда возвращают
    цену, поэтому этот случай они не видят — он пришёл с боевого прогона.
    """

    def test_zero_actual_with_estimate_is_a_free_request(self) -> None:
        cost = UnitsCost.from_headers(
            {"x-api-units-cost-total-actual": "0", "x-api-units-cost-total": "55"}
        )
        assert cost.known
        assert cost.was_free
        assert cost.billable == 0

    def test_no_headers_is_unknown_not_free(self) -> None:
        """Разница принципиальная: бесплатный запрос записывается в журнал,
        неизвестный — повод для предупреждения."""
        cost = UnitsCost.from_headers({})
        assert not cost.known
        assert not cost.was_free

    def test_paid_request_is_not_free(self) -> None:
        cost = UnitsCost.from_headers(
            {"x-api-units-cost-total-actual": "55", "x-api-units-cost-total": "55"}
        )
        assert cost.known
        assert not cost.was_free
        assert cost.billable == 55


def _passed(
    checked: int,
    suitable: int,
    *,
    region: int = 0,
    failed: int = 0,
    empty: int = 0,
    calls: int = 0,
    code: str = "US",
) -> dict[str, object]:
    """Отчёт прогона (`report.run_stats`): сколько проверено, вердикты и причины.

    Остальные проверенные — отсеяны порогом: «DR 5 ниже 20» и прочие первые слова.
    `calls` — запросов по странам, как их записал журнал прогона.
    """
    thresholds = checked - suitable - region - failed - empty
    return {
        "checked_now": checked,
        "by_status": {
            "suitable": suitable,
            "unsuitable": region + thresholds,
            "unchecked": failed + empty,
        },
        "reject_reasons": {code: region, "DR": thresholds} if region else {"DR": thresholds},
        "unchecked_reasons": {COUNTRIES_FAILED: failed, NO_COUNTRY_DATA: empty},
        "units_by_operation": {"by_country": calls * UNITS_BY_COUNTRY},
    }


class TestCountryShareFromHistory:
    """Доля доменов с запросом по странам — с 16.10.2026 доля прошедших пороги метрик.

    Запрос по странам идёт каждому прошедшему (топ-5 стран — поле донора по ТЗ),
    и доля берётся из вердиктов своих прогонов, а не из числа самих запросов:
    до 16.10 их было меньше прошедших, и старые прогоны занизили бы смету.
    """

    def test_passed_thresholds_not_paid_calls(self) -> None:
        """Боевой прогон 23.09.2026: 508 новых доменов США, запросов по странам —
        40 (верхняя страна пакета избавляла от остальных), пороги прошли 389."""
        battle = _passed(508, 389, calls=40)

        assert country_share_from([("us", battle)], "us") == pytest.approx(389 / 508)

    def test_region_and_countries_unchecked_count_as_reached(self) -> None:
        """Дошёл до стран — годный, отказ по региону (кодом страны прогона, у старых
        записей — строчными) и «не проверен» по странам; отсеянный порогом — нет."""
        stats = _passed(100, 50, region=10, failed=5, empty=5, code="us")

        assert reached_countries(stats, "US") == 70
        assert country_share_from([("us", stats)], "us") == pytest.approx(0.7)

    def test_the_worst_of_the_recent_runs(self) -> None:
        """Худшая, а не средняя — как у уникальных доменов: смета — верхняя граница."""
        newest_first = [("us", _passed(100, 60)), ("us", _passed(100, 90))]

        assert country_share_from(newest_first, "us") == pytest.approx(0.9)

    def test_window_washes_out_old_runs(self) -> None:
        """Недавние — пока не наберётся 500 новых доменов: старое вымывается без дат."""
        newest_first = [("us", _passed(508, 389)), ("us", _passed(83, 80))]

        assert country_share_from(newest_first, "us") == pytest.approx(389 / 508)

    def test_small_runs_are_noise(self) -> None:
        """Шестнадцать доменов — не число: один домен в них — шесть процентов."""
        assert country_share_from([("mx", _passed(16, 16))], "mx") == COUNTRY_CALL_SHARE
        newest_first = [("mx", _passed(16, 16)), ("mx", _passed(40, 20))]
        assert country_share_from(newest_first, "mx") == pytest.approx(0.5)

    def test_other_countries_do_not_transfer(self) -> None:
        """В региональной выдаче сайты слабее: доля чужой страны — не своя."""
        history = [("us", _passed(500, 400)), ("vn", _passed(300, 250, code="VN"))]

        assert country_share_from(history, "fr") == COUNTRY_CALL_SHARE

    def test_runs_without_checks_are_skipped(self) -> None:
        history = [("us", {"checked_now": 0}), ("us", _passed(100, 80))]

        assert country_share_from(history, "us") == pytest.approx(0.8)

    def test_without_history_the_share_errs_high_but_not_blind(self) -> None:
        """Без своей истории — у верхнего края замеров (70–97% в 16 прогонах),
        но не единица: смета на единице не пускала бы прогоны, которые помещаются."""
        assert 0.85 <= COUNTRY_CALL_SHARE < 1.0

    def test_counts_the_words_a_run_really_writes(self) -> None:
        """Сверка со словами вердиктов, а не с образцом теста: причина отказа по
        региону — от `check_geo`, первым словом — как пишет отчёт (`RunReport.record`).
        Поменяют слова — покраснеет здесь, а не смета тихо занизит страны."""
        candidates = Candidates(
            hosts=[f"d{i}.com" for i in range(6)],
            keywords=1,
            results=6,
            empty_keywords=[],
            dropped=0,
        )
        plan = RunPlan(candidates, fresh=[], new=list(candidates.hosts), estimate=estimate_run(6))
        report = RunReport(plan=plan)
        report.record(DonorStatus.SUITABLE, check_geo("us", [CountryShare("us", 9, 0.9)]).reason)
        report.record(DonorStatus.UNSUITABLE, check_geo("us", [CountryShare("de", 9, 0.9)]).reason)
        report.record(DonorStatus.UNCHECKED, COUNTRIES_FAILED)
        report.record(DonorStatus.UNCHECKED, check_geo("us", []).reason)
        report.record(DonorStatus.UNSUITABLE, "DR 5 ниже 20")
        report.record(DonorStatus.UNCHECKED, "Ahrefs не знает домен")

        assert reached_countries(run_stats(report, None), "us") == 4


class TestCountryShareInTheBase:
    """Смета берёт долю из записанных прогонов своей страны — только законченных:
    у идущего и остановленного отчёт неполный, и их доля — не доля."""

    async def test_only_finished_runs_of_the_country(self, session: AsyncSession) -> None:
        repo = RunRepository(session)
        settings = await repo.create_settings(
            Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300),
            geo_top_n=5,
            geo_min_share=0.2,
            metrics_ttl_days=90,
            price_ttl_days=150,
            units_cap=100_000,
        )
        history = (
            (RunStatus.DONE, "us", _passed(100, 60)),
            (RunStatus.RUNNING, "us", _passed(100, 100)),
            (RunStatus.STOPPED, "us", _passed(100, 100)),
            (RunStatus.DONE, "za", _passed(100, 95, code="ZA")),
        )
        for status, country, stats in history:
            run = await repo.create_run(
                stage=Stage.DONORS,
                settings_id=settings.id,
                keywords=["crm"],
                country=country,
                status=status,
            )
            await repo.save_stats(run, stats)

        assert await repo.country_call_share("US") == pytest.approx(0.6)
        assert await repo.country_call_share("za") == pytest.approx(0.95)
        assert await repo.country_call_share("fr") == COUNTRY_CALL_SHARE
