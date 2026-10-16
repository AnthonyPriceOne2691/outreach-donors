"""Правило региона и срок метрик — из версии настроек прогона, а не из окружения.

Сверка ТЗ 11.10.2026: топ-N и доля брались из конфига (`config/filters.py` →
`collect`, `geo`), срок годности метрик — тоже, а поле `run_settings.metrics_ttl_days`
только записывалось. Проверка `top_n × доля ≥ 1` звалась только из консоли. Теперь
прогон судит версией своей строки настроек, а несогласованное правило — отказ словами
на нажатии «Запустить» и в задаче, до первой траты.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from backend.config.startup_checks import ConfigError
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core.domain import DonorStatus, RunStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.run import RunSettingsModel
from backend.features.donors.collect import DomainResult, collect
from backend.features.donors.geo import GeoRule, assert_settings_allow_limited_fetch
from backend.features.donors.repository import DonorRepository
from backend.features.donors.verdict import Metrics, Thresholds
from backend.features.runs.exclusions import Exclusions
from backend.features.runs.pipeline import RunDeps, RunRequest, execute_run
from backend.features.runs.planning import Candidates
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import (
    Rules,
    ThresholdsRepository,
    default_rules,
    defaults,
    rules_in_force,
    rules_of,
)
from backend.workers import jobs
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tests.conftest import bearer
from tests.test_ahrefs_cap import known_key

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
GOOD = {"domain_rating": 40, "org_traffic": 9000, "refdomains": 500, "org_keywords": 2000}
#: США третьей с 11%: топ-5 и 20% пропускают, «топ-2 или половина» — нет.
THIRD_US = [
    {"country": "de", "org_traffic": 6000},
    {"country": "gb", "org_traffic": 2000},
    {"country": "us", "org_traffic": 1000},
]
QUOTA = {
    "units_limit_workspace": 6_400_000,
    "units_usage_workspace": 0,
    "units_limit_api_key": 1_600_000,
    "units_usage_api_key": 0,
}
OWN = {"geo_top_n": 2, "geo_min_share": 0.5, "metrics_ttl_days": 30, "price_ttl_days": 120}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", instant)


async def _version(session: AsyncSession, **columns: Any) -> RunSettingsModel:
    """Действующая версия настроек — последней строкой, как её заводит запуск."""
    return await RunRepository(session).create_settings(
        T, **{**default_rules().columns(), **columns}, units_cap=100_000
    )


class TestTheVersionCarriesTheRules:
    async def test_without_a_version_the_config_stands(self, session: AsyncSession) -> None:
        assert await rules_in_force(session) == default_rules()

    async def test_the_last_version_is_in_force(self, session: AsyncSession) -> None:
        await _version(session, **OWN)

        rules = await rules_in_force(session)

        assert rules.geo == GeoRule(top_n=2, min_share=0.5)
        assert (rules.metrics_ttl_days, rules.price_ttl_days) == (30, 120)

    async def test_new_thresholds_carry_the_rest_of_the_version(
        self, session: AsyncSession
    ) -> None:
        """Экран «Пороги» правит четыре порога; правило региона и сроки — переносятся."""
        await _version(session, **OWN)

        saved = await ThresholdsRepository(session).save(
            Thresholds(30, 600, 120, 310), author="оператор@site.example.test"
        )

        assert rules_of(saved).columns() == OWN


class TestTheRuleIsChecked:
    @pytest.mark.parametrize(("top_n", "share"), [(6, 0.5), (0, 1.0), (7, 0.2)])
    def test_a_top_wider_than_the_answer_is_refused(self, top_n: int, share: float) -> None:
        """Ahrefs спрашиваем о пяти странах: «седьмая — в топ-7» в ответе не увидеть."""
        with pytest.raises(ConfigError, match="не проверить"):
            assert_settings_allow_limited_fetch(top_n=top_n, min_share=share)

    def test_a_narrower_top_with_its_share_passes(self) -> None:
        assert_settings_allow_limited_fetch(top_n=2, min_share=0.5)


class FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id


class FakeQueue:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, path: str, *args: Any, **_: Any) -> FakeJob:
        self.calls.append((path, args))
        return FakeJob("job-из-теста")


@pytest.fixture
async def queue(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> FakeQueue:
    # Ключ Ahrefs известен — снимком, как от его ответа: потолок прогона — от размера ключа.
    await known_key(session, key_limit=100_000)
    monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
    monkeypatch.setattr("backend.config.serp.SANDBOX", False)
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: fake)
    return fake


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.example.test", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.example.test")


BODY = {"keywords": ["ремонт квартир"], "country": "us", "depth_pages": 1}


class TestTheButton:
    async def test_run_settings_copy_the_version_not_the_environment(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue, session: AsyncSession
    ) -> None:
        await _version(session, **OWN)
        await session.commit()

        started = await client.post("/api/runs", json=BODY, headers=bearer(operator_token))

        assert started.status_code == 200, started.text
        run = await RunRepository(session).get(started.json()["run_id"])
        settings = await RunRepository(session).settings_of(run)
        assert rules_of(settings).columns() == OWN

    async def test_an_inconsistent_rule_is_refused_in_words_before_the_queue(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue, session: AsyncSession
    ) -> None:
        await _version(session, geo_top_n=5, geo_min_share=0.1)
        await session.commit()

        refused = await client.post("/api/runs", json=BODY, headers=bearer(operator_token))

        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"].startswith("При топ-5 и пороге доли 10%")
        assert queue.calls == [], "отказ — до очереди"


class _Closable:
    async def aclose(self) -> None:
        return None

    async def dispose(self) -> None:
        return None


async def _no_heartbeat(*args: object, **kwargs: object) -> None:
    return None


@pytest.fixture
async def pipeline(monkeypatch: pytest.MonkeyPatch, session: AsyncSession) -> dict[str, Any]:
    """Задача — в транзакции теста, сбор подменён: вопрос — с чем он позван. Ключ Ahrefs
    известен — снимком: потолок на старте считается от его размера."""
    await known_key(session, key_limit=100_000)
    seen: dict[str, Any] = {}

    async def execute(deps: RunDeps, request: RunRequest) -> Any:
        seen["request"] = request
        plan = type("Plan", (), {"new": [], "estimate": type("E", (), {"total": 0})()})()
        return type("Report", (), {"plan": plan, "by_status": {}, "spent_units": 0})()

    monkeypatch.setattr(jobs, "execute_run", execute)
    monkeypatch.setattr(jobs, "heartbeat", _no_heartbeat)
    monkeypatch.setattr(jobs, "check_collect", lambda: None)
    monkeypatch.setattr(jobs, "AhrefsClient", _Closable)
    monkeypatch.setattr(jobs, "build_provider", lambda client, **_: object())
    monkeypatch.setattr(jobs, "create_async_engine", lambda dsn: _Closable())
    monkeypatch.setattr(
        jobs,
        "async_sessionmaker",
        lambda engine, **kw: async_sessionmaker(bind=session.bind, expire_on_commit=False),
    )
    return seen


async def _queued(session: AsyncSession, **columns: Any) -> Any:
    settings = await _version(session, **columns)
    run = await RunRepository(session).create_run(
        stage=Stage.DONORS, settings_id=settings.id, keywords=["a"], country="us"
    )
    await session.commit()
    return run


class TestTheJob:
    async def test_the_job_collects_with_the_rules_of_its_version(
        self, session: AsyncSession, pipeline: dict[str, Any]
    ) -> None:
        run = await _queued(session, **OWN)

        await jobs._run(run.id)

        request = pipeline["request"]
        assert request.geo == GeoRule(top_n=2, min_share=0.5)
        assert request.metrics_ttl_days == 30

    async def test_an_inconsistent_rule_stops_the_run_with_its_reason(
        self, session: AsyncSession, pipeline: dict[str, Any]
    ) -> None:
        """Строку настроек могли завести до правила — задача судит ей сама и не тратит."""
        run = await _queued(session, geo_top_n=5, geo_min_share=0.1)

        await jobs._search(run.id)
        await session.refresh(run)

        assert "request" not in pipeline, "с неверным правилом сбор не начинается"
        assert run.status is RunStatus.STOPPED
        assert run.stats["причина"].startswith("остановлен: При топ-5 и пороге доли 10%")


class Ahrefs:
    """Провайдер-заглушка: страны — `THIRD_US`, запросы записываются."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        cost = {"x-api-units-cost-total-actual": "10"}
        path = request.url.path
        if path.endswith("limits-and-usage"):
            return httpx.Response(200, json={"limits_and_usage": QUOTA})
        if path.endswith("batch-analysis"):
            hosts = [t["url"] for t in json.loads(request.content)["targets"]]
            self.calls.extend(f"batch:{host}" for host in hosts)
            rows = [{"url": f"{h}/", **GOOD} for h in hosts]
            return httpx.Response(200, json={"domains": rows}, headers=cost)
        self.calls.append(f"country:{dict(request.url.params)['limit']}")
        return httpx.Response(200, json={"metrics": THIRD_US}, headers=cost)

    def client(self) -> AhrefsClient:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self), base_url="https://api.test")
        return AhrefsClient(api_key="k", http=http)


class TestTheCollectorJudgesByTheRule:
    async def _one(self, geo: GeoRule) -> DomainResult:
        ahrefs = Ahrefs()
        out: list[DomainResult] = []
        async for batch in collect(
            ["third.example.test"], ahrefs.client(), T, "us", date="2026-10-11", geo=geo
        ):
            out.extend(batch)
        assert ahrefs.calls[-1] == "country:5", "стран спрашиваем пять при любом топе"
        return out[0]

    async def test_the_default_rule_lets_the_third_country_in(self) -> None:
        assert (await self._one(GeoRule(top_n=5, min_share=0.2))).status is DonorStatus.SUITABLE

    async def test_the_version_rule_decides(self) -> None:
        result = await self._one(GeoRule(top_n=2, min_share=0.5))

        assert result.status is DonorStatus.UNSUITABLE
        assert result.reason == "US не входит в топ-2 и даёт меньше 50%"


class NoSerp:
    name = "none"

    async def search(self, *args: object, **kwargs: object) -> dict[str, Any]:
        raise AssertionError("выдача уже куплена — источник не зовут")


class TestTheRunUsesItsRequest:
    async def _run(self, session: AsyncSession, ahrefs: Ahrefs, rules: Rules) -> Any:
        settings = await _version(session, **rules.columns())
        deps = RunDeps(
            provider=NoSerp(),
            client=ahrefs.client(),
            donors=DonorRepository(session),
            runs=RunRepository(session),
            exclusions=Exclusions(session),
        )
        bought = Candidates(
            hosts=["third.example.test"], keywords=1, results=1, empty_keywords=[], dropped=0
        )
        return await execute_run(
            deps,
            RunRequest(
                ["crm"],
                "us",
                rules.thresholds,
                settings.id,
                geo=rules.geo,
                metrics_ttl_days=rules.metrics_ttl_days,
                candidates=bought,
            ),
        )

    async def test_metrics_older_than_the_version_term_are_bought_again(
        self, session: AsyncSession
    ) -> None:
        """Сорок дней — свежие по умолчанию конфига (90) и несвежие по версии (30)."""
        measured = DomainResult(
            "third.example.test", DonorStatus.SUITABLE, "US на 3-м месте", Metrics(dr=40)
        )
        await DonorRepository(session).save_results(
            [measured], now=datetime.now(UTC) - timedelta(days=40)
        )
        ahrefs = Ahrefs()

        report = await self._run(session, ahrefs, Rules(thresholds=defaults(), metrics_ttl_days=30))

        assert report.plan.new == ["third.example.test"]
        assert "batch:third.example.test" in ahrefs.calls

    async def test_the_geo_rule_of_the_request_reaches_the_verdict(
        self, session: AsyncSession
    ) -> None:
        report = await self._run(
            session, Ahrefs(), Rules(thresholds=defaults(), geo=GeoRule(top_n=2, min_share=0.5))
        )

        assert report.by_status == {DonorStatus.UNSUITABLE: 1}
        assert report.reject_reasons == {"US": 1}


THRESHOLDS = {"min_dr": 20, "min_org_traffic": 500, "min_refdomains": 100, "min_keywords": 300}


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("пороги@site.example.test", role=UserRole.ADMIN)
    return await sign_in("пороги@site.example.test")


class TestTheScreenEditsTheRule:
    """Правило региона и срок метрик правятся на «Порогах» частью версии (ТЗ: «все пороги
    вынесены в настройки прогона»); до 11.10.2026 их задавало только окружение."""

    async def test_saved_rule_is_the_version_the_next_run_takes(
        self, client: AsyncClient, admin_token: str, session: AsyncSession
    ) -> None:
        body = {**THRESHOLDS, "geo_top_n": 3, "geo_min_share_percent": 34, "metrics_ttl_days": 60}

        saved = await client.post(
            "/api/settings/thresholds", json=body, headers=bearer(admin_token)
        )

        assert saved.status_code == 200, saved.text
        assert (saved.json()["geo_top_n"], saved.json()["geo_min_share_percent"]) == (3, 34)
        rules = await rules_in_force(session)
        assert rules.geo == GeoRule(top_n=3, min_share=0.34)
        assert rules.metrics_ttl_days == 60

    async def test_an_inconsistent_rule_is_refused_in_words(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        body = {**THRESHOLDS, "geo_top_n": 5, "geo_min_share_percent": 10}

        refused = await client.post(
            "/api/settings/thresholds", json=body, headers=bearer(admin_token)
        )

        assert refused.status_code == 409, refused.text
        assert refused.json()["detail"].startswith("При топ-5 и пороге доли 10%")

    async def test_thresholds_alone_carry_the_rule_of_the_version(
        self, client: AsyncClient, admin_token: str, session: AsyncSession
    ) -> None:
        """Прежний экран шлёт только пороги — правило и срок остаются действующими."""
        await _version(session, **OWN)
        await session.commit()

        saved = await client.post(
            "/api/settings/thresholds", json=THRESHOLDS, headers=bearer(admin_token)
        )
        view = await client.get("/api/settings/thresholds", headers=bearer(admin_token))

        assert saved.status_code == 200, saved.text
        assert (await rules_in_force(session)).columns() == OWN
        latest = view.json()["history"][0]
        assert (latest["geo_top_n"], latest["metrics_ttl_days"]) == (2, 30)
        assert view.json()["defaults"]["geo_min_share_percent"] == 20
