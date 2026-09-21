"""Стоп-лист на экране: кто его видит, кто правит и что попадает в журнал.

Смотреть список должен каждый, кто видит базу, — иначе «почему донору
не ушло письмо» остаётся без ответа. Править — только тот, кому доверена
отправка: список отвечает ровно на вопрос, кому мы пишем.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import (
    AuditAction,
    SuppressionReason,
    UserRole,
)
from backend.features.core.models.access import AuditLogModel, UserModel
from backend.features.core.models.ops import SuppressionModel
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer, make_donor

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

HOST = "donor.example.test"

ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/suppressions", None, "view"),
    ("POST", "/api/suppressions", {"target": HOST, "reason": "manual"}, "send"),
    ("POST", "/api/suppressions/{row}/remove", {"reason": "передумали"}, "send"),
]


@pytest.fixture
async def row(session: AsyncSession) -> SuppressionModel:
    """Запись об отписке — та, которую нельзя снять молча."""
    domain = await make_donor(session, HOST, email=f"editor@{HOST}")
    entry = SuppressionModel(
        domain_id=domain.id,
        reason=SuppressionReason.UNSUBSCRIBED,
        created_by="страница отписки",
    )
    session.add(entry)
    await session.commit()
    return entry


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


def _path(path: str, row: SuppressionModel) -> str:
    return path.replace("{row}", str(row.id))


class TestWhoIsLetIn:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        row: SuppressionModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        response = await client.request(method, _path(path, row), json=body)

        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_operator_sees_but_does_not_change(
        self,
        client: AsyncClient,
        operator_token: str,
        row: SuppressionModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        response = await client.request(
            method, _path(path, row), json=body, headers=bearer(operator_token)
        )

        if permission == "send":
            assert response.status_code == 403
            assert "«send»" in response.json()["detail"]
        else:
            assert response.status_code == 200, response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/suppressions")
            for method in methods
        }
        in_table = {(method, path.replace("{row}", "{row_id}")) for method, path, _, _ in ROUTES}

        assert in_app == in_table


class TestSeeing:
    async def test_list_names_the_site_and_the_decision(
        self, client: AsyncClient, admin_token: str, row: SuppressionModel
    ) -> None:
        response = await client.get("/api/suppressions", headers=bearer(admin_token))

        body = response.json()
        assert body["total"] == 1
        assert body["donor_decisions"] == 1
        assert body["rows"][0]["host"] == HOST
        assert body["rows"][0]["donor_decision"] is True
        assert body["rows"][0]["created_by"] == "страница отписки"


class TestChanging:
    async def test_adding_goes_to_the_journal(
        self, client: AsyncClient, admin_token: str, session: AsyncSession
    ) -> None:
        response = await client.post(
            "/api/suppressions",
            json={"target": "supplier.example.test", "reason": "supplier"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200, response.text
        assert response.json()["host"] == "supplier.example.test"
        entry = (
            (
                await session.execute(
                    select(AuditLogModel).where(
                        AuditLogModel.action == AuditAction.SUPPRESSION_ADDED
                    )
                )
            )
            .scalars()
            .one()
        )
        assert entry.details is not None
        assert entry.details["кому не пишем"] == "supplier.example.test"

    async def test_unsubscribe_is_not_removed_silently(
        self, client: AsyncClient, admin_token: str, row: SuppressionModel, session: AsyncSession
    ) -> None:
        """Снять можно, но причина уходит в журнал: это разрешение
        написать тому, кто просил не писать."""
        response = await client.post(
            f"/api/suppressions/{row.id}/remove", json={}, headers=bearer(admin_token)
        )

        assert response.status_code == 409
        assert "надо написать, почему" in response.json()["detail"]
        assert await session.get(SuppressionModel, row.id) is not None

    async def test_reason_reaches_the_journal(
        self, client: AsyncClient, admin_token: str, row: SuppressionModel, session: AsyncSession
    ) -> None:
        response = await client.post(
            f"/api/suppressions/{row.id}/remove",
            json={"reason": "написал «пишите, передумал»"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 200, response.text
        entry = (
            (
                await session.execute(
                    select(AuditLogModel).where(
                        AuditLogModel.action == AuditAction.SUPPRESSION_REMOVED
                    )
                )
            )
            .scalars()
            .one()
        )
        assert entry.details is not None
        assert entry.details["почему сняли"] == "написал «пишите, передумал»"
        assert entry.details["решение адресата"] is True

    async def test_own_row_needs_no_reason(self, client: AsyncClient, admin_token: str) -> None:
        added = await client.post(
            "/api/suppressions",
            json={"target": "supplier.example.test", "reason": "supplier"},
            headers=bearer(admin_token),
        )

        response = await client.post(
            f"/api/suppressions/{added.json()['id']}/remove", json={}, headers=bearer(admin_token)
        )

        assert response.status_code == 200, response.text

    async def test_nonsense_target_is_refused(self, client: AsyncClient, admin_token: str) -> None:
        response = await client.post(
            "/api/suppressions",
            json={"target": "не домен", "reason": "manual"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 409
        assert "не похоже" in response.json()["detail"]
