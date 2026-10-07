"""Домены рассылки из консоли: лимит домена, выдержка нового домена, пауза (Ф4, 4.5a).

    outreach sending-domain --domain mail-b.example --stage sales --daily-limit 30
    outreach sending-domain --domain mail-b.example --pause "жалоба" | --resume

Лимит — первых писем в сутки со всех ящиков домена (`features/outreach/limits.py`);
новый домен выдерживается неделю — молодой домен почтовые сервисы штрафуют.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import SendingDomainModel

EXIT_OK = 0
EXIT_INCOMPLETE = 5
#: Сколько дней новый домен выдерживают до первого письма.
YOUNG_DAYS = 7


async def cmd_sending_domain(args: argparse.Namespace) -> int:
    """Завести домен рассылки или поправить названное: лимит, выдержку, паузу."""
    check_storage()
    domain, moment = args.domain.strip().lower(), datetime.now(UTC)
    engine = create_async_engine(storage.DSN)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            row = await session.scalar(
                select(SendingDomainModel).where(SendingDomainModel.domain == domain)
            )
            if row is None:
                if args.stage is None or args.daily_limit is None:
                    print(f"Домена {domain} ещё нет: заводится с --stage и --daily-limit")
                    return EXIT_INCOMPLETE
                row = SendingDomainModel(domain=domain, young_until=moment + timedelta(YOUNG_DAYS))
                session.add(row)
            _apply(row, args, moment)
            await session.commit()
            print(_said(row))
    finally:
        await engine.dispose()
    return EXIT_OK


def _apply(row: SendingDomainModel, args: argparse.Namespace, moment: datetime) -> None:
    """Правки строки домена из доводов команды: только названные."""
    if args.stage is not None:
        row.stage = Stage(args.stage)
    if args.daily_limit is not None:
        row.daily_limit = args.daily_limit
    if args.young_days is not None:
        row.young_until = moment + timedelta(days=args.young_days)
    if args.pause or args.resume:
        row.paused_at, row.pause_reason = (moment, args.pause[:128]) if args.pause else (None, None)


def _said(row: SendingDomainModel) -> str:
    """Состояние домена одной строкой — то, что увидит отправка."""
    state = f"на паузе ({row.pause_reason})" if row.paused_at else "пишет"
    young = f", выдержка до {row.young_until:%d.%m %H:%M} UTC" if row.young_until else ""
    return f"Домен {row.domain} ({row.stage.value}): лимит {row.daily_limit}, {state}{young}"


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    domain = sub.add_parser("sending-domain", help="домен рассылки: лимит, выдержка, пауза")
    domain.add_argument("--domain", required=True, help="домен рассылки, например mail-b.example")
    domain.add_argument("--stage", choices=[stage.value for stage in Stage], help="этап домена")
    domain.add_argument("--daily-limit", type=int, help="первых писем в сутки со всего домена")
    domain.add_argument("--young-days", type=int, help=f"выдержка, дней (нового — {YOUNG_DAYS})")
    switch = domain.add_mutually_exclusive_group()
    switch.add_argument("--pause", help="поставить домен на паузу с причиной")
    switch.add_argument("--resume", action="store_true", help="снять паузу")
