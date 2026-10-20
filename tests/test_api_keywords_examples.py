"""Сборка ключей по примерам из интерфейса: маршрут целиком на поддельной сети и модели.

Главные — синтетические сайты за `httpx.MockTransport`, модель — заглушка: настоящих
сайтов и провайдеров тест не трогает. Проверяется то, что видит человек: ключи, тема,
каждый пример с исходом, токены в журнале расхода, — и что отказы, которые не ждут
модели, не стоят ни запроса к сайтам, ни токена.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.ops import UsageRecordModel
from backend.features.crawl.limiter import DomainLimiter
from backend.features.keywords.angles import load_prompt
from backend.features.keywords.client import Ask
from backend.features.keywords.example_pages import PageReader
from backend.features.keywords.example_theme import THEME_PROMPT
from backend.features.keywords.hygiene import parse_phrases
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

GARDEN = (
    '<html lang="de"><head><title>Gartenfreunde — Tipps für Garten und Balkon</title>'
    '<meta name="description" content="Ratgeber für Hobbygärtner: Gemüse, Rasen, Balkon.">'
    "</head><body><h1>Gartentipps</h1><h2>Tomaten pflanzen</h2>"
    "<p>Wir zeigen, wie Sie Tomaten und Kräuter anbauen.</p></body></html>"
)
THEME = {
    "theme": "блоги о садоводстве",
    "about": "Hobby gardening blogs with practical advice.",
    "topics": {"English": "home gardening"},
    "off_theme": [],
}


class FakeModel:
    """Модель-заглушка: о теме — объект (или отказ), углам — непохожие фразы."""

    def __init__(self, *, refuse_theme: bool = False, refuse_phrases: bool = False) -> None:
        self.refuse_theme = refuse_theme
        self.refuse_phrases = refuse_phrases
        self.tokens_spent = 0
        self.calls = 0
        self.refusals: list[str] = []
        self.closed = False
        self._count = 0

    def require_key(self) -> None:
        """Ключ у заглушки есть всегда — его отсутствие проверяет тест клиента."""

    async def reply(self, ask: Ask) -> str | None:
        self.calls += 1
        theme = ask.system == load_prompt(THEME_PROMPT)
        if (theme and self.refuse_theme) or (not theme and self.refuse_phrases):
            self.refusals.append("модель, отказ (чинить): HTTP 401: ключ не принят")
            return None
        self.tokens_spent += 300
        if theme:
            return json.dumps(THEME, ensure_ascii=False)
        self._count += 1
        return json.dumps(
            [f"тема{self._count}{n}альфа тема{self._count}{n}бета" for n in range(12)]
        )

    async def ask(self, ask: Ask) -> list[str]:
        content = await self.reply(ask)
        return parse_phrases(content) if content else []

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
async def admin_token(make_user: MakeUser, sign_in: SignIn) -> str:
    await make_user("админ@site.com", role=UserRole.ADMIN)
    return await sign_in("админ@site.com")


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Подделаны сеть до сайтов и модель: маршрут идёт настоящим путём сборки."""
    state: dict[str, Any] = {"seen": [], "model": FakeModel()}

    def handle(request: httpx.Request) -> httpx.Response:
        state["seen"].append(str(request.url))
        # `host` у httpx — письменностью сайта; на проводе — ASCII (`raw_host`).
        if request.url.raw_host == b"xn--80ai1b.example.test":
            if request.url.path == "/robots.txt":
                return httpx.Response(404)
            return httpx.Response(200, html=GARDEN)
        raise httpx.ConnectError("имя не найдено", request=request)

    monkeypatch.setattr(
        "backend.api.keywords.routes.guarded_client",
        lambda **_kwargs: httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    monkeypatch.setattr(
        "backend.api.keywords.routes.PageReader",
        lambda http: PageReader(http, limiter=DomainLimiter(delay_sec=0)),
    )
    monkeypatch.setattr("backend.api.keywords.routes.KeygenClient", lambda: state["model"])
    return state


def body(*examples: str, **extra: Any) -> dict[str, Any]:
    return {"examples": list(examples), "preset": "reviews", "country": "us", "cap": 6, **extra}


async def spent(session: AsyncSession) -> list[int | None]:
    rows = await session.scalars(
        select(UsageRecordModel.units).where(UsageRecordModel.operation == "keywords")
    )
    return list(rows)


class TestBuild:
    async def test_keys_theme_and_every_example_come_back(
        self,
        client: AsyncClient,
        admin_token: str,
        world: dict[str, Any],
        session: AsyncSession,
    ) -> None:
        response = await client.post(
            "/api/keywords/examples",
            json=body("https://сад.example.test/blog", "", "gone.example.test"),
            headers=bearer(admin_token),
        )

        assert response.status_code == 200, response.text
        made = response.json()
        assert made["theme"] == "блоги о садоводстве"
        assert made["topics"] == {"English": "home gardening"}
        assert len(made["keywords"]) == 6
        assert made["languages"] == ["English"]
        # Пример — письменностью сайта, как его вписал человек, и с исходом.
        assert made["sites"] == [
            {
                "host": "сад.example.test",
                "read": True,
                "reason": "",
                "title": "Gartenfreunde — Tipps für Garten und Balkon",
                "off_theme": False,
            },
            {
                "host": "gone.example.test",
                "read": False,
                "reason": "не открылся: имя не найдено или соединение отклонено",
                "title": "",
                "off_theme": False,
            },
        ]
        assert made["theme_tokens"] == 300
        assert made["tokens"] == world["model"].tokens_spent
        # Строка журнала — одна, на всю сборку: тема и ключи — одна модель и один счёт.
        assert await spent(session) == [made["tokens"]]
        assert world["model"].closed

    async def test_bad_lines_cost_neither_a_request_nor_a_token(
        self,
        client: AsyncClient,
        admin_token: str,
        world: dict[str, Any],
        session: AsyncSession,
    ) -> None:
        response = await client.post(
            "/api/keywords/examples",
            json=body("сад.example.test", "не сайт"),
            headers=bearer(admin_token),
        )

        assert response.status_code == 422
        assert "строка 2: «не сайт»" in response.text
        assert world["seen"] == []
        assert world["model"].calls == 0
        assert await spent(session) == []

    async def test_more_than_ten_sites_are_refused_in_our_words(
        self, client: AsyncClient, admin_token: str, world: dict[str, Any]
    ) -> None:
        """Потолок называет сборка словами, а не схема «не больше N элементов»."""
        lines = [f"site{number}.example.test" for number in range(11)]

        response = await client.post(
            "/api/keywords/examples", json=body(*lines), headers=bearer(admin_token)
        )

        assert response.status_code == 422
        assert "Примеров 11 — берём до 10" in response.text
        assert world["seen"] == []

    async def test_nothing_read_is_a_refusal_without_a_token(
        self,
        client: AsyncClient,
        admin_token: str,
        world: dict[str, Any],
        session: AsyncSession,
    ) -> None:
        response = await client.post(
            "/api/keywords/examples",
            json=body("gone.example.test", "lost.example.test"),
            headers=bearer(admin_token),
        )

        assert response.status_code == 422
        assert "Ни одна главная примеров не прочитана" in response.text
        assert "gone.example.test — не открылся" in response.text
        assert world["model"].calls == 0
        assert await spent(session) == []

    async def test_a_theme_refusal_is_502_with_the_reason(
        self, client: AsyncClient, admin_token: str, world: dict[str, Any]
    ) -> None:
        world["model"].refuse_theme = True

        response = await client.post(
            "/api/keywords/examples", json=body("сад.example.test"), headers=bearer(admin_token)
        )

        assert response.status_code == 502
        assert "общая тема примеров не выведена" in response.text
        assert "ключ не принят" in response.text
        assert world["model"].closed

    async def test_tokens_spent_before_a_refusal_are_recorded(
        self,
        client: AsyncClient,
        admin_token: str,
        world: dict[str, Any],
        session: AsyncSession,
    ) -> None:
        """Тема ответила, углы — нет: 502, а токены темы — строкой журнала, как у
        сборки по теме (аудит 10.10.2026: иначе расход выглядит меньше настоящего)."""
        world["model"].refuse_phrases = True

        response = await client.post(
            "/api/keywords/examples", json=body("сад.example.test"), headers=bearer(admin_token)
        )

        assert response.status_code == 502
        assert "модель отказала" in response.text
        assert await spent(session) == [300]


class TestRefusalsBeforeTheSites:
    async def test_unknown_market_refuses_instead_of_english(
        self, client: AsyncClient, admin_token: str, world: dict[str, Any]
    ) -> None:
        response = await client.post(
            "/api/keywords/examples",
            json=body("сад.example.test", country="zz"),
            headers=bearer(admin_token),
        )

        assert response.status_code == 422
        assert "карте рынков" in response.text
        assert world["seen"] == []

    async def test_guest_without_footprints_refuses_before_the_pages(
        self, client: AsyncClient, admin_token: str, world: dict[str, Any]
    ) -> None:
        """У японского рынка нет футпринтов «guest»: отказ — до главных и до модели,
        а не после минуты ожидания и вызова о теме."""
        response = await client.post(
            "/api/keywords/examples",
            json=body("сад.example.test", country="jp", preset="guest", cap=8),
            headers=bearer(admin_token),
        )

        assert response.status_code == 422
        assert "Футпринтов для языка «Japanese» нет" in response.text
        assert world["seen"] == []
        assert world["model"].calls == 0
