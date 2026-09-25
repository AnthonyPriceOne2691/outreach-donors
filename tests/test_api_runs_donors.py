"""Прогон и доноры по HTTP: права, смета и таблица.

Права проверяются таблицей, как и в остальных разделах. Отдельно
проверяется главное свойство экрана прогона: смета ничего не тратит,
а запуск кладёт задачу в очередь, а не выполняет её в запросе.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from backend.features.core import usage
from backend.features.core.domain import ContactSource, DonorStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from backend.features.core.models.run import RunModel, RunSettingsModel
from backend.features.runs.repository import RunRepository
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

NOW = datetime.now(UTC)

RUN_BODY = {"keywords": ["ремонт квартир"], "country": "us", "depth_pages": 1}

ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/runs/countries", None, "run"),
    ("POST", "/api/runs/estimate", RUN_BODY, "run"),
    ("POST", "/api/runs", RUN_BODY, "run"),
    ("GET", "/api/runs", None, "view"),
    ("GET", "/api/runs/with-accepted", None, "view"),
    ("GET", "/api/runs/{run}", None, "view"),
    ("GET", "/api/donors", None, "view"),
    ("GET", "/api/donors/{donor}", None, "view"),
    ("GET", "/api/donors/export", None, "view"),
]


@pytest.fixture
async def donors(session: AsyncSession) -> list[DonorModel]:
    made: list[DonorModel] = []
    for index, (host, status, dr, reason) in enumerate(
        [
            ("good.example.test", DonorStatus.SUITABLE, 45, None),
            ("weak.example.test", DonorStatus.UNSUITABLE, 8, "dr ниже порога"),
            ("unknown.example.test", DonorStatus.UNCHECKED, None, None),
        ]
    ):
        domain = DomainModel(host=host)
        session.add(domain)
        await session.flush()
        donor = DonorModel(
            domain_id=domain.id,
            status=status,
            dr=dr,
            reject_reason=reason,
            org_traffic=1000 * (index + 1),
            metrics_refreshed_at=NOW - timedelta(days=index),
        )
        session.add(donor)
        if index == 0:
            session.add(
                ContactModel(
                    domain_id=domain.id,
                    email=f"editor@{host}",
                    source=ContactSource.PAGE,
                )
            )
        made.append(donor)
    await session.commit()
    return made


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def viewer_token(make_user: MakeUser, sign_in: SignIn) -> str:
    """Оператор без права на прогон: смотреть базу может, тратить — нет."""
    await make_user("зритель@site.com", role=UserRole.OPERATOR, permissions={"run": False})
    return await sign_in("зритель@site.com")


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        donors: list[DonorModel],
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        target = path.format(run=1, donor=donors[0].id)
        response = await client.request(method, target, json=body)
        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_run_needs_its_own_right(
        self,
        client: AsyncClient,
        viewer_token: str,
        donors: list[DonorModel],
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        """Смотреть базу и тратить юниты — разные права. Отобранное
        точечно право на прогон закрывает смету и запуск, но не таблицу."""
        target = path.format(run=1, donor=donors[0].id)
        response = await client.request(method, target, json=body, headers=bearer(viewer_token))

        if permission == "run":
            assert response.status_code == 403
            assert "«run»" in response.json()["detail"]
        else:
            assert response.status_code in (200, 404), response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith(("/api/runs", "/api/donors"))
            for method in methods
        }
        in_table = {
            (method, path.replace("{run}", "{run_id}").replace("{donor}", "{donor_id}"))
            for method, path, _, _ in ROUTES
        }
        assert in_app == in_table


class TestDonorsTable:
    async def test_counts_separate_unchecked_from_unsuitable(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        """«Не проверен» — не «не подходит»: спутать их значит копить
        ложные отказы и терять доноров."""
        response = await client.get("/api/donors", headers=bearer(operator_token))

        counts = response.json()["counts"]
        assert counts["suitable"] == 1
        assert counts["unsuitable"] == 1
        assert counts["unchecked"] == 1

    async def test_reject_reason_is_always_returned(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        response = await client.get("/api/donors?status=unsuitable", headers=bearer(operator_token))

        row = response.json()["rows"][0]
        assert row["reject_reason"] == "dr ниже порога"

    async def test_search_looks_at_host_and_reason(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        by_reason = await client.get("/api/donors?search=порога", headers=bearer(operator_token))

        assert [row["host"] for row in by_reason.json()["rows"]] == ["weak.example.test"]

    async def test_total_is_counted_before_the_page(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        """Без общего числа «ничего не найдено» читается как «база пуста»."""
        response = await client.get("/api/donors?limit=1", headers=bearer(operator_token))

        body = response.json()
        assert len(body["rows"]) == 1
        assert body["total"] == 3

    async def test_filter_by_contact(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        response = await client.get("/api/donors?has_contact=true", headers=bearer(operator_token))
        assert response.json()["total"] == 0  # адрес найден — это отметка на доноре

    async def test_card_shows_contacts_and_expiry(
        self, client: AsyncClient, operator_token: str, donors: list[DonorModel]
    ) -> None:
        response = await client.get(f"/api/donors/{donors[0].id}", headers=bearer(operator_token))

        card = response.json()
        assert card["contacts"][0]["email"].startswith("editor@")
        assert card["expires_at"] is not None
        assert card["fresh"] is True

    async def test_unknown_donor_is_not_found(
        self, client: AsyncClient, operator_token: str
    ) -> None:
        response = await client.get("/api/donors/9999", headers=bearer(operator_token))
        assert response.status_code == 404


class FakeJob:
    def __init__(self, job_id: str) -> None:
        self.id = job_id


class FakeQueue:
    """Очередь, которая ничего не выполняет: проверяется, что в неё
    положили, а не что rq работает."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def enqueue(self, path: str, *args: Any, **_: Any) -> FakeJob:
        self.calls.append((path, args))
        return FakeJob("job-из-теста")


async def _cap_of_run(session: AsyncSession, run_id: int) -> int:
    """Потолок, с которым прогон уедет в задачу: он лежит в настройках,
    и берёт его оттуда сам воркер."""
    run = await RunRepository(session).get(run_id)
    settings = await session.get(RunSettingsModel, run.settings_id)
    assert settings is not None
    return settings.units_cap


class TestTheCeilings:
    """Потолки, которые до этого среза были объявлены и не применялись."""

    @pytest.fixture
    def queue(self, monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
        monkeypatch.setattr("backend.config.serp.SANDBOX", False)
        fake = FakeQueue()
        monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: fake)
        return fake

    async def test_more_keywords_than_allowed_is_refused(
        self, client: AsyncClient, operator_token: str
    ) -> None:
        """Настройка «до ста ключей за прогон» существовала и не
        проверялась нигде: маршрут принимал впятеро больше."""
        response = await client.post(
            "/api/runs",
            json={"keywords": [f"ключ {n}" for n in range(101)], "country": "us"},
            headers=bearer(operator_token),
        )

        assert response.status_code == 422
        assert "не больше 100" in response.text

    async def test_own_ceiling_reaches_the_run(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        session: AsyncSession,
    ) -> None:
        """Поле «потолок юнитов» — способ попробовать нишу дёшево,
        не сокращая список ключей."""
        started = await client.post(
            "/api/runs",
            json={**RUN_BODY, "cap": 5_000},
            headers=bearer(operator_token),
        )

        assert started.status_code == 200, started.text
        cap = await _cap_of_run(session, started.json()["run_id"])
        assert cap == 5_000

    async def test_spent_units_lower_the_ceiling(
        self,
        client: AsyncClient,
        operator_token: str,
        queue: FakeQueue,
        session: AsyncSession,
    ) -> None:
        """Кап месячный: потраченное вычитается, иначе он ограничивает
        один прогон, а на экране расхода называется месячным."""
        usage.record(session, operation="batch_metrics", units=99_000)
        await session.commit()

        started = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

        cap = await _cap_of_run(session, started.json()["run_id"])
        assert cap == 1_000


class TestStartPutsTheRunOnTheScreen:
    """Прогон существует с нажатия, а не с первой траты.

    До этого строка появлялась внутри задачи, уже после выдачи: всё это
    время экран показывал пустоту, а если задачу никто не брал — всегда.
    """

    @pytest.fixture
    def queue(self, monkeypatch: pytest.MonkeyPatch) -> FakeQueue:
        # Настройки задаются тестом, а не берутся из окружения машины.
        # Первая версия проходила локально и падала в CI: ключ Ahrefs
        # лежал в `.env` разработчика, и проверка настроек на маршруте
        # пропускала запуск по чужой причине.
        monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
        monkeypatch.setattr("backend.config.serp.SANDBOX", False)
        fake = FakeQueue()
        monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: fake)
        return fake

    async def test_row_appears_queued_with_its_job(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue
    ) -> None:
        started = await client.post("/api/runs", json=RUN_BODY, headers=bearer(operator_token))

        assert started.status_code == 200, started.text
        run_id = started.json()["run_id"]
        assert queue.calls == [("backend.workers.jobs.run_donor_search", (run_id,))]

        listed = await client.get("/api/runs", headers=bearer(operator_token))
        card = next(row for row in listed.json()["runs"] if row["id"] == run_id)
        assert card["status"] == "queued"
        assert card["estimated_units"] is None
        assert card["hosts"] is None

    async def test_job_gets_only_the_run_number(
        self, client: AsyncClient, operator_token: str, queue: FakeQueue, session: AsyncSession
    ) -> None:
        """Доводы задачи — один номер. Ключи и глубина лежат в строке:
        продолжению после смерти воркера неоткуда взять другие."""
        await client.post(
            "/api/runs",
            json={"keywords": ["ремонт", "кухня"], "country": "de", "depth_pages": 3},
            headers=bearer(operator_token),
        )

        _, args = queue.calls[0]
        assert len(args) == 1

        run = (
            (await session.execute(select(RunModel).order_by(RunModel.id.desc()))).scalars().first()
        )
        assert run is not None
        assert run.depth_pages == 3
        assert run.country == "de"
        assert run.job_id == "job-из-теста"

    async def test_screen_knows_there_is_nobody_to_take_the_job(
        self, client: AsyncClient, operator_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Число живых воркеров — единственное, что отличает работающий
        сервис от очереди, из которой никто не читает."""
        monkeypatch.setattr("backend.api.runs.routes.workers_alive", lambda: 0)

        listed = await client.get("/api/runs", headers=bearer(operator_token))

        assert listed.json()["workers"] == 0

    async def test_unknown_worker_count_is_not_zero(
        self, client: AsyncClient, operator_token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Redis не ответил — это «не знаю», а не «никого нет»."""
        monkeypatch.setattr("backend.api.runs.routes.workers_alive", lambda: None)

        listed = await client.get("/api/runs", headers=bearer(operator_token))

        assert listed.json()["workers"] is None
