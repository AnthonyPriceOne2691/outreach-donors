"""Сборка приложения.

Приложение собирается функцией, а не создаётся при импорте модуля:
создание проверяет настройки и падает без них, а импорт случается
в том числе в тестах и в инструментах, которым сервер не нужен.
Запуск сервера — `backend/api/main.py`.
"""

from __future__ import annotations

from fastapi import FastAPI

from backend.api.auth import routes as auth_routes
from backend.api.donors import routes as donors_routes
from backend.api.errors import install
from backend.api.health import router as health_router
from backend.api.inbound import routes as inbound_routes
from backend.api.letters import routes as letters_routes
from backend.api.replies import routes as replies_routes
from backend.api.runs import routes as runs_routes
from backend.api.senders import routes as senders_routes
from backend.api.settings import routes as settings_routes
from backend.api.threads import routes as threads_routes
from backend.api.unsubscribe import routes as unsubscribe_routes
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
    app.include_router(health_router, prefix=API_PREFIX)
    app.include_router(auth_routes.router, prefix=API_PREFIX)
    app.include_router(users_routes.router, prefix=API_PREFIX)
    app.include_router(runs_routes.router, prefix=API_PREFIX)
    app.include_router(donors_routes.router, prefix=API_PREFIX)
    app.include_router(senders_routes.router, prefix=API_PREFIX)
    app.include_router(settings_routes.router, prefix=API_PREFIX)
    app.include_router(threads_routes.router, prefix=API_PREFIX)
    app.include_router(letters_routes.router, prefix=API_PREFIX)
    app.include_router(inbound_routes.router, prefix=API_PREFIX)
    app.include_router(replies_routes.router, prefix=API_PREFIX)
    app.include_router(unsubscribe_routes.router, prefix=API_PREFIX)
    return app
