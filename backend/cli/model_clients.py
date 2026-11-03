"""Сессия базы, клиент модели и клиент чужих сайтов — для команд, которые зовут модель по текстам
сайтов: досуд тематики (`topic_backfill.py`) и проход судьи рекламодателей (`advertiser_judge.py`).

Одним местом, а не копией в каждой команде: клиент сайтов — всегда с защитой адресов
(`shared/net/url_guard.guarded_client`) и тем же сроком, что у разбора главной (`home_timeout`), а
клиент модели — со сроком модели. Команда, собравшая их своей копией, могла бы забыть защиту.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import llm as llm_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.donors.home_signals import home_timeout
from backend.shared.net.url_guard import guarded_client


@asynccontextmanager
async def model_and_sites() -> AsyncIterator[
    tuple[AsyncSession, httpx.AsyncClient, httpx.AsyncClient]
]:
    """Сессия базы, клиент модели и клиент сайтов на время команды; движок закрывается после."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with (
            factory() as session,
            httpx.AsyncClient(timeout=llm_cfg.TIMEOUT_S) as model_http,
            guarded_client(timeout=home_timeout()) as site_http,
        ):
            yield session, model_http, site_http
    finally:
        await engine.dispose()
