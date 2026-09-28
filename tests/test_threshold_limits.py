"""Границы порогов: одни у схемы и у экрана (замечание 28.09.2026).

Экран проверяет поле до нажатия границами из ответа сервера, а не своей
копией чисел. До этого он знал только верх DR, трафик в сто миллионов
уходил на сервер, и отказ возвращался по-английски, а блок «Что станет
с базой» продолжал показывать прошлый ответ. Здесь проверяется, что
числа, которые получает экран, — ровно те, по которым отказывает схема:
на самой границе принимает, на шаг за ней — отказывает.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest
from backend.api.settings.schemas import LIMITS, ThresholdsBody
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from httpx import AsyncClient
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

#: Пороги, которые заведомо в границах: от них двигают по одному полю.
INSIDE = {"min_dr": 20, "min_org_traffic": 500, "min_refdomains": 100, "min_keywords": 300}


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("пороги@site.com", role=UserRole.ADMIN)
    return await sign_in("пороги@site.com")


class TestLimitsAreOnePlace:
    def test_every_threshold_has_limits(self) -> None:
        """Поле без границ экран проверить не сможет, а граница без поля —
        опечатка в имени."""
        assert set(LIMITS) == set(ThresholdsBody.model_fields)

    async def test_screen_gets_the_limits(self, client: AsyncClient, token: str) -> None:
        response = await client.get("/api/settings/thresholds", headers=bearer(token))

        assert response.status_code == 200, response.text
        assert response.json()["limits"] == {
            name: {"min": low, "max": high} for name, (low, high) in LIMITS.items()
        }


class TestSchemaRefusesByTheSameNumbers:
    @pytest.mark.parametrize("name", sorted(LIMITS))
    async def test_the_edges_themselves_pass(
        self, client: AsyncClient, token: str, name: str
    ) -> None:
        low, high = LIMITS[name]
        for value in (low, high):
            response = await client.post(
                "/api/settings/preview", json={**INSIDE, name: value}, headers=bearer(token)
            )
            assert response.status_code == 200, (name, value, response.text)

    @pytest.mark.parametrize("name", sorted(LIMITS))
    async def test_one_step_outside_is_refused(
        self, client: AsyncClient, token: str, name: str
    ) -> None:
        low, high = LIMITS[name]
        for value in (low - 1, high + 1):
            response = await client.post(
                "/api/settings/preview", json={**INSIDE, name: value}, headers=bearer(token)
            )
            assert response.status_code == 422, (name, value, response.text)

    async def test_a_fraction_is_refused(self, client: AsyncClient, token: str) -> None:
        """Порог — целое: DR 20,5 в базе не бывает, и экран не даёт его набрать."""
        response = await client.post(
            "/api/settings/preview", json={**INSIDE, "min_dr": 20.5}, headers=bearer(token)
        )

        assert response.status_code == 422
