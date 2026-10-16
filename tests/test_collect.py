"""Трёхступенчатый сбор: порядок оплаты, частичный отказ, чекпоинты."""

from __future__ import annotations

import json
import logging
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

    def __init__(
        self,
        metrics: dict[str, dict[str, Any]],
        countries: dict[str, list[dict]],
        *,
        refusal: int = 500,
    ):
        self.metrics = metrics
        self.countries = countries
        #: Код ответа по странам для домена, которого нет в `countries`.
        self.refusal = refusal
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
            return httpx.Response(self.refusal)
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


#: Топ стран домена, у которого верхняя — целевая: ответ запроса по странам.
US_FIRST = [
    {"country": "us", "org_traffic": 5000},
    {"country": "gb", "org_traffic": 1200},
    {"country": "ca", "org_traffic": 800},
]


class TestCountriesAlways:
    """Топ-5 стран — каждому, кто прошёл пороги (решение владельца 16.10.2026).

    До 16.10 верхняя страна пакета, совпавшая с целевой, избавляла от запроса по
    странам: вердикт он не менял, а у донора оставалась одна страна из пяти. ТЗ
    называет «org_traffic по топ-5 странам» обязательным полем донора.
    """

    async def test_target_on_top_is_asked_for_all_five(self) -> None:
        """Верхняя страна и есть целевая — запрос всё равно идёт: страны нужны
        донору, а не только вердикту. Разбивка полная, без пометки «неполная»."""
        fake = Fake({"good.com": _with_top(GOOD, "us", 5000)}, {"good.com": US_FIRST})
        (result,) = await _run(fake, ["good.com"])

        assert fake.by_country_hosts == ["good.com"]
        assert result.status is DonorStatus.SUITABLE
        assert result.geo is not None
        assert not result.geo.partial
        assert [item.country for item in result.breakdown] == ["us", "gb", "ca"]

    async def test_another_country_on_top_is_asked_too(self) -> None:
        """Верхняя страна чужая — про целевую мы не знаем НИЧЕГО: она может
        быть второй, а может не быть в ответе вовсе."""
        rows = [{"country": "de", "org_traffic": 5000}, {"country": "us", "org_traffic": 3000}]
        fake = Fake({"good.com": _with_top(GOOD, "de", 5000)}, {"good.com": rows})
        (result,) = await _run(fake, ["good.com"])

        assert fake.by_country_hosts == ["good.com"]
        assert result.status is DonorStatus.SUITABLE
        assert result.reason == "US на 2-м месте по трафику (33%)"

    async def test_every_survivor_is_asked_once(self) -> None:
        """Каждый дошедший — ровно одним запросом, с колонкой верхней страны и без."""
        fake = Fake(
            {
                "a.com": _with_top(GOOD, "us", 5000),
                "b.com": _with_top(GOOD, "de", 5000),
                "c.com": GOOD,
            },
            {"a.com": US_FIRST, "b.com": US_FIRST, "c.com": US_FIRST},
        )
        await _run(fake, ["a.com", "b.com", "c.com"])

        assert sorted(fake.by_country_hosts) == ["a.com", "b.com", "c.com"]

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
    async def test_the_top_column_changes_nothing_when_countries_come(
        self, top_country: str, rows: list[dict[str, Any]]
    ) -> None:
        """Колонка верхней страны — запасной путь, а не второй судья: пришли страны —
        с ней и без неё те же запросы, тот же вердикт и та же разбивка."""
        with_column = Fake(
            {"x.com": _with_top(GOOD, top_country, rows[0]["org_traffic"])}, {"x.com": rows}
        )
        without = Fake({"x.com": GOOD}, {"x.com": rows})

        topped = (await _run(with_column, ["x.com"]))[0]
        plain = (await _run(without, ["x.com"]))[0]

        assert with_column.calls == without.calls
        assert (topped.status, topped.reason) == (plain.status, plain.reason)
        assert topped.breakdown == plain.breakdown


class TestCountriesFailed:
    """Запрос по странам не удался — правило 16.10.2026: годного донора сбой не отнимает.

    Отказ — постоянный (402): клиент не повторяет его, и тест не ждёт пауз."""

    async def test_target_on_top_keeps_a_suitable_donor(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Верхняя страна пакета и есть целевая — первое место в топ-N при любом N:
        «подходит», разбивка — одна верхняя страна с пометкой «неполная»,
        и строка в журнал."""
        fake = Fake({"good.com": _with_top(GOOD, "us", 5000)}, {}, refusal=402)

        with caplog.at_level(logging.WARNING, logger="backend.features.donors.collect"):
            (result,) = await _run(fake, ["good.com"])

        assert fake.by_country_hosts == ["good.com"]
        assert result.status is DonorStatus.SUITABLE
        assert result.reason == "US на 1-м месте по трафику (56%)"
        assert result.geo is not None
        assert result.geo.partial
        assert [item.country for item in result.breakdown] == ["us"]
        assert "Страны для good.com не получены" in caplog.text
        assert "годен по верхней стране пакета" in caplog.text

    @pytest.mark.parametrize(
        "row",
        [
            _with_top(GOOD, "de", 5000),
            {**GOOD, "org_traffic_top_by_country": "не список"},
            GOOD,
        ],
        ids=["чужая верхняя", "колонка не разобрана", "колонки нет"],
    )
    async def test_otherwise_unchecked_with_paid_metrics(self, row: dict[str, Any]) -> None:
        """Верхняя чужая, непонятная или её нет — про целевую страну не знаем
        ничего: «не проверен», метрики сохранены, страны доберёт следующий прогон."""
        fake = Fake({"good.com": row}, {}, refusal=402)

        (result,) = await _run(fake, ["good.com"])

        assert (result.status, result.reason) == (DonorStatus.UNCHECKED, "страны не получены")
        assert result.geo is None
        assert result.metrics.org_traffic == 9000


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
    @pytest.mark.parametrize("refusal", [500, 402, 410, 301, 501])
    async def test_country_failure_keeps_paid_metrics(self, refusal: int) -> None:
        """Метрики за 18 юнитов уже оплачены. Потеряв их из-за сбоя на третьей
        ступени, мы заплатим за них второй раз при повторном прогоне.

        Сбой — любой отказ: 500 после повторов и коды вне обоих списков клиента
        (402, 410, переадресация, 501). До 08.10.2026 вторые уходили голым
        `HTTPStatusError` мимо `except AhrefsError` и роняли сбор целиком."""
        fake = Fake({"good.com": GOOD}, {}, refusal=refusal)  # страны отказывают
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
