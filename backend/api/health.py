"""Живость сервиса: единственный маршрут без входа.

Нужен контейнеру и обратному прокси, а не человеку. Поэтому отвечает
ровно двумя словами и не рассказывает ничего о начинке: страница
состояния, доступная без входа, — это карта сервиса для того, кто его
изучает.

**Проверяется база, а не только сам процесс.** Сервер, который отвечает
«жив» с недоступной базой, заставляет контейнер считать себя рабочим
и принимать запросы, каждый из которых падает. Запрос дешёвый —
`SELECT 1`.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import db_session

logger = logging.getLogger(__name__)
router = APIRouter(tags=["служебное"])


@router.get("/health", summary="Жив ли сервис")
async def health(session: AsyncSession = Depends(db_session)) -> JSONResponse:
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        # Причина — в лог, наружу только состояние: текст ошибки базы
        # называет хост, пользователя и порт.
        logger.exception("проверка живости: база не ответила")
        return JSONResponse(
            {"status": "база недоступна"},
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )
    return JSONResponse({"status": "жив"})
