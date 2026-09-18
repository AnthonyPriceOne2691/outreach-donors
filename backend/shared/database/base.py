"""Декларативная база и фабрика сессий."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from backend.config import storage


class Base(DeclarativeBase):
    """Общий предок всех моделей."""


engine = create_async_engine(storage.DSN, echo=storage.ECHO_SQL, pool_pre_ping=True)
SessionFactory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
