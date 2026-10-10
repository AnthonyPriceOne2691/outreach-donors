"""Двойной щелчок «Запустить»: два одинаковых запуска разом — один прогон (аудит 10.10.2026, №2).

**Гонка здесь настоящая.** У каждого запроса своя сессия и своё соединение, данные
фиксируются по-настоящему (`committed_sessions`), после теста база вычищается. Постановка
в очередь задержана: второй запуск гарантированно спрашивает «такой уже есть?», пока
первый ещё не зафиксирован, — без замка оба прочли бы «нет» и оба купили бы выдачу.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from backend.api import deps
from backend.api.app import create_app
from backend.features.access.attempts import LoginAttempts
from backend.features.access.repository import AccessRepository
from backend.features.core.domain import UserRole
from backend.features.core.models.run import RunModel
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from tests.conftest import bearer
from tests.test_send_race import committed_sessions

PASSWORD = "пароль-для-двойного-щелчка"
BODY = {"keywords": ["ремонт квартир"], "country": "us", "depth_pages": 1}


class SlowQueue:
    """Очередь, которая ставит задачу не сразу: пока первый запуск ждёт её в пуле потоков,
    второй успевает дойти до проверки «такой уже есть?»."""

    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []

    def enqueue(self, *args: Any, **_: Any) -> object:
        time.sleep(0.3)
        self.calls.append(args)
        return type("Job", (), {"id": f"job-{len(self.calls)}"})()


async def test_two_clicks_at_once_make_one_run(
    jwt_secret: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("backend.config.ahrefs.API_KEY", "ключ-для-теста")
    monkeypatch.setattr("backend.config.serp.SANDBOX", False)
    monkeypatch.setattr(deps, "attempts", LoginAttempts(limit=5))
    queue = SlowQueue()
    monkeypatch.setattr("backend.api.runs.routes.runs_queue", lambda: queue)

    async with committed_sessions() as factory:
        async with factory() as session:
            user = await AccessRepository(session).create(
                email="двойной@site.com", password=PASSWORD, role=UserRole.OPERATOR
            )
            user.must_change_password = False
            await session.commit()

        async def per_request() -> AsyncIterator[AsyncSession]:
            async with factory() as session:
                yield session

        app = create_app()
        app.dependency_overrides[deps.db_session] = per_request
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            login = await client.post(
                "/api/auth/login", json={"email": "двойной@site.com", "password": PASSWORD}
            )
            assert login.status_code == 200, login.text
            headers = bearer(login.json()["token"])

            answers = await asyncio.gather(
                client.post("/api/runs", json=BODY, headers=headers),
                client.post("/api/runs", json=BODY, headers=headers),
            )

        assert sorted(answer.status_code for answer in answers) == [200, 409], [
            answer.text for answer in answers
        ]
        assert len(queue.calls) == 1, "выдачу покупает один прогон"
        async with factory() as session:
            assert await session.scalar(select(func.count()).select_from(RunModel)) == 1
