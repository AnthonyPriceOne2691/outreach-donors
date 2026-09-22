"""Трёхступенчатый сбор: порядок оплаты, частичный отказ, чекпоинты."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from backend.features.ahrefs.client import AhrefsClient
from backend.features.core.domain import DonorStatus
from backend.features.donors.collect import collect, parse_domain_rating
from backend.features.donors.verdict import Thresholds

T = Thresholds(min_dr=20, min_org_traffic=500, min_refdomains=100, min_keywords=300)
COST = {"x-api-units-cost-total-actual": "10", "x-api-units-cost-row": "2"}


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тесты не ждут пауз между повторами: проверяется поведение, а не часы.
    Без этого один тест с падающим запросом занимает 14 секунд."""

    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.features.ahrefs.client.asyncio.sleep", instant)


class Fake:
    """Провайдер-заглушка. Считает запросы по ступеням — по ним видно,
    что дорогие вызовы делаются только для дошедших доменов."""

    def __init__(self, metrics: dict[str, dict[str, Any]], countries: dict[str, list[dict]]):
        self.metrics = metrics
        self.countries = countries
        self.calls: list[str] = []
        self.by_country_hosts: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("batch-analysis"):
            payload = json.loads(request.content)
            stage = "screen" if len(payload["select"]) == 2 else "full"
            self.calls.append(stage)
            hosts = [t["url"] for t in payload["targets"]]
            rows = [{"url": f"{h}/", **self.metrics[h]} for h in hosts if h in self.metrics]
            return httpx.Response(200, json={"domains": rows}, headers=COST)

        host = dict(request.url.params)["target"]
        self.calls.append("country")
        self.by_country_hosts.append(host)
        if host not in self.countries:
            return httpx.Response(500)
        return httpx.Response(200, json={"metrics": self.countries[host]}, headers=COST)


def _client(fake: Fake) -> AhrefsClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(fake), base_url="https://api.test")
    return AhrefsClient(api_key="k", http=http)


GOOD = {"domain_rating": 40, "org_traffic": 9000, "refdomains": 500, "org_keywords": 2000}
US_ONLY = [{"country": "us", "org_traffic": 8000}]


async def _run(fake: Fake, hosts: list[str], country: str = "us") -> list:
    out = []
    async for batch in collect(hosts, _client(fake), T, country, date="2026-09-01"):
        out.extend(batch)
    return out


class TestStageOrder:
    async def test_low_dr_never_reaches_the_expensive_calls(self) -> None:
        """Смысл первой ступени: домен с DR 5 отсекается за 2 юнита и не
        доходит ни до метрик за 18, ни до стран за 55."""
        fake = Fake({"weak.com": {"domain_rating": 5}}, {})
        results = await _run(fake, ["weak.com"])

        assert fake.calls == ["screen"]
        assert results[0].status is DonorStatus.UNSUITABLE
        assert results[0].reason == "DR 5 ниже 20"

    async def test_failing_second_stage_skips_countries(self) -> None:
        fake = Fake({"thin.com": {**GOOD, "org_traffic": 100}}, {})
        results = await _run(fake, ["thin.com"])

        assert fake.calls == ["screen", "full"]
        assert results[0].reason == "органический трафик 100 ниже 500"

    async def test_only_survivors_are_asked_for_countries(self) -> None:
        """Третья ступень самая дорогая, и она должна видеть только дошедших."""
        fake = Fake(
            {
                "good.com": GOOD,
                "weak.com": {"domain_rating": 5},
                "thin.com": {**GOOD, "org_traffic": 10},
            },
            {"good.com": US_ONLY},
        )
        await _run(fake, ["good.com", "weak.com", "thin.com"])

        assert fake.by_country_hosts == ["good.com"]


def _with_top(row: dict[str, Any], country: str, traffic: int) -> dict[str, Any]:
    """Метрики с верхней страной — как их отдаёт пакетный анализ."""
    return {**row, "org_traffic_top_by_country": [[country, traffic]]}


class TestGeoWithoutPaying:
    """Верхняя страна приезжает пакетом за 10 юнитов на домен, отдельный
    запрос по странам стоит 55 и идёт по одному домену — это 70% расхода
    прогона. Проверяется главное: **вердикт от этого не меняется**.
    """

    async def test_target_on_top_needs_no_country_call(self) -> None:
        """Верхняя страна и есть целевая → она на первом месте, то есть
        в топ-N при любом N. Дорогой запрос вернул бы её же первой строкой."""
        fake = Fake({"good.com": _with_top(GOOD, "us", 8000)}, {"good.com": US_ONLY})
        results = await _run(fake, ["good.com"])

        assert fake.by_country_hosts == [], "заплатили за то, что уже знали"
        assert results[0].status is DonorStatus.SUITABLE
        assert results[0].geo is not None
        assert results[0].geo.partial, "разбивка неполная, и это должно быть видно"

    async def test_another_country_on_top_still_pays(self) -> None:
        """Верхняя страна чужая — про целевую мы не знаем НИЧЕГО: она может
        быть второй, а может не быть в ответе вовсе. Догадка здесь
        отбраковывала бы годных доноров молча."""
        rows = [{"country": "de", "org_traffic": 5000}, {"country": "us", "org_traffic": 3000}]
        fake = Fake({"good.com": _with_top(GOOD, "de", 5000)}, {"good.com": rows})
        results = await _run(fake, ["good.com"])

        assert fake.by_country_hosts == ["good.com"], "сэкономили там, где знать не могли"
        assert results[0].status is DonorStatus.SUITABLE
        assert results[0].geo is not None
        assert not results[0].geo.partial

    @pytest.mark.parametrize(
        ("top_country", "rows"),
        [
            ("us", [{"country": "us", "org_traffic": 8000}]),
            (
                "us",
                [{"country": "us", "org_traffic": 4000}, {"country": "de", "org_traffic": 3000}],
            ),
            (
                "de",
                [{"country": "de", "org_traffic": 5000}, {"country": "us", "org_traffic": 3000}],
            ),
            ("de", [{"country": "de", "org_traffic": 8000}]),
        ],
    )
    async def test_verdict_is_the_same_with_and_without_the_cheap_column(
        self, top_country: str, rows: list[dict[str, Any]]
    ) -> None:
        """Ради этого всё и затевалось: экономия не должна менять вердикты.

        Один и тот же домен проходит оба пути — с дешёвой колонкой и без
        неё, — и статус с причиной обязаны совпасть.
        """
        with_column = Fake(
            {"x.com": _with_top(GOOD, top_country, rows[0]["org_traffic"])}, {"x.com": rows}
        )
        without = Fake({"x.com": GOOD}, {"x.com": rows})

        cheap = (await _run(with_column, ["x.com"]))[0]
        full = (await _run(without, ["x.com"]))[0]

        assert cheap.status is full.status
        assert cheap.reason == full.reason

    async def test_broken_column_falls_back_to_paying(self) -> None:
        """Чужой формат однажды приедет другим. Молча счесть его за «страны
        нет» значит отбраковать домен, за метрики которого уже заплатили."""
        fake = Fake(
            {"good.com": {**GOOD, "org_traffic_top_by_country": "не список"}},
            {"good.com": US_ONLY},
        )
        results = await _run(fake, ["good.com"])

        assert fake.by_country_hosts == ["good.com"]
        assert results[0].status is DonorStatus.SUITABLE


class TestVerdicts:
    async def test_target_country_passes(self) -> None:
        fake = Fake({"good.com": GOOD}, {"good.com": US_ONLY})
        results = await _run(fake, ["good.com"])

        assert results[0].status is DonorStatus.SUITABLE
        assert results[0].breakdown[0].country == "us"

    async def test_wrong_country_is_rejected(self) -> None:
        fake = Fake({"good.com": GOOD}, {"good.com": [{"country": "ru", "org_traffic": 8000}]})
        results = await _run(fake, ["good.com"], country="us")

        assert results[0].status is DonorStatus.UNSUITABLE

    async def test_unknown_domain_is_unchecked_not_rejected(self) -> None:
        """Домена нет в ответе Ahrefs — это , повод вернуться позже."""
        results = await _run(Fake({}, {}), ["ghost.com"])

        assert results[0].status is DonorStatus.UNCHECKED
        assert results[0].reason == "Ahrefs не знает домен"


class TestPartialFailure:
    async def test_country_failure_keeps_paid_metrics(self) -> None:
        """Метрики за 18 юнитов уже оплачены. Потеряв их из-за сбоя на третьей
        ступени, мы заплатим за них второй раз при повторном прогоне."""
        fake = Fake({"good.com": GOOD}, {})  # страны отвечают 500
        results = await _run(fake, ["good.com"])

        assert results[0].status is DonorStatus.UNCHECKED
        assert results[0].reason == "страны не получены"
        assert results[0].metrics.org_traffic == 9000
        assert results[0].metrics.dr == 40


class TestCheckpoints:
    async def test_results_arrive_in_batches(self) -> None:
        """Пачка — чекпоинт. Прогон на тысячу доменов идёт часами, и падение
        на середине не должно стоить всей работы."""
        hosts = [f"d{i}.com" for i in range(5)]
        fake = Fake({h: {"domain_rating": 5} for h in hosts}, {})

        batches = [
            batch
            async for batch in collect(
                hosts, _client(fake), T, "us", date="2026-09-01", batch_size=2
            )
        ]

        assert [len(b) for b in batches] == [2, 2, 1]


class TestDomainRatingParsing:
    @pytest.mark.parametrize(
        ("row", "expected"),
        [
            ({"domain_rating": 42}, 42.0),
            ({"domain_rating": 42.5}, 42.5),
            # Вторая форма ответа Ahrefs — объект с вложенным полем.
            ({"domain_rating": {"domain_rating": 42, "ahrefs_rank": 100}}, 42.0),
            ({"domain_rating": None}, None),
            ({}, None),
            ({"domain_rating": "42"}, None),
            ({"domain_rating": {"ahrefs_rank": 100}}, None),
        ],
    )
    def test_both_shapes_are_understood(self, row: dict[str, Any], expected: float | None) -> None:
        """Наивное чтение однажды вернёт словарь, сравнение с порогом упадёт —
        и упадёт после того, как юниты за пачку уже списаны."""
        assert parse_domain_rating(row) == expected
