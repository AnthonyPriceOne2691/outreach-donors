"""Учётки по HTTP: кто пускается в раздел и что там можно.

Права проверяются таблицей, а не примерами: на каждый маршрут раздела
проверяется, что без входа он отвечает «нужен вход», оператору —
«недоступно», а админу работает. Отдельный тест сверяет таблицу с самим
приложением — иначе новая ручка однажды уедет без проверки прав, и
заметят это по факту.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import AuditAction, UserRole
from backend.features.core.models.access import AuditLogModel, UserModel
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Маршруты раздела учёток: метод, путь с местом под номер, тело.
USER_ROUTES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("GET", "/api/users", None),
    ("POST", "/api/users", {"email": "ещё-один@site.com", "role": "operator"}),
    ("PATCH", "/api/users/{id}", {"is_active": False}),
    ("POST", "/api/users/{id}/password", None),
]


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


@pytest.fixture
async def operator_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("оператор@site.com", role=UserRole.OPERATOR)
    return await sign_in("оператор@site.com")


@pytest.fixture
async def target(make_user: MakeUser) -> UserModel:
    return await make_user("подопытный@site.com", role=UserRole.OPERATOR)


class TestWhoIsLetIn:
    """Матрица прав: одна и та же проверка на каждом маршруте раздела."""

    @pytest.mark.parametrize(("method", "path", "body"), USER_ROUTES)
    async def test_without_pass_nobody(
        self,
        client: AsyncClient,
        target: UserModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> None:
        response = await client.request(method, path.format(id=target.id), json=body)
        assert response.status_code == 401

    @pytest.mark.parametrize(("method", "path", "body"), USER_ROUTES)
    async def test_operator_is_refused(
        self,
        client: AsyncClient,
        operator_token: str,
        target: UserModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> None:
        response = await client.request(
            method, path.format(id=target.id), json=body, headers=bearer(operator_token)
        )
        assert response.status_code == 403
        # Отказ называет действие, а не «нет прав»: иначе через полгода
        # никто не ответит, чего именно не хватает человеку.
        assert "users" in response.json()["detail"]

    @pytest.mark.parametrize(("method", "path", "body"), USER_ROUTES)
    async def test_admin_is_let_in(
        self,
        client: AsyncClient,
        admin_token: str,
        target: UserModel,
        method: str,
        path: str,
        body: dict[str, Any] | None,
    ) -> None:
        response = await client.request(
            method, path.format(id=target.id), json=body, headers=bearer(admin_token)
        )
        assert response.status_code not in (401, 403), response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        """Таблица сверяется с приложением: маршрут без строки в таблице —
        это маршрут, права которого никто не проверял."""
        # Список берётся из схемы OpenAPI, а не из внутренностей приложения:
        # схема — то же самое, что видит клиент, и она не меняется от версии
        # к версии вместе с устройством маршрутизатора.
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/users")
            for method in methods
        }
        in_table = {(method, path.replace("{id}", "{user_id}")) for method, path, _ in USER_ROUTES}
        assert in_app == in_table


class TestCreate:
    async def test_new_user_gets_one_time_password_and_must_change_it(
        self, client: AsyncClient, admin_token: str, sign_in: SignIn
    ) -> None:
        response = await client.post(
            "/api/users",
            json={"email": "Новичок@Site.com", "role": "operator"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["user"]["email"] == "новичок@site.com"
        assert body["user"]["must_change_password"] is True
        # Пароль показан один раз — и он работает.
        token = await sign_in("новичок@site.com", body["password"])
        assert (await client.get("/api/auth/me", headers=bearer(token))).status_code == 200

    async def test_second_user_with_the_same_email_refused(
        self, client: AsyncClient, admin_token: str, target: UserModel
    ) -> None:
        response = await client.post(
            "/api/users",
            json={"email": target.email, "role": "operator"},
            headers=bearer(admin_token),
        )
        assert response.status_code == 409
        assert "уже существует" in response.json()["detail"]

    async def test_creation_is_written_down_with_its_author(
        self, client: AsyncClient, session: AsyncSession, admin_token: str
    ) -> None:
        await client.post(
            "/api/users",
            json={"email": "новичок@site.com", "role": "operator"},
            headers=bearer(admin_token),
        )

        # Порядок задаётся явно. Без него «последняя строка» — это та,
        # которую вернул планировщик: тест зеленел годами и мигнул
        # красным, когда в той же транзакции прибавилось строк.
        rows = await session.execute(
            select(AuditLogModel)
            .where(AuditLogModel.action == AuditAction.USER_CREATED)
            .order_by(AuditLogModel.id)
        )
        record = rows.scalars().all()[-1]
        assert record.user_id is not None  # автор известен — это не команда из консоли


class TestChangeAccess:
    async def test_pointed_right_is_given_and_taken_back(
        self, client: AsyncClient, admin_token: str, target: UserModel, sign_in: SignIn
    ) -> None:
        given = await client.patch(
            f"/api/users/{target.id}",
            json={"permissions": {"send": True}},
            headers=bearer(admin_token),
        )
        assert given.status_code == 200, given.text
        assert "send" in given.json()["permissions"]
        assert given.json()["overrides"] == {"send": True}

        taken = await client.patch(
            f"/api/users/{target.id}",
            json={"permissions": {}},
            headers=bearer(admin_token),
        )
        assert "send" not in taken.json()["permissions"]

    async def test_right_can_be_taken_below_the_role(
        self, client: AsyncClient, admin_token: str, target: UserModel
    ) -> None:
        """Исключения работают в обе стороны: у оператора можно отобрать
        то, что даёт роль, не заводя третью роль."""
        response = await client.patch(
            f"/api/users/{target.id}",
            json={"permissions": {"run": False}},
            headers=bearer(admin_token),
        )
        assert response.status_code == 200, response.text
        assert "run" not in response.json()["permissions"]

    async def test_unknown_action_refused_by_name(
        self, client: AsyncClient, admin_token: str, target: UserModel
    ) -> None:
        response = await client.patch(
            f"/api/users/{target.id}",
            json={"permissions": {"снд": True}},
            headers=bearer(admin_token),
        )
        assert response.status_code == 422
        assert "снд" in response.text

    async def test_empty_patch_refused(
        self, client: AsyncClient, admin_token: str, target: UserModel
    ) -> None:
        response = await client.patch(
            f"/api/users/{target.id}", json={}, headers=bearer(admin_token)
        )
        assert response.status_code == 422

    async def test_unknown_user_is_not_found(self, client: AsyncClient, admin_token: str) -> None:
        response = await client.patch(
            "/api/users/9999", json={"is_active": False}, headers=bearer(admin_token)
        )
        assert response.status_code == 404

    async def test_disabled_user_cannot_get_in(
        self, client: AsyncClient, admin_token: str, target: UserModel
    ) -> None:
        await client.patch(
            f"/api/users/{target.id}", json={"is_active": False}, headers=bearer(admin_token)
        )
        response = await client.post(
            "/api/auth/login", json={"email": target.email, "password": "пароль-для-теста"}
        )
        assert response.status_code == 401


class TestSelfLockout:
    """Сервис не должен оставаться без того, кто может впустить человека."""

    async def test_admin_cannot_disable_himself(
        self, client: AsyncClient, admin_token: str, make_user: MakeUser
    ) -> None:
        me = (await client.get("/api/auth/me", headers=bearer(admin_token))).json()
        await make_user("второй-админ@site.com", role=UserRole.ADMIN)

        response = await client.patch(
            f"/api/users/{me['id']}", json={"is_active": False}, headers=bearer(admin_token)
        )

        assert response.status_code == 409
        assert "самого себя" in response.json()["detail"]

    async def test_last_admin_cannot_be_demoted(
        self, client: AsyncClient, admin_token: str, make_user: MakeUser, sign_in: SignIn
    ) -> None:
        """Второй админ разжалует первого, оставаясь единственным, —
        отказ по счёту действующих админов, а не по имени."""
        first = (await client.get("/api/auth/me", headers=bearer(admin_token))).json()
        await make_user("второй-админ@site.com", role=UserRole.ADMIN)
        second_token = await sign_in("второй-админ@site.com")

        demoted = await client.patch(
            f"/api/users/{first['id']}", json={"role": "operator"}, headers=bearer(second_token)
        )
        assert demoted.status_code == 200, demoted.text

        last = await client.get("/api/auth/me", headers=bearer(second_token))
        response = await client.patch(
            f"/api/users/{last.json()['id']}",
            json={"role": "operator"},
            headers=bearer(second_token),
        )
        assert response.status_code == 409


class TestReset:
    async def test_reset_gives_a_new_one_time_password(
        self, client: AsyncClient, admin_token: str, target: UserModel, sign_in: SignIn
    ) -> None:
        response = await client.post(
            f"/api/users/{target.id}/password", headers=bearer(admin_token)
        )

        assert response.status_code == 200, response.text
        password = response.json()["password"]
        assert response.json()["user"]["must_change_password"] is True
        old = await client.post(
            "/api/auth/login", json={"email": target.email, "password": "пароль-для-теста"}
        )
        assert old.status_code == 401
        await sign_in(target.email, password)

    async def test_journal_names_the_admin_not_the_owner(
        self,
        client: AsyncClient,
        session: AsyncSession,
        admin_token: str,
        target: UserModel,
    ) -> None:
        """«Сотрудник сменил себе пароль» и «админ выдал новый» — разные
        события, и второе интересно ровно при разборе доступа."""
        me = (await client.get("/api/auth/me", headers=bearer(admin_token))).json()

        await client.post(f"/api/users/{target.id}/password", headers=bearer(admin_token))

        rows = await session.execute(
            select(AuditLogModel).where(AuditLogModel.action == AuditAction.PASSWORD_CHANGED)
        )
        record = rows.scalars().all()[-1]
        assert record.user_id == me["id"]
        assert record.target == f"user:{target.id}"
