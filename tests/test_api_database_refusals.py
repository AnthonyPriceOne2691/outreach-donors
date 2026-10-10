"""Отказ базы, который не поломка сервера, — 4xx словами (аудит 10.10.2026, №12).

Номер из адреса больше столбца `integer`, строка длиннее поля, два запроса за одну строку —
до этой правки маршрут отвечал «Internal Server Error». Классы проверены на настоящем
Postgres: asyncpg через SQLAlchemy отдаёт переполнение номера и длинную строку общим
`DBAPIError` с кодом класса 22, а не `DataError`, — поэтому обработчик смотрит на код.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable

import pytest
from backend.api import deps
from backend.api.deps import db_session
from backend.api.errors import DATA_REFUSED, RACED, install
from backend.features.core.domain import UserRole
from backend.features.core.models.access import UserModel
from backend.features.core.models.domain import DomainModel
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from tests.conftest import TEST_DSN, bearer

MakeUser = Callable[..., Awaitable[UserModel]]
SignIn = Callable[..., Awaitable[str]]

HUGE = 99_999_999_999
HOST = "twin.example.test"


@pytest.fixture
async def token(make_user: MakeUser, sign_in: SignIn) -> str:
    """Смотрит, ищет адреса и пишет ответы: маршруты ниже под разными правами."""
    await make_user("оператор@site.com", role=UserRole.OPERATOR, permissions={"send": True})
    return await sign_in("оператор@site.com")


class TestNumbersBeyondTheColumn:
    async def test_a_huge_thread_is_a_refusal_not_a_crash(
        self, client: AsyncClient, token: str
    ) -> None:
        """Ровно замер аудита: `GET /api/threads/99999999999` — пятисотка. Своей проверки
        номера у маршрута нет — отвечает общий обработчик отказов базы."""
        response = await client.get(f"/api/threads/{HUGE}", headers=bearer(token))

        assert response.status_code == 422, response.text
        assert response.json()["detail"] == DATA_REFUSED

    @pytest.mark.parametrize(
        ("path", "said"),
        [
            (f"/api/contacts/donors/{HUGE}", f"Донора №{HUGE} нет"),
            (f"/api/threads/1/replies/{HUGE}/draft", f"нет ответа №{HUGE}"),
        ],
        ids=["поиск адреса донору", "черновик агента"],
    )
    async def test_the_named_routes_say_not_found(
        self, client: AsyncClient, token: str, path: str, said: str
    ) -> None:
        """Два места, названные аудитом, — «не найдено», как у остальных номеров
        (`shared/database/ids.py`): такой строки нет по построению."""
        response = await client.post(path, headers=bearer(token))

        assert response.status_code == 404, response.text
        assert said in response.json()["detail"]


@pytest.fixture
async def engine(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncEngine]:
    """Сессия на запрос — настоящая (`deps.db_session`), на своём соединении: проверяется
    и то, что после отказа она откатана и соединение вернулось в пул."""
    made = create_async_engine(TEST_DSN)
    monkeypatch.setattr(deps, "SessionFactory", async_sessionmaker(made, expire_on_commit=False))
    yield made
    await made.dispose()


@pytest.fixture
async def bare(engine: AsyncEngine) -> AsyncIterator[AsyncClient]:
    """Приложение из одних отказов (`errors.install`) и трёх маршрутов на настоящей базе."""
    app = FastAPI()
    install(app)

    @app.post("/twins")
    async def twins(session: AsyncSession = Depends(db_session)) -> None:
        # Два запроса за одну строку: оба прошли проверку «такой ещё нет», вставка
        # второго упирается в уникальный индекс. Здесь — одной вставкой, без гонки.
        session.add_all([DomainModel(host=HOST), DomainModel(host=HOST)])
        await session.commit()

    @app.post("/long")
    async def long(session: AsyncSession = Depends(db_session)) -> None:
        session.add(DomainModel(host="x" * 300 + ".example.test"))
        await session.commit()

    @app.get("/alive")
    async def alive(session: AsyncSession = Depends(db_session)) -> int:
        return int(await session.scalar(text("SELECT 1")) or 0)

    @app.get("/down")
    async def down() -> None:
        raise OperationalError("SELECT 1", {}, Exception("база не отвечает"))

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def _domains(engine: AsyncEngine) -> int:
    async with engine.connect() as conn:
        return int(await conn.scalar(select(func.count()).select_from(DomainModel)) or 0)


async def test_two_requests_for_one_row_is_409_in_words(
    bare: AsyncClient, engine: AsyncEngine
) -> None:
    response = await bare.post("/twins")

    assert response.status_code == 409, response.text
    assert response.json()["detail"] == RACED
    assert RACED == "Это уже сделано или изменено другим запросом — обновите экран"
    assert engine.pool.checkedout() == 0, "сессия запроса закрыта — соединение в пуле"
    assert await _domains(engine) == 0, "откатано: ни одной строки"
    assert (await bare.get("/alive")).json() == 1, "следующий запрос работает"


async def test_a_string_longer_than_the_column_is_422_in_words(
    bare: AsyncClient, engine: AsyncEngine
) -> None:
    response = await bare.post("/long")

    assert response.status_code == 422, response.text
    assert response.json()["detail"] == DATA_REFUSED
    assert engine.pool.checkedout() == 0
    assert await _domains(engine) == 0


async def test_a_broken_database_stays_a_crash(bare: AsyncClient) -> None:
    """База не отвечает — это поломка, а не отказ запросу: обработчик её не прячет,
    пятисотка и трассировка остаются за сервером, как до него."""
    with pytest.raises(OperationalError):
        await bare.get("/down")
