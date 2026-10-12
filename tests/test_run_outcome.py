"""Отчёт прогона на экране: проверено, прошло, отсеяно по каждому порогу, юниты и деньги.

Требование ТЗ: «после прогона — отчёт: сколько доменов проверено, сколько прошло
фильтр, сколько отсеяно и по какому порогу, сколько потрачено юнитов и денег». Отчёт
писался, а экран его не показывал (сверка ТЗ 11.10.2026).

Порог отчёт хранит первым словом причины, и перевод в порог держится на словах
вердикта. Поэтому причины здесь пишет сам вердикт (`donors/verdict.py`, `donors/geo.py`),
а не строки теста: поменяют слова — покраснеет этот набор, а не экран тихо назовёт
отсев по DR «другой причиной».
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from decimal import Decimal

import pytest
from backend.features.access.repository import AccessRepository
from backend.features.ahrefs.units import estimate_run
from backend.features.core import usage
from backend.features.core.domain import AuditAction, DonorStatus, RunStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.run import RunModel
from backend.features.donors.geo import CountryShare, GeoVerdict, check_geo
from backend.features.donors.verdict import (
    Metrics,
    Thresholds,
    Verdict,
    check_dr,
    check_metrics,
)
from backend.features.runs.exclusions import ExclusionReason
from backend.features.runs.outcome import Threshold, domains_of, rejections
from backend.features.runs.planning import Candidates, RunPlan
from backend.features.runs.report import RunReport, run_stats
from backend.features.runs.repository import RESUMES_KEY, RunRepository
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

THRESHOLDS = Thresholds(min_dr=25, min_org_traffic=700, min_refdomains=150, min_keywords=400)


def _reason(verdict: Verdict | GeoVerdict | None) -> str:
    """Причина отказа, как её пишет вердикт. Не отказ — тест написан неверно."""
    assert verdict is not None
    refused = verdict.is_rejection if isinstance(verdict, Verdict) else not verdict.passed
    assert refused, verdict
    return verdict.reason


def _measured(*, traffic: int = 900, keywords: int = 900, refdomains: int = 900) -> str:
    """Отказ второй ступени: DR проходит, проваливается один из трёх порогов."""
    metrics = Metrics(dr=40, org_traffic=traffic, refdomains=refdomains, org_keywords=keywords)
    return _reason(check_metrics(metrics, THRESHOLDS))


#: Отказ по каждому порогу — словами самого вердикта.
REFUSALS: list[tuple[Threshold, str]] = [
    (Threshold.DR, _reason(check_dr(12, THRESHOLDS))),
    (Threshold.TRAFFIC, _measured(traffic=90)),
    (Threshold.KEYWORDS, _measured(keywords=9)),
    (Threshold.REFDOMAINS, _measured(refdomains=9)),
    (
        Threshold.GEO,
        _reason(
            check_geo(
                "us",
                [
                    CountryShare("de", 300, 0.3),
                    CountryShare("fr", 200, 0.2),
                    CountryShare("gb", 200, 0.2),
                    CountryShare("it", 100, 0.1),
                    CountryShare("es", 100, 0.1),
                    CountryShare("us", 50, 0.05),
                ],
                top_n=5,
                min_share=0.2,
            )
        ),
    ),
]


class TestThresholdOfReason:
    @pytest.mark.parametrize(("threshold", "reason"), REFUSALS, ids=[t for t, _ in REFUSALS])
    def test_every_threshold_is_found_by_the_verdict_words(
        self, threshold: Threshold, reason: str
    ) -> None:
        report = RunReport(plan=None)  # type: ignore[arg-type]
        report.record(DonorStatus.UNSUITABLE, reason)

        named = {row.threshold: row.count for row in rejections(report.reject_reasons, "us")}

        assert named[threshold] == 1, (reason, report.reject_reasons)
        assert Threshold.OTHER not in named, f"«{reason}» не узнана как порог"

    def test_all_five_thresholds_are_named_even_at_zero(self) -> None:
        """«Реф. домены не отсеяли никого» — тоже ответ на «по какому порогу»."""
        rows = rejections({"DR": 3}, "us")

        assert [(row.threshold, row.count) for row in rows] == [
            (Threshold.DR, 3),
            (Threshold.TRAFFIC, 0),
            (Threshold.KEYWORDS, 0),
            (Threshold.REFDOMAINS, 0),
            (Threshold.GEO, 0),
        ]

    def test_unknown_reason_is_its_own_line_not_lost(self) -> None:
        """Незнакомая причина не пропадает и не прячется в сумму: она называется как
        записана — так видно, что слова вердикта разошлись с экраном."""
        rows = rejections({"DR": 1, "без причины": 2, "спам": 1}, "us")

        other = [(row.said, row.count) for row in rows if row.threshold is Threshold.OTHER]
        assert other == [("без причины", 2), ("спам", 1)]

    def test_region_is_the_run_country_not_any_code(self) -> None:
        """Регион вердикт пишет кодом страны прогона. Код чужой страны — не регион."""
        assert {row.threshold for row in rejections({"GB": 1}, "gb") if row.count} == {
            Threshold.GEO
        }
        assert {row.threshold for row in rejections({"GB": 1}, "us") if row.count} == {
            Threshold.OTHER
        }


def _plan() -> RunPlan:
    """Выдача на шесть доменов: один исключён гейтом, один свежий, четыре новых."""
    hosts = [f"site{i}.example.test" for i in range(6)]
    return RunPlan(
        candidates=Candidates(hosts=hosts, keywords=2, results=9, empty_keywords=[], dropped=0),
        fresh=[hosts[1]],
        new=hosts[2:],
        estimate=estimate_run(4),
        excluded={hosts[0]: ExclusionReason.STOPLIST},
    )


class TestDomainsOfStats:
    def test_report_written_by_the_run_reads_back(self) -> None:
        """Сквозь настоящую запись отчёта (`run_stats`), а не своим словарём: экран
        читает то, что прогон пишет."""
        report = RunReport(plan=_plan())
        report.record(DonorStatus.SUITABLE, "US на 1-м месте по трафику (80%)")
        report.record(DonorStatus.UNSUITABLE, _reason(check_dr(3, THRESHOLDS)))
        report.record(DonorStatus.UNCHECKED, "Ahrefs не знает домен")

        domains = domains_of(run_stats(report, None))

        assert domains is not None
        assert (domains.found, domains.excluded, domains.fresh, domains.to_check) == (6, 1, 1, 4)
        assert (domains.suitable, domains.unsuitable, domains.unchecked) == (1, 1, 1)
        assert domains.checked == 3
        # Четвёртый новый до Ahrefs не дошёл: прогон остановился раньше или его отрезал судья.
        assert domains.not_reached == 1
        assert domains.judge_enforced is False

    def test_no_counts_before_the_attempt_ends(self) -> None:
        """Отчёт пишется в конце попытки: у идущего прогона чисел доменов нет — это не нули."""
        assert domains_of({}) is None
        assert domains_of({"причина": "остановлен до сметы"}) is None

    def test_resumed_run_says_so(self) -> None:
        domains = domains_of({"checked_now": 2, RESUMES_KEY: 1, "judge": {"mode": "enforce"}})

        assert domains is not None
        assert domains.resumes == 1
        assert domains.judge_enforced is True


# --- по HTTP ---


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@team.example.test", role=UserRole.OPERATOR)
    return await sign_in("оператор@team.example.test")


async def _run(session: AsyncSession, *, status: RunStatus = RunStatus.DONE) -> RunModel:
    repository = RunRepository(session)
    settings = await repository.create_settings(
        THRESHOLDS,
        geo_top_n=3,
        geo_min_share=0.25,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    run = await repository.create_run(
        stage=Stage.DONORS,
        settings_id=settings.id,
        keywords=["guest post saas"],
        country="us",
        status=status,
    )
    await session.flush()
    return run


async def _report(client: AsyncClient, token: str, run_id: int) -> dict[str, object]:
    response = await client.get(f"/api/runs/{run_id}/report", headers=bearer(token))
    assert response.status_code == 200, response.text
    body = response.json()
    assert isinstance(body, dict)
    return body


class TestReportRoute:
    async def test_names_domains_thresholds_and_spending(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        run = await _run(session)
        other = await _run(session)
        report = RunReport(plan=_plan())
        report.record(DonorStatus.SUITABLE, "US на 1-м месте по трафику (80%)")
        report.record(DonorStatus.UNSUITABLE, _reason(check_dr(3, THRESHOLDS)))
        report.record(DonorStatus.UNSUITABLE, REFUSALS[-1][1])
        report.record(DonorStatus.UNCHECKED, "Ahrefs не знает домен")
        report.free_by_operation["batch_metrics"] = 2
        run.stats = run_stats(report, None)
        # Журнал прогона — Ahrefs юнитами, выдача деньгами, судья токенами.
        usage.record(session, operation="batch_metrics", units=40, run_id=run.id)
        usage.record(session, operation="batch_metrics", units=28, run_id=run.id)
        usage.record(session, operation="by_country", units=55, run_id=run.id)
        usage.record(session, operation="serp_search", amount_usd=Decimal("0.0012"), run_id=run.id)
        usage.record(session, operation="site_judge", units=3_456, run_id=run.id)
        # Чужое — не этого прогона: соседний прогон и разбор ответа без прогона.
        usage.record(session, operation="batch_metrics", units=999, run_id=other.id)
        usage.record(session, operation="reply_parse", units=777)
        await session.commit()
        # Время правки ставит база: прочитать его сейчас, а не ленивой загрузкой в маршруте.
        await session.refresh(run)

        body = await _report(client, token, run.id)

        assert body["thresholds"] == {
            "min_dr": 25,
            "min_org_traffic": 700,
            "min_refdomains": 150,
            "min_keywords": 400,
            "geo_top_n": 3,
            "geo_min_share": 0.25,
        }
        domains = body["domains"]
        assert isinstance(domains, dict)
        assert {
            key: domains[key] for key in ("checked", "suitable", "unsuitable", "unchecked")
        } == {
            "checked": 4,
            "suitable": 1,
            "unsuitable": 2,
            "unchecked": 1,
        }
        assert (domains["found"], domains["excluded"], domains["fresh"]) == (6, 1, 1)
        assert domains["to_check"] == 4
        assert domains["not_reached"] == 0
        rejected = {row["threshold"]: row["count"] for row in body["rejected"]}  # type: ignore[union-attr]
        assert rejected == {"dr": 1, "org_traffic": 0, "keywords": 0, "refdomains": 0, "geo": 1}
        spending = {
            row["operation"]: (row["provider"], row["units"], row["amount_usd"], row["calls"])
            for row in body["spending"]  # type: ignore[union-attr]
        }
        assert spending == {
            "batch_metrics": ("ahrefs", 68, "0", 2),
            "by_country": ("ahrefs", 55, "0", 1),
            "serp_search": ("serp", 0, "0.0012", 1),
            "site_judge": ("llm", 3_456, "0", 1),
        }
        assert body["free_requests"] == 2
        assert body["run"]["id"] == run.id  # type: ignore[index]

    async def test_running_run_has_spending_but_no_counts_yet(
        self, client: AsyncClient, session: AsyncSession, token: str
    ) -> None:
        """Отчёт пишется в конце попытки, журнал — на каждом чекпоинте: идущий прогон
        уже потратил, а итога по доменам у него нет — и это не нули."""
        run = await _run(session, status=RunStatus.RUNNING)
        usage.record(session, operation="batch_metrics", units=40, run_id=run.id)
        await session.commit()

        body = await _report(client, token, run.id)

        assert body["domains"] is None
        assert body["rejected"] == []
        assert [row["units"] for row in body["spending"]] == [40]  # type: ignore[union-attr]

    async def test_who_started_is_from_the_journal_record_of_the_start(
        self, client: AsyncClient, session: AsyncSession, token: str, make_user: MakeUser
    ) -> None:
        """ТЗ: «кто, когда, сколько потратил». Своей колонки автора у прогона нет — его
        называет запись журнала о запуске (цель «run:N»); без неё — пусто, а не догадка."""
        starter = await make_user("запускал@team.example.test", role=UserRole.OPERATOR)
        started = await _run(session)
        from_console = await _run(session)
        await AccessRepository(session).record(
            AuditAction.RUN_STARTED, author_id=starter.id, target=f"run:{started.id}"
        )
        await session.commit()

        report = await _report(client, token, started.id)
        history = await client.get("/api/runs", headers=bearer(token))

        assert report["run"]["started_by"] == "запускал@team.example.test"  # type: ignore[index]
        authors = {row["id"]: row["started_by"] for row in history.json()["runs"]}
        assert authors[started.id] == "запускал@team.example.test"
        assert authors[from_console.id] is None

    async def test_unknown_run_is_refused_in_words(self, client: AsyncClient, token: str) -> None:
        response = await client.get("/api/runs/987654/report", headers=bearer(token))

        assert response.status_code == 404
        assert "Прогона №987654 нет" in response.text
