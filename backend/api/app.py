"""Сборка приложения.

Приложение собирается функцией, а не создаётся при импорте модуля:
создание проверяет настройки и падает без них, а импорт случается
в том числе в тестах и в инструментах, которым сервер не нужен.
Запуск сервера — `backend/api/main.py`.
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.api.auth import routes as auth_routes
from backend.api.errors import install
from backend.api.users import routes as users_routes
from backend.config.startup_checks import check_access, check_storage
from backend.shared.logs import setup_logging

API_PREFIX = "/api"


def create_app() -> FastAPI:
    """Собрать приложение. Настроек не хватает — отказ здесь и сразу."""
    # Логи собираются тем же способом, что и в командах: иначе поля из
    # `extra` теряет стандартный форматтер, и структурность, за которую
    # заплачено дисциплиной на каждом вызове, до вывода не доходит.
    setup_logging()
    check_storage()
    check_access()

    app = FastAPI(
        title="outreach-donors",
        summary="Поиск доноров по выдаче и сбор цен размещения",
        version="0.1.0",
        # Схема и песочница живут под тем же префиксом, что и маршруты:
        # на сервере наружу открыт один путь, и всё остальное за ним.
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
        openapi_url=f"{API_PREFIX}/openapi.json",
    )

    install(app)
    app.include_router(auth_routes.router, prefix=API_PREFIX)
    app.include_router(users_routes.router, prefix=API_PREFIX)
    return app
