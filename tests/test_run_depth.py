"""Глубина выдачи: от 10 до 100 результатов на ключ, и смета честна на любой.

Замечание 25.09.2026: поле «Глубина, страниц» (от одной до пяти) стало
выпадающим списком «Глубина выдачи» — 10, 20, 30, 50 и 100 результатов.
На границе глубина по-прежнему передаётся страницами по десять: провайдер
берёт деньги за каждые десять, и смета считает в них же. Здесь проверяется,
что сервер принимает сто результатов, отказывает за пределом словами,
а не умолчанием разбора, и что смета растёт вместе с глубиной.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from backend.config import serp as serp_cfg
from backend.features.core.domain import RunStatus, Stage, UserRole
from backend.features.core.models.access import UserModel
from backend.features.runs.repository import RunRepository
from backend.features.runs.thresholds import defaults
from backend.features.serp.dataforseo import DataForSeoProvider
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

KEYWORDS = ["ремонт квартир", "дизайн интерьера"]


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


class _Queue:
    """Очередь, которая только запоминает, что в неё положили."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def enqueue(self, *args: Any, **_: Any) -> None:
        self.calls.append(args)


class _NoAhrefs:
    """Клиент Ahrefs, который никуда не ходит: остаток подменён ниже."""

    async def aclose(self) -> None:
        return None


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Смета без сети: остаток у провайдера — большой и известный."""

    async def plenty(_: Any) -> int:
        return 10_000_000

    monkeypatch.setattr("backend.api.runs.routes.AhrefsClient", _NoAhrefs)
    monkeypatch.setattr("backend.api.runs.routes.units_left", plenty)


async def _estimate(client: AsyncClient, token: str, depth: int) -> httpx.Response:
    return await client.post(
        "/api/runs/estimate",
        json={"keywords": KEYWORDS, "country": "us", "depth_pages": depth},
        headers=bearer(token),
    )


class TestDepthOnTheBoundary:
    async def test_a_hundred_results_is_accepted(
        self, client: AsyncClient, token: str, offline: None
    ) -> None:
        response = await _estimate(client, token, 10)

        assert response.status_code == 200, response.text
        forecast = response.json()
        assert forecast["depth_pages"] == 10
        assert forecast["expected_results"] == len(KEYWORDS) * 100

    async def test_forecast_grows_with_depth(
        self, client: AsyncClient, token: str, offline: None
    ) -> None:
        """Сто результатов — вдесятеро больше выдачи и доменов, чем десять:
        и деньги за выдачу, и юниты Ahrefs растут, а не остаются как на топ-10."""
        shallow = (await _estimate(client, token, 1)).json()
        deep = (await _estimate(client, token, 10)).json()

        assert deep["serp_cost_usd"] == pytest.approx(10 * shallow["serp_cost_usd"])
        assert deep["serp_cost_usd"] == pytest.approx(
            len(KEYWORDS) * 10 * serp_cfg.PRICE_PER_KEYWORD_USD
        )
        assert deep["expected_domains"] > shallow["expected_domains"]
        assert deep["units_total"] > shallow["units_total"]

    @pytest.mark.parametrize("depth", [0, 11, 50, 100])
    async def test_beyond_the_ceiling_is_refused_in_words(
        self, client: AsyncClient, token: str, depth: int
    ) -> None:
        """50 и 100 — самая вероятная ошибка: прислали число результатов
        вместо страниц. Отказ называет обе единицы и говорит по-русски."""
        response = await _estimate(client, token, depth)

        assert response.status_code == 422
        said = response.json()["detail"][0]["msg"]
        assert said.startswith("Глубина выдачи — от 10 до 100 результатов на ключ")
        assert f"пришло {depth}" in said
        assert "страницами" in said

    async def test_launch_is_refused_the_same_way(
        self, client: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Запуск отказывает до очереди: задача с глубиной, которой экран
        не предлагает, не должна стоить ни цента."""
        queue = _Queue()
        monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: queue)

        response = await client.post(
            "/api/runs",
            json={"keywords": KEYWORDS, "country": "us", "depth_pages": 20},
            headers=bearer(token),
        )

        assert response.status_code == 422
        assert queue.calls == []

    async def test_keyword_ceiling_speaks_without_an_english_prefix(
        self, client: AsyncClient, token: str
    ) -> None:
        """Умолчание разбора приклеивало «Value error, » к нашему тексту,
        и на экран уходило полуанглийское сообщение."""
        response = await client.post(
            "/api/runs/estimate",
            json={"keywords": [f"ключ {n}" for n in range(101)], "country": "us"},
            headers=bearer(token),
        )

        assert response.status_code == 422
        assert response.json()["detail"][0]["msg"].startswith("За прогон берём не больше 100")


class TestProviderAtAHundred:
    async def test_dataforseo_is_asked_for_a_hundred_and_gives_a_hundred(self) -> None:
        """Свой потолок у провайдера — 700 результатов, а платит он за каждые
        десять: глубина 100 уходит ему как есть, а не обрезается."""
        posted: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("task_post"):
                posted.extend(row["depth"] for row in json.loads(request.content))
                task = {"id": "t-1", "status_code": 20100, "data": {"tag": "к"}}
                return httpx.Response(200, json={"status_code": 20000, "tasks": [task]})
            items = [
                {"type": "organic", "rank_absolute": n, "url": f"https://site{n}.test/"}
                for n in range(1, 131)
            ]
            result = {"status_code": 20000, "result": [{"items": items}]}
            return httpx.Response(200, json={"status_code": 20000, "tasks": [result]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://dfs")
        provider = DataForSeoProvider(client, sandbox=True, login="l", password="p")

        found = await provider.search(["к"], "us", depth_pages=10)

        assert posted == [100]
        assert len(found["к"]) == 100


async def _runs(session: AsyncSession, statuses: list[RunStatus]) -> None:
    repository = RunRepository(session)
    settings = await repository.create_settings(
        defaults(),
        geo_top_n=5,
        geo_min_share=0.2,
        metrics_ttl_days=90,
        price_ttl_days=150,
        units_cap=100_000,
    )
    for status in statuses:
        await repository.create_run(
            stage=Stage.DONORS,
            settings_id=settings.id,
            keywords=["ключ"],
            country="us",
            status=status,
        )
    await session.commit()


class TestNobodyToTakeTheJob:
    async def test_queue_is_counted_over_the_whole_history(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        """Прогон в очереди — на первой странице, а смотрят вторую. До
        25.09.2026 экран искал его только среди строк своей страницы и
        молчал про «задачу некому взять»."""
        await _runs(session, [RunStatus.DONE] * 11 + [RunStatus.QUEUED])

        second = (await client.get("/api/runs?page=2", headers=bearer(token))).json()

        assert all(run["status"] != "queued" for run in second["runs"])
        assert second["queued"] == 1


class TestNumbersBeyondTheColumn:
    """Номер из адреса больше, чем помещается в столбец, — «не найдено».

    До 25.09.2026 база отказывала переполнением, и маршрут отвечал
    пятисоткой: `/api/donors/99999999999` — «Internal Server Error».
    """

    HUGE = 99_999_999_999

    @pytest.mark.parametrize(
        "path",
        [
            "/api/donors/{n}",
            "/api/runs/{n}",
            "/api/review/runs/{n}?status=pending",
        ],
    )
    async def test_huge_number_is_not_found(
        self, client: AsyncClient, token: str, path: str
    ) -> None:
        response = await client.get(path.format(n=self.HUGE), headers=bearer(token))

        assert response.status_code == 404, response.text
        assert f"№{self.HUGE} нет" in response.json()["detail"]

    async def test_huge_candidate_is_foreign_not_a_crash(
        self, client: AsyncClient, token: str, session: AsyncSession
    ) -> None:
        await _runs(session, [RunStatus.DONE])
        run_id = (await client.get("/api/runs", headers=bearer(token))).json()["runs"][0]["id"]

        response = await client.post(
            f"/api/review/runs/{run_id}/decide",
            json={"candidate_ids": [self.HUGE], "decision": "rejected"},
            headers=bearer(token),
        )

        assert response.status_code == 409, response.text
        assert str(self.HUGE) in response.json()["detail"]

    async def test_huge_domain_on_selection_is_not_found(
        self, client: AsyncClient, token: str
    ) -> None:
        response = await client.post(
            f"/api/selection/{self.HUGE}/decide",
            json={"intent": "publisher"},
            headers=bearer(token),
        )

        assert response.status_code == 404, response.text

    async def test_accuracy_of_a_huge_run_is_empty(self, client: AsyncClient, token: str) -> None:
        response = await client.get(
            f"/api/review/accuracy?run_id={self.HUGE}", headers=bearer(token)
        )

        assert response.status_code == 200, response.text
        assert response.json()["decided"] == 0
