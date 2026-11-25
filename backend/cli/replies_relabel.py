"""Команда `replies-relabel`: переразобрать вид лежащих ответов нынешними правилами.

Без `--yes` — только показ: какие ответы доноров и рекламодателей, ещё ждущие человека, нынешний
разбор называет автоответом и каким правилом. С `--yes` — то же с записью. Порядок работы — в ядре
(`features/replies/relabel.py`), здесь — доводы и печать.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.replies import relabel

logger = logging.getLogger(__name__)


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "replies-relabel",
        help="переразобрать вид ответов, ждущих человека (без --yes — только показать)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="записать: ответам, которые разбор называет автоответом, поставить автоответ",
    )


def _print(found: Sequence[relabel.Relabel], *, write: bool) -> None:
    verb = "стал" if write else "станет"
    for item in found:
        print(
            f"№{item.reply_id} {item.stage.value}: {verb} автоответом — {item.rule} · {item.subject[:80]}"
        )


async def run(session: AsyncSession, *, write: bool) -> int:
    """Показать — или записать — ответы, которые нынешний разбор называет автоответом."""
    found = await relabel.candidates(session)
    _print(found, write=write)
    if not found:
        print("Автоответов среди ответов, ждущих человека, нет.")
        return 0
    if write:
        changed = await relabel.apply(session, found)
        await session.commit()
        logger.info("ответы: переразобраны в автоответ", extra={"replies": changed})
        print(f"\nЗаписано: автоответом стали {changed}.")
        if changed < len(found):
            print(f"Не тронуты: {len(found) - changed} — их уже решил человек.")
    else:
        print(
            f"\nЭто показ — в базе ничего не изменилось. Станут автоответом: {len(found)}. "
            "Записать — та же команда с --yes."
        )
    return 0


async def cmd_replies_relabel(args: argparse.Namespace) -> int:
    """Переразобрать вид ответов, ждущих человека. Без `--yes` в базу не пишется ничего."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            return await run(session, write=args.yes)
    finally:
        await engine.dispose()
