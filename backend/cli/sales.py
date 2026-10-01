"""Команды модуля «Продажи».

    outreach sales-hypothesis-add --name "…" --description "…"

Стартовые гипотезы заводятся здесь, данными в базе, а не миграцией:
описание гипотезы — коммерческий текст, а репозиторий публичный. Логика
заведения — `features/sales/hypotheses.py`; здесь только разбор и печать.
"""

from __future__ import annotations

import argparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.sales import hypotheses

EXIT_OK = 0
EXIT_TAKEN = 3
EXIT_BAD_NAME = 4


async def cmd_sales_hypothesis_add(args: argparse.Namespace) -> int:
    """Завести гипотезу продаж."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await run_hypothesis_add(session, args.name, args.description)
    finally:
        await engine.dispose()


async def run_hypothesis_add(session: AsyncSession, name: str, description: str | None) -> int:
    """Заведение на готовой сессии — отдельно от команды ради теста."""
    try:
        hypothesis = await hypotheses.add(session, name, description)
    except hypotheses.BadNameError as exc:
        print(f"Гипотеза не заведена: {exc}")
        return EXIT_BAD_NAME
    except hypotheses.NameTakenError as exc:
        print(f"Гипотеза не заведена: {exc}")
        return EXIT_TAKEN
    await session.commit()
    print(f"Заведена гипотеза №{hypothesis.id} «{hypothesis.name}».")
    return EXIT_OK


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    add = sub.add_parser("sales-hypothesis-add", help="завести гипотезу продаж: кому и зачем пишем")
    add.add_argument(
        "--name", required=True, help="короткое имя — по нему гипотезу выбирают при загрузке базы"
    )
    add.add_argument("--description", help="кому и зачем пишем, словами; хранится в базе")
