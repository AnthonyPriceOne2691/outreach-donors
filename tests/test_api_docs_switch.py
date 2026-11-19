"""Схема API и песочница — только там, где их включили (аудит 19.11.2026).

`/api/docs` и `/api/openapi.json` отвечали всегда, и закрывал их только пароль прокси. Схема —
карта всех ручек, а песочница грузит скрипт с чужого адреса на тот же адрес, где экран держит
пропуск. Теперь их включает настройка `API_DOCS_ENABLED` — по умолчанию выключена, и в образце
`.env`, из которого собирают боевой, тоже. Схему для тестов и обходов маршрутов
(`app.openapi()`) приложение собирает и без адреса.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.api import app as app_module
from backend.config import observability
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

SAMPLE = Path(__file__).resolve().parent.parent / ".env.example"
PLACES = ("/api/docs", "/api/openapi.json")


async def _answers(app: FastAPI) -> dict[str, int]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return {place: (await client.get(place)).status_code for place in PLACES}


def test_off_unless_switched_on() -> None:
    assert observability._Observability.model_fields["api_docs"].default is False
    assert "API_DOCS_ENABLED=false" in SAMPLE.read_text(encoding="utf-8").splitlines()


@pytest.mark.usefixtures("jwt_secret")
async def test_switched_off_there_is_neither(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observability, "API_DOCS", False)

    app = app_module.create_app()

    assert await _answers(app) == dict.fromkeys(PLACES, 404)
    assert "/api/auth/login" in app.openapi()["paths"], "схема для тестов собирается и так"


@pytest.mark.usefixtures("jwt_secret")
async def test_switched_on_both_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Для разработки: песочница и схема на месте, под тем же префиксом, что маршруты."""
    monkeypatch.setattr(observability, "API_DOCS", True)

    app = app_module.create_app()

    assert await _answers(app) == dict.fromkeys(PLACES, 200)
