"""Сборка пула ключей из интерфейса: второй режим того же поля.

Механика генерации замерена и оттестирована отдельно. Здесь проверяется
не она, а проводка и границы: кому можно, что видно и чем кончается отказ
модели. Главная граница — **этот маршрут не тратит ничего платного**:
пул собран не значит, что прогон запущен, и смета остаётся последним
рубежом перед тратой.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.keywords.client import LlmError
from backend.features.keywords.generator import Pool, PoolReport
from fastapi import FastAPI
from httpx import AsyncClient
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

POOL_BODY = {"preset": "reviews", "country": "za", "topics": ["ставки"], "cap": 6}

ROUTES: list[tuple[str, str, dict[str, Any] | None, str]] = [
    ("GET", "/api/keywords/presets", None, "run"),
    ("GET", "/api/keywords/languages?country=za", None, "run"),
    # Ключи страны, дававшие доноров, — часть запуска прогона.
    ("GET", "/api/keywords/yield?country=us", None, "run"),
    ("POST", "/api/keywords", POOL_BODY, "run"),
]


class FakeClient:
    """Модель-заглушка: фразы заранее известны, сеть не трогается."""

    def __init__(self, phrases: list[str] | None = None, *, fail: str = "") -> None:
        self._phrases = phrases if phrases is not None else []
        self._fail = fail
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


def _pool(phrases: list[str]) -> Pool:
    report = PoolReport(
        preset_name="reviews", country="za", language="English", cap=6, topic="ставки"
    )
    report.asked = 9
    report.received = len(phrases)
    report.tokens = 1313
    return Pool(keywords=phrases, report=report)


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Подменяется клиент и сборщик — оба, иначе тест пошёл бы в сеть."""
    seen: dict[str, Any] = {}
    client = FakeClient()
    monkeypatch.setattr("backend.api.keywords.routes.KeygenClient", lambda: client)
    seen["client"] = client

    class Builder:
        def __init__(self, _client: Any, *, topics: Any = ()) -> None:
            seen["topics"] = list(topics)

        async def build(self, **kwargs: Any) -> Pool:
            seen.update(kwargs)
            if seen.get("raise") is not None:
                raise seen["raise"]
            return _pool(["best betting sites south africa", "top bookmakers sa"])

    monkeypatch.setattr("backend.api.keywords.routes.PoolBuilder", Builder)
    return seen


class TestWhoMay:
    @pytest.mark.parametrize(("method", "path", "body", "permission"), ROUTES)
    async def test_operator_with_the_right_may(
        self,
        client: AsyncClient,
        make_user: MakeUser,
        sign_in: SignIn,
        model: dict[str, Any],
        method: str,
        path: str,
        body: dict[str, Any] | None,
        permission: str,
    ) -> None:
        """Пул ведёт к трате, пусть и не сразу, — поэтому право `run`,
        а не `view`."""
        await make_user(email="op@example.test", role=UserRole.OPERATOR)
        token = await sign_in(email="op@example.test")

        response = await client.request(method, path, json=body, headers=bearer(token))

        assert response.status_code == 200, response.text

    async def test_table_covers_every_route(self, api_app: FastAPI) -> None:
        in_app = {
            (method.upper(), path)
            for path, methods in api_app.openapi()["paths"].items()
            if path.startswith("/api/keywords")
            for method in methods
        }

        # Строка запроса в схему не входит: сравниваем по пути.
        assert in_app == {(method, path.split("?")[0]) for method, path, _, _ in ROUTES}


class TestPool:
    async def test_topics_reach_the_builder(
        self, client: AsyncClient, admin_token: str, model: dict[str, Any]
    ) -> None:
        """Темы — то, чего у генерации не было вовсе: без них пул выходит
        «обзоры в стране X», а не «обзоры про Y в стране X»."""
        await client.post("/api/keywords", json=POOL_BODY, headers=bearer(admin_token))

        assert model["topics"] == ["ставки"]
        assert model["country"] == "za"
        assert model["preset_name"] == "reviews"

    async def test_report_comes_with_the_pool(
        self, client: AsyncClient, admin_token: str, model: dict[str, Any]
    ) -> None:
        """Отчёт отдаётся целиком: пул, собранный наполовину из-за отказов,
        внешне неотличим от пула, который модель честно не набрала."""
        response = await client.post("/api/keywords", json=POOL_BODY, headers=bearer(admin_token))

        body = response.json()
        assert body["keywords"] == [
            "best betting sites south africa",
            "top bookmakers sa",
        ]
        assert body["tokens"] == 1313
        assert body["refusals"] == []

    async def test_model_client_is_closed(
        self, client: AsyncClient, admin_token: str, model: dict[str, Any]
    ) -> None:
        """Соединение закрывается и на успехе, и на отказе: иначе каждая
        сборка оставляла бы за собой открытый клиент."""
        await client.post("/api/keywords", json=POOL_BODY, headers=bearer(admin_token))

        assert model["client"].closed


class TestRefusals:
    async def test_empty_pool_from_refusals_is_an_error_not_a_result(
        self, client: AsyncClient, admin_token: str, model: dict[str, Any]
    ) -> None:
        """Отдать пустой список значит предложить человеку запустить
        прогон ни за чем — и заплатить за выдачу по пустому пулу."""
        model["raise"] = LlmError("пул ключей пуст: модель отказала 3 раз(а)")

        response = await client.post("/api/keywords", json=POOL_BODY, headers=bearer(admin_token))

        assert response.status_code == 502
        assert "модель отказала" in response.text
        assert model["client"].closed, "клиент не закрыт на отказе"

    async def test_unknown_market_refuses_instead_of_english(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        """Язык выводится из страны. Незнакомая страна — отказ, а не тихий
        английский: он увёл бы прогон в другой веб, и отчёт показал бы успех."""
        response = await client.post(
            "/api/keywords",
            json={**POOL_BODY, "country": "zz"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 422
        assert "карте рынков" in response.text

    async def test_languages_come_from_the_market(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        """Оператор выбирает только страну — языки показываются до сборки:
        пул на двух языках стоит вдвое дороже."""
        response = await client.get(
            "/api/keywords/languages?country=ca", headers=bearer(admin_token)
        )

        assert response.json() == ["English", "French"]

    async def test_too_many_combinations_refuse_with_a_way_out(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        """Тонкая доля на сочетание — это не маленький пул, а рваный:
        буфер просит с запасом, дедуп режет, на выходе два ключа из пяти."""
        response = await client.post(
            "/api/keywords",
            json={"preset": "reviews", "country": "ca", "topics": ["а", "б", "в"], "cap": 6},
            headers=bearer(admin_token),
        )

        assert response.status_code == 422
        assert "Поднимите потолок" in response.text

    async def test_unknown_preset_names_the_known_ones(
        self, client: AsyncClient, admin_token: str
    ) -> None:
        """Свободного ввода углов нет, поэтому опечатка в пресете — частый
        случай, и отказ обязан называть известные."""
        response = await client.post(
            "/api/keywords",
            json={**POOL_BODY, "preset": "такого-нет"},
            headers=bearer(admin_token),
        )

        assert response.status_code == 422
        assert "reviews" in response.text
