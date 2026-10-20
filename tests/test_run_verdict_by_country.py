"""Вердикт региона и порогов — у прогона, а не у домена.

Аудит 20.10.2026: вердикт лежал на домене одной строкой без страны и порогов, для
которых его вынесли, а домен со свежими метриками новый прогон не перепроверял
90 дней. Годный для прогона США предлагался в прогоне Канады, отсеянный для Канады
не предлагался в прогоне Великобритании. Теперь свежий домен прогон судит сам — своей
страной и своими порогами по сохранённым метрикам и топ-5 стран, без Ahrefs.

Прогоны — целиком, на настоящей базе; провайдер — заглушка с разбивкой по стране. Домены —
`example.com` и `example.org`: прогон сводит адрес выдачи к домену по списку публичных
суффиксов, и выдуманная зона (`.test`) до замера не дошла бы.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.config.judge import JudgeMode
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core.domain import DonorStatus, RunStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import DonorModel
from backend.features.core.models.run import RunCandidateModel, RunModel, RunVerdictModel
from backend.features.donors.publisher_judge import Intent, Judgement, Recommendation
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Thresholds, big_site_reason
from backend.features.review.candidates import Decision, RunReview
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.report import RunReport
from backend.features.runs.repository import RunRepository
from backend.features.serp.protocol import SerpResult
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
HOST = "example.com"
URL = f"https://{HOST}/a"
QUOTA = {
    "units_limit_workspace": 6_400_000,
    "units_usage_workspace": 0,
    "units_limit_api_key": 1_600_000,
    "units_usage_api_key": 0,
}
GOOD = {"domain_rating": 40, "org_traffic": 10_000, "refdomains": 500, "org_keywords": 2000}
#: Сайт с американской аудиторией: США первыми, Великобритания второй, Канады нет.
US_GB = [("us", 6000), ("gb", 2500), ("in", 600), ("au", 400), ("de", 300)]
#: Тот же сайт позже: Великобритания ушла из его топ-5.
US_ONLY = [("us", 9000), ("in", 600), ("au", 400), ("de", 300), ("fr", 200)]
#: Причина отказа по региону — словами вердикта.
NOT_IN_CA = "CA не входит в топ-5 и даёт меньше 20%"


class Serp:
    name = "fake"

    def __init__(self, urls: list[str]) -> None:
        self._urls = urls

    async def search(
        self, keywords: Sequence[str], country: str, *, depth_pages: int = 1
    ) -> dict[str, list[SerpResult]]:
        return {kw: [SerpResult(i + 1, u) for i, u in enumerate(self._urls)] for kw in keywords}


@pytest.fixture(autouse=True)
def _no_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Судья площадки — выключен: он не предмет этих тестов, а с ключом модели в окружении
    пошёл бы к ней. Тест, которому судья нужен, ставит режим и подменяет вызов сам."""
    monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.OFF)


def _ahrefs(
    metrics: dict[str, dict[str, Any]],
    countries: dict[str, list[tuple[str, int]]],
    asked: list[str],
) -> AhrefsClient:
    """Заглушка Ahrefs: пакет метрик и топ-5 стран по домену. `asked` копит каждый
    платный запрос — «прогон не спросил Ahrefs» проверяется по нему."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        cost = {"x-api-units-cost-total-actual": "10"}
        if path.endswith("limits-and-usage"):
            return httpx.Response(200, json={"limits_and_usage": QUOTA})
        if path.endswith("batch-analysis"):
            hosts = [t["url"] for t in json.loads(request.content)["targets"]]
            asked.extend(f"batch:{host}" for host in hosts)
            rows = [{"url": f"{h}/", **metrics[h]} for h in hosts if h in metrics]
            return httpx.Response(200, json={"domains": rows}, headers=cost)
        host = request.url.params["target"]
        asked.append(f"countries:{host}")
        rows = [{"country": code, "org_traffic": traffic} for code, traffic in countries[host]]
        return httpx.Response(200, json={"metrics": rows}, headers=cost)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://api.test")
    return AhrefsClient(api_key="k", http=http)


async def _run(
    session: AsyncSession,
    country: str,
    urls: list[str],
    client: AhrefsClient,
    thresholds: Thresholds = T,
    *,
    run: RunModel | None = None,
    provider: Serp | None = None,
) -> RunReport:
    """Прогон целиком. `run` — продолжить этот прогон: его версия настроек и выдача."""
    runs = RunRepository(session)
    settings_id = run.settings_id if run is not None else await _settings(runs, thresholds)
    deps = RunDeps(
        provider=provider or Serp(urls),
        client=client,
        donors=DonorRepository(session),
        runs=runs,
        exclusions=Exclusions(session),
        review=RunReview(session),
    )
    request = RunRequest(["saas"], country, thresholds, settings_id, run=run)
    return await execute_run(deps, request)


async def _settings(runs: RunRepository, thresholds: Thresholds) -> int:
    settings = await runs.create_settings(
        thresholds,
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    return settings.id


async def _queued(session: AsyncSession, run_id: int) -> set[str]:
    rows = await session.execute(
        select(DomainModel.host)
        .join(RunCandidateModel, RunCandidateModel.domain_id == DomainModel.id)
        .where(RunCandidateModel.run_id == run_id)
    )
    return set(rows.scalars().all())


async def _last_run(session: AsyncSession) -> RunModel:
    rows = await session.execute(select(RunModel).order_by(RunModel.id.desc()).limit(1))
    return rows.scalar_one()


async def _donor(session: AsyncSession, host: str) -> DonorModel:
    donor = await session.scalar(
        select(DonorModel).join(DomainModel).where(DomainModel.host == host)
    )
    assert donor is not None
    await session.refresh(donor)
    return donor


class TestFreshDomainIsJudgedForTheRunCountry:
    async def test_fit_for_the_us_is_not_offered_in_a_canada_run(
        self, session: AsyncSession
    ) -> None:
        """Годный для США не предлагается в прогоне Канады: Канады в его топ-5 нет."""
        asked: list[str] = []
        client = _ahrefs({HOST: GOOD}, {HOST: US_GB}, asked)

        await _run(session, "us", [URL], client)
        us = await _last_run(session)
        assert await _queued(session, us.id) == {HOST}

        asked.clear()
        report = await _run(session, "ca", [URL], client)
        ca = await _last_run(session)

        assert asked == [], "свежий домен прогон судит сам, без Ahrefs"
        assert report.plan.fresh == [HOST]
        assert await _queued(session, ca.id) == set()
        assert ca.stats is not None
        assert ca.stats["fresh_verdicts"]["by_status"] == {DonorStatus.UNSUITABLE.value: 1}
        assert ca.stats["fresh_verdicts"]["reject_reasons"] == {"CA": 1}

    async def test_rejected_for_canada_is_offered_in_a_uk_run(self, session: AsyncSession) -> None:
        """Отсеянный для Канады предлагается в прогоне Великобритании: она вторая в топ-5."""
        asked: list[str] = []
        client = _ahrefs({HOST: GOOD}, {HOST: US_GB}, asked)

        await _run(session, "ca", [URL], client)
        ca = await _last_run(session)
        assert await _queued(session, ca.id) == set()

        asked.clear()
        await _run(session, "gb", [URL], client)
        gb = await _last_run(session)

        assert asked == []
        assert await _queued(session, gb.id) == {HOST}

    async def test_current_thresholds_judge_a_fresh_domain(self, session: AsyncSession) -> None:
        """Пороги — этого прогона: поднятый DR отсеивает свежий домен, купленный под
        прежние пороги годным, и в очередь он не идёт."""
        asked: list[str] = []
        client = _ahrefs({HOST: GOOD}, {HOST: US_GB}, asked)
        await _run(session, "us", [URL], client)

        stricter = replace(T, min_dr=45)
        await _run(session, "us", [URL], client, thresholds=stricter)
        second = await _last_run(session)

        assert await _queued(session, second.id) == set()
        assert second.stats is not None
        assert second.stats["fresh_verdicts"]["reject_reasons"] == {"DR": 1}


class TestAcceptedOfferIsAWritableDonor:
    async def test_accepted_in_the_uk_run_is_suitable_for_the_outputs(
        self, session: AsyncSession
    ) -> None:
        """Принятый в прогоне Великобритании — годен: адреса и письма смотрят на вердикт
        донора, и он — того прогона, где домен приняли, а не канадского замера."""
        asked: list[str] = []
        client = _ahrefs({HOST: GOOD}, {HOST: US_GB}, asked)
        await _run(session, "ca", [URL], client)
        assert (await _donor(session, HOST)).status is DonorStatus.UNSUITABLE

        await _run(session, "gb", [URL], client)
        gb = await _last_run(session)
        candidate = await session.scalar(
            select(RunCandidateModel).where(RunCandidateModel.run_id == gb.id)
        )
        assert candidate is not None

        await RunReview(session).decide(gb.id, [candidate.id], Decision.ACCEPTED, by="op@test")

        donor = await _donor(session, HOST)
        assert donor.status is DonorStatus.SUITABLE
        assert donor.reject_reason is None
        assert donor.review == Decision.ACCEPTED.value


async def _accept(session: AsyncSession, run: RunModel) -> None:
    candidate = await session.scalar(
        select(RunCandidateModel).where(RunCandidateModel.run_id == run.id)
    )
    assert candidate is not None
    await RunReview(session).decide(run.id, [candidate.id], Decision.ACCEPTED, by="op@team.test")


async def _stale(session: AsyncSession, host: str = HOST) -> None:
    """Метрики донора — старше срока годности: следующий прогон его перемерит."""
    donor = await _donor(session, host)
    donor.metrics_refreshed_at = donor.geo_refreshed_at = datetime.now(UTC) - timedelta(days=91)
    await session.flush()


class TestAnotherCountryDoesNotUndoTheDecision:
    """Ревью 20.10.2026: принятого в прогоне Великобритании через 91 день перемерил прогон
    Канады — и донор стал «не подходит» при `review = accepted`: адрес, письма, добивки и
    перезапрос цены молча вставали. Вердикт Канады — строкой её прогона и вердиктом замера,
    а вердикт для выходов принятому понижает только прогон, где его приняли."""

    async def test_a_canada_remeasure_keeps_the_donor_accepted_in_the_uk(
        self, session: AsyncSession
    ) -> None:
        asked: list[str] = []
        client = _ahrefs({HOST: GOOD}, {HOST: US_GB}, asked)
        await _run(session, "gb", [URL], client)
        await _accept(session, await _last_run(session))
        await _stale(session)

        asked.clear()
        await _run(session, "ca", [URL], client)
        ca = await _last_run(session)

        assert f"countries:{HOST}" in asked, "перемер настоящий: метрики просрочены"
        donor = await _donor(session, HOST)
        assert (donor.status, donor.reject_reason) == (DonorStatus.SUITABLE, None)
        assert donor.review == Decision.ACCEPTED.value
        assert (donor.measured_status, donor.measured_reason) == ("unsuitable", NOT_IN_CA)
        row = await session.scalar(select(RunVerdictModel).where(RunVerdictModel.run_id == ca.id))
        assert row is not None
        assert (row.status, row.cause, row.reason, row.measured) == (
            "unsuitable",
            "geo",
            NOT_IN_CA,
            True,
        )

    async def test_the_uk_verdict_on_new_data_still_lowers_it(self, session: AsyncSession) -> None:
        """Понижает вердикт прогона, где приняли, — по новым данным: Великобритания ушла из
        топ-5 сайта, и для прогона, где его приняли, он больше не годен."""
        countries = {HOST: US_GB}
        client = _ahrefs({HOST: GOOD}, countries, [])
        await _run(session, "gb", [URL], client)
        await _accept(session, await _last_run(session))
        await _stale(session)
        countries[HOST] = US_ONLY

        await _run(session, "ca", [URL], client)

        donor = await _donor(session, HOST)
        assert donor.status is DonorStatus.UNSUITABLE
        assert donor.reject_reason == "GB не входит в топ-5 и даёт меньше 20%"
        assert donor.measured_reason == NOT_IN_CA


class FailingReview(RunReview):
    """Очередь, которая падает: прогон рвётся после оплаченной пачки и продолжится."""

    async def queue_run(self, run_id: int, hosts: Sequence[str]) -> Any:
        raise ConnectionError("база ушла посреди очереди")


class TestContinuationKeepsWhatItBought:
    async def test_a_big_site_bought_before_the_failure_stays_big(
        self, session: AsyncSession
    ) -> None:
        """Ревью 20.10.2026: продолжение застаёт купленное первой попыткой свежим и судило его
        пересудом — крупный сайт с DR 80,4 (в колонке — 80) становился «не проверен», и
        причина «кому не пишем» терялась. Купленное этим прогоном остаётся вердиктом замера."""
        asked: list[str] = []
        big = {"domain_rating": 80.4}
        client = _ahrefs({HOST: big}, {HOST: US_GB}, asked)
        runs = RunRepository(session)
        deps = RunDeps(
            provider=Serp([URL]),
            client=client,
            donors=DonorRepository(session),
            runs=runs,
            exclusions=Exclusions(session),
            review=FailingReview(session),
        )
        with pytest.raises(ConnectionError):
            await execute_run(deps, RunRequest(["saas"], "us", T, await _settings(runs, T)))
        run = await _last_run(session)
        assert run.status is RunStatus.RUNNING, "временный сбой: прогон продолжат"

        asked.clear()
        await _run(session, "us", [URL], client, run=run)

        assert asked == [], "продолжение не покупает купленное"
        row = await session.scalar(select(RunVerdictModel).where(RunVerdictModel.run_id == run.id))
        assert row is not None
        assert (row.status, row.cause, row.reason, row.measured) == (
            "unsuitable",
            "big",
            big_site_reason(80),
            True,
        )
        await session.refresh(run)
        assert run.stats is not None
        assert run.stats["fresh_verdicts"]["reject_reasons"] == {"DR > 80": 1}


class TestJudgeCutIsNotQueuedByOldData:
    async def test_a_new_domain_cut_by_the_judge_is_not_rejudged_into_the_queue(
        self, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Судья режет (`ENFORCE`): отрезанный им новый домен Ahrefs не мерит, и в очередь
        его не кладёт и пересуд по прежним, просроченным данным — годным по ним (ревью
        20.10.2026: очередь брала новых целиком, а не тех, кого прогон мерил)."""
        asked: list[str] = []
        client = _ahrefs({HOST: GOOD}, {HOST: US_GB}, asked)
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.OFF)
        await _run(session, "us", [URL], client)
        await _stale(session)
        asked.clear()

        async def judge(http: object, **kwargs: object) -> Judgement:
            return Judgement(
                Intent.SELLS_OWN, Recommendation.REJECT, "Заголовок", "продаёт своё", "m", 400
            )

        monkeypatch.setattr("backend.features.donors.judging.judge_host", judge)
        monkeypatch.setattr("backend.config.judge.HOME_CHECK", False)
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.ENFORCE)

        await _run(session, "us", [URL], client)
        second = await _last_run(session)

        assert asked == [], "отрезанный судьёй до Ahrefs не доходит"
        assert await _queued(session, second.id) == set()
