"""Прогон по ссылкам конкурентов по HTTP: смета, запуск, отказы словами и строка истории.

Сети нет: остаток Ahrefs подменён, очередь — заглушка, в которой видно, ушёл ли прогон
в работу. Бюджет запуска сверяется со снимком ключа, как у прогона по ключам.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.config.judge import JudgeMode
from backend.config.topics import TopicMode
from backend.features.ahrefs.units import estimate_run
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.run import RunModel
from backend.features.runs.budget import LIMIT_WORDS
from backend.features.runs.competitor_estimate import TRIM
from backend.features.topics.cost import ESTIMATE as TOPIC_ESTIMATE
from backend.shared.queue import RUN_JOB
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_ahrefs_cap import known_key
from tests.test_api_runs_donors import RUN_BODY, FakeQueue

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

BODY = {"competitors": ["https://www.example.com/", "example.net"], "country": "us", "depth": 50}


class _NoAhrefs:
    async def aclose(self) -> None:
        return None


@pytest.fixture
async def runner(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("конкуренты@site.com", role=UserRole.OPERATOR)
    return await sign_in("конкуренты@site.com")


@pytest.fixture
def offline(monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
    """Остаток у Ahrefs большой и известный, судья выключен, очередь — заглушка."""

    async def plenty(_: Any, **__: Any) -> int:
        return 10_000_000

    monkeypatch.setattr("backend.api.runs.competitors.AhrefsClient", _NoAhrefs)
    monkeypatch.setattr("backend.api.runs.competitors.units_left", plenty)
    monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.OFF)
    monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
    monkeypatch.setattr("backend.config.serp.SANDBOX", False)
    fake = FakeQueue()
    monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: fake)
    return fake


class TestEstimate:
    async def test_cost_is_named_plainly_before_the_launch(
        self, client: AsyncClient, session: AsyncSession, runner: str, offline: FakeQueue
    ) -> None:
        await known_key(session, key_limit=100_000)

        response = await client.post(
            "/api/runs/competitors/estimate", json=BODY, headers=bearer(runner)
        )

        assert response.status_code == 200, response.text
        made = response.json()
        funnel = estimate_run(100, country_share=1.0)
        assert [row["target"] for row in made["lists"]] == ["example.com", "example.net"]
        assert made["row_units"] == 14
        assert made["units_lists"] == 2 * 50 * 14
        assert made["expected_domains"] == 100
        assert made["units_by_country"] == funnel.by_country
        assert made["units_total"] == 1400 + funnel.total
        assert made["tokens"] == 0, "судья выключен — модель не тратится"
        assert made["affordable"] is True
        assert made["binding"] == "cap"
        assert offline.calls == [], "смета ничего не ставит в очередь"

    async def test_short_key_is_named_like_on_the_keyword_estimate(
        self,
        client: AsyncClient,
        session: AsyncSession,
        runner: str,
        offline: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Упор — словом экрана, как у сметы ключей: лимит ключа, а не «кап»."""
        await known_key(session, key_limit=100_000)

        async def little(_: Any, **__: Any) -> int:
            return 1_000

        monkeypatch.setattr("backend.api.runs.competitors.units_left", little)

        response = await client.post(
            "/api/runs/competitors/estimate", json=BODY, headers=bearer(runner)
        )

        assert response.status_code == 200, response.text
        assert response.json()["affordable"] is False
        assert response.json()["short_of"] == ["key"]

    @pytest.mark.parametrize(
        ("body", "words"),
        [
            ({"competitors": ["не домен"]}, "«не домен» — в нём пробел или невидимый знак"),
            ({"competitors": ["a_b.com"]}, "«a_b.com» — не домен по правилам имён"),
            ({"competitors": ["-bad-.com"]}, "«-bad-.com» — не домен по правилам имён"),
            ({"competitors": ["%65xample.com"]}, "ведёт не туда, куда виден"),
            ({"competitors": ["ex\u0430mple.com"]}, "смешаны письменности"),
            ({"competitors": ["facebook.com/somepage"]}, "«facebook.com» — платформа или соцсеть"),
            ({"competitors": ["youtube.com/@chan"]}, "«youtube.com» — платформа или соцсеть"),
            ({"competitors": ["t.me/somechan"]}, "«t.me» — платформа или соцсеть"),
            ({"competitors": ["someone.wordpress.com"]}, "«wordpress.com» — платформа"),
            ({"country": "usa"}, "Страны «usa» нет в списке стран сервиса"),
            ({"country": "zz"}, "Страны «zz» нет в списке стран сервиса"),
            ({"country": "uk"}, "Страны «uk» нет в списке стран сервиса"),
            ({"country": "xx-yy"}, "Страны «xx-yy» нет в списке стран сервиса"),
            ({"competitors": ["  "]}, "Конкурентов нет"),
            ({"competitors": [f"r{i}.github.io" for i in range(6)]}, "не больше 5, пришло 6"),
            ({"depth": 5}, "Глубина списка — от 10 до 1000"),
            ({"depth": 5000}, "пришло 5000: больше Ahrefs за один запрос не отдаёт"),
        ],
    )
    async def test_refusals_of_the_form_are_in_words(
        self,
        client: AsyncClient,
        runner: str,
        offline: FakeQueue,
        body: dict[str, Any],
        words: str,
    ) -> None:
        response = await client.post(
            "/api/runs/competitors/estimate", json={**BODY, **body}, headers=bearer(runner)
        )

        assert response.status_code == 422, response.text
        assert any(words in item["msg"] for item in response.json()["detail"]), response.text

    async def test_national_name_and_its_ascii_form_are_one_list(
        self, client: AsyncClient, session: AsyncSession, runner: str, offline: FakeQueue
    ) -> None:
        await known_key(session, key_limit=100_000)
        body = {**BODY, "competitors": ["пример.рф", "xn--e1afmkfd.xn--p1ai"], "country": " US "}

        response = await client.post(
            "/api/runs/competitors/estimate", json=body, headers=bearer(runner)
        )

        assert response.status_code == 200, response.text
        assert [row["target"] for row in response.json()["lists"]] == ["xn--e1afmkfd.xn--p1ai"]

    async def test_model_caps_are_named_on_the_estimate_and_refuse_the_start(
        self,
        client: AsyncClient,
        session: AsyncSession,
        runner: str,
        offline: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Суд сайтов списков не помещается в потолок модели на прогон: смета говорит это
        словами, запуск отказывает до строки прогона — списки не покупаются."""
        await known_key(session, key_limit=100_000)
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
        monkeypatch.setattr("backend.config.llm.RUN_TOKEN_CAP", 10_000)

        estimate = await client.post(
            "/api/runs/competitors/estimate", json=BODY, headers=bearer(runner)
        )
        started = await client.post("/api/runs/competitors", json=BODY, headers=bearer(runner))

        assert estimate.status_code == 200, estimate.text
        assert "потолок модели на прогон" in estimate.json()["llm_refusal"]
        assert estimate.json()["topic_tokens"] == 0, "тематика в тестах выключена"
        assert started.status_code == 429, started.text
        assert started.json()["detail"] == estimate.json()["llm_refusal"]
        assert offline.calls == []
        assert await session.scalar(select(func.count(RunModel.id))) == 0

    async def test_topics_of_the_sites_are_named_in_the_estimate(
        self,
        client: AsyncClient,
        session: AsyncSession,
        runner: str,
        offline: FakeQueue,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Тематика — той же пачкой судьи: смета называет её токены, как у ключей."""
        await known_key(session, key_limit=100_000)
        monkeypatch.setattr("backend.config.judge.MODE", JudgeMode.SHADOW)
        monkeypatch.setattr("backend.config.topics.MODE", TopicMode.ON)

        response = await client.post(
            "/api/runs/competitors/estimate", json=BODY, headers=bearer(runner)
        )

        made = response.json()
        assert response.status_code == 200, response.text
        assert made["topic_tokens_per_site"] == TOPIC_ESTIMATE.tokens
        assert made["topic_tokens"] == 100 * TOPIC_ESTIMATE.tokens
        assert made["topic_measured"] is False, "замера ещё не было — оценка"
        assert made["llm_refusal"] is None


class TestStart:
    async def test_run_is_queued_with_its_competitors(
        self, client: AsyncClient, session: AsyncSession, runner: str, offline: FakeQueue
    ) -> None:
        await known_key(session, key_limit=100_000)

        response = await client.post("/api/runs/competitors", json=BODY, headers=bearer(runner))

        assert response.status_code == 200, response.text
        assert "списки сайтов, ссылающихся на конкурентов" in response.json()["note"]
        run = await session.get(RunModel, response.json()["run_id"])
        assert run is not None
        assert run.keywords == []
        assert run.competitors == {"targets": ["example.com", "example.net"], "depth": 50}
        assert offline.calls == [(RUN_JOB, (run.id,))]
        audit = await session.scalar(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.RUN_STARTED)
        )
        assert audit is not None
        assert audit.details["конкуренты"] == ["example.com", "example.net"]
        assert audit.details["глубина"] == 50

    async def test_country_outside_the_list_is_not_queued(
        self, client: AsyncClient, session: AsyncSession, runner: str, offline: FakeQueue
    ) -> None:
        """Страны нет в списке — списки и метрики ушли бы на чужой рынок (проба ревью)."""
        await known_key(session, key_limit=100_000)

        response = await client.post(
            "/api/runs/competitors", json={**BODY, "country": "uk"}, headers=bearer(runner)
        )
        spelled = await client.post(
            "/api/runs/competitors", json={**BODY, "country": "GB"}, headers=bearer(runner)
        )

        assert response.status_code == 422, response.text
        assert spelled.status_code == 200, spelled.text
        run = await session.get(RunModel, spelled.json()["run_id"])
        assert run is not None
        assert run.country == "gb", "страна — в форме списка"
        assert offline.calls == [(RUN_JOB, (run.id,))]

    async def test_over_the_key_is_refused_before_the_run_row(
        self, client: AsyncClient, session: AsyncSession, runner: str, offline: FakeQueue
    ) -> None:
        await known_key(session, key_limit=10_000, key_used=9_900)

        response = await client.post("/api/runs/competitors", json=BODY, headers=bearer(runner))

        assert response.status_code == 409, response.text
        said = response.json()["detail"]
        assert said.startswith(LIMIT_WORDS["key"][:1].upper() + LIMIT_WORDS["key"][1:])
        assert said.endswith(f"{TRIM}.")
        assert offline.calls == []
        assert await session.scalar(select(func.count(RunModel.id))) == 0

    async def test_same_run_while_the_first_is_queued_is_refused(
        self, client: AsyncClient, session: AsyncSession, runner: str, offline: FakeQueue
    ) -> None:
        await known_key(session, key_limit=100_000)
        first = await client.post("/api/runs/competitors", json=BODY, headers=bearer(runner))
        assert first.status_code == 200, first.text

        again = await client.post(
            "/api/runs/competitors",
            json={**BODY, "competitors": ["example.net", "example.com"]},
            headers=bearer(runner),
        )
        deeper = await client.post(
            "/api/runs/competitors", json={**BODY, "depth": 100}, headers=bearer(runner)
        )

        assert again.status_code == 409, again.text
        assert "те же конкуренты, страна и глубина" in again.json()["detail"]
        assert deeper.status_code == 200, "глубже — другая покупка"

    async def test_keyword_run_leaves_the_competitors_field_empty_not_json_null(
        self, client: AsyncClient, session: AsyncSession, runner: str, offline: FakeQueue
    ) -> None:
        """Прогон по ключам идёт тем же путём постановки (`routes.enqueue_run`), и поле
        конкурентов у него — без значения, а не JSON `null`: отбор «только прогоны по выдаче»
        (`IS NULL`, доля дублей сметы ключей) иначе молча терял бы их все."""
        await known_key(session, key_limit=100_000)

        response = await client.post("/api/runs", json=RUN_BODY, headers=bearer(runner))

        assert response.status_code == 200, response.text
        empty = await session.scalar(
            select(func.count(RunModel.id)).where(RunModel.competitors.is_(None))
        )
        assert empty == 1

    async def test_history_names_the_competitors_of_the_run(
        self, client: AsyncClient, session: AsyncSession, runner: str, offline: FakeQueue
    ) -> None:
        await known_key(session, key_limit=100_000)
        await client.post("/api/runs/competitors", json=BODY, headers=bearer(runner))

        page = await client.get("/api/runs", headers=bearer(runner))

        assert page.status_code == 200, page.text
        card = page.json()["runs"][0]
        assert card["competitors"] == ["example.com", "example.net"]
        assert card["keywords"] == 0
