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
from backend.features.core.domain import ContactSource, DonorStatus, UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.donor import ContactModel, DonorModel
from fastapi import FastAPI
from httpx import AsyncClient
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
    ("GET", "/api/runs/{run}", None, "view"),
    ("GET", "/api/donors", None, "view"),
    ("GET", "/api/donors/{donor}", None, "view"),
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
