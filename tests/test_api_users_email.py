"""Почта новой учётки: форма адреса проверяется до заведения — и отказ говорит, что не так.

Проверка QA 10.10.2026: хватало трёх знаков, и `a@b` заводился учёткой, которую
потом не удалить — только отключить, а `no-at-sign` отказывал словами «Почта —
она же логин», не называя ошибки. Учётки не удаляются намеренно (`AccessPatch`):
вместе с ними ушла бы история действий, — поэтому мусор надо не пускать на входе.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.api.users.schemas import mail_problem
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@example.com", role=UserRole.ADMIN)
    return await sign_in("админ@example.com")


class TestShape:
    @pytest.mark.parametrize(
        ("email", "says"),
        [
            ("a@b", "После «@» нужен домен с зоной"),
            ("a@b.c", "После «@» нужен домен с зоной"),
            ("a@b..com", "После «@» нужен домен с зоной"),
            ("no-at-sign", "нет «@»"),
            ("a@b@example.com", "больше одного «@»"),
            ("@example.com", "нет имени ящика"),
            ("ivan petrov@example.com", "пробел"),
            ("", "Впишите почту"),
            ("x" * 250 + "@example.com", "длиннее 255 знаков"),
        ],
    )
    def test_refusal_names_what_is_wrong(self, email: str, says: str) -> None:
        problem = mail_problem(email)

        assert problem is not None
        assert says in problem

    @pytest.mark.parametrize(
        "email",
        ["ivan@example.com", "Новичок@Example.com", "иван@пример.рф", "a.b+c@mail.example.co.uk"],
    )
    def test_sane_address_passes(self, email: str) -> None:
        assert mail_problem(email) is None


class TestCreate:
    async def test_address_without_a_zone_is_not_created(
        self, client: AsyncClient, admin_token: str, session: AsyncSession
    ) -> None:
        response = await client.post(
            "/api/users", json={"email": "a@b", "role": "operator"}, headers=bearer(admin_token)
        )

        assert response.status_code == 422
        # Текст доходит до экрана как написан: без «Value error, » и по-русски.
        assert response.json()["detail"][0]["msg"].startswith("После «@» нужен домен с зоной")
        created = await session.scalar(select(UserModel.id).where(UserModel.email == "a@b"))
        assert created is None

    async def test_missing_at_sign_is_named(self, client: AsyncClient, admin_token: str) -> None:
        response = await client.post(
            "/api/users",
            json={"email": "no-at-sign", "role": "operator"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 422
        assert "нет «@»" in response.json()["detail"][0]["msg"]

    async def test_spaces_around_are_not_a_mistake(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        """Пробелы по краям срезает хранилище (`normalize_email`) — отказывать за них незачем."""
        response = await client.post(
            "/api/users",
            json={"email": "  Ivan@Example.com ", "role": "operator"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 201, response.text
        assert response.json()["user"]["email"] == "ivan@example.com"
