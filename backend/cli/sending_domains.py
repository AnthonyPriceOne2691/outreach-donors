"""Домены рассылки из консоли: лимит домена, выдержка нового домена, пауза (Ф4, 4.5a).

    outreach sending-domain --domain mail-b.example --stage sales --daily-limit 30
    outreach sending-domain --domain mail-b.example --pause "жалоба" | --resume

Лимит — первых писем в сутки со всех ящиков домена (`features/outreach/limits.py`).
Выдержка — неделя только домену, с которого ещё не ушло ни одного письма: молодой домен
почтовые сервисы штрафуют, а пишущий выдержка остановила бы целиком — ему она ставится
лишь явным `--young-days`. Этап домена — этап его ящиков: с чужим этапом фильтр отсеял
бы их все («домен записан за другим направлением»), поэтому такой этап — отказ с именами
ящиков. Строка печатает то, что увидит отправка.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import Stage
from backend.features.core.models.outreach import MessageModel, SenderModel, SendingDomainModel

EXIT_OK = 0
EXIT_INCOMPLETE = 5
EXIT_REFUSED = 6
#: Сколько дней новый домен выдерживают до первого письма.
YOUNG_DAYS = 7


async def cmd_sending_domain(args: argparse.Namespace) -> int:
    """Завести домен рассылки или поправить названное: лимит, выдержку, паузу."""
    check_storage()
    domain, moment = domain_name(args.domain), datetime.now(UTC)
    engine = create_async_engine(storage.DSN)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            boxes = (
                await session.scalars(select(SenderModel).where(SenderModel.domain == domain))
            ).all()
            foreign = [box for box in boxes if args.stage and box.stage.value != args.stage]
            if foreign:
                print(_foreign(domain, args.stage, foreign))
                return EXIT_REFUSED
            row = await session.scalar(
                select(SendingDomainModel).where(SendingDomainModel.domain == domain)
            )
            if row is None:
                if args.stage is None or args.daily_limit is None:
                    print(f"Домена {domain} ещё нет: заводится с --stage и --daily-limit")
                    return EXIT_INCOMPLETE
                young = None if await _wrote(session, boxes) else moment + timedelta(YOUNG_DAYS)
                row = SendingDomainModel(domain=domain, young_until=young)
                session.add(row)
            _apply(row, args, moment)
            await session.commit()
            print(_said(row, moment))
    finally:
        await engine.dispose()
    return EXIT_OK


async def _wrote(session: AsyncSession, boxes: Sequence[SenderModel]) -> bool:
    """Уходило ли с ящиков домена хоть одно письмо: такой домен уже пишет."""
    sent = select(MessageModel.id).where(
        MessageModel.sender_id.in_([box.id for box in boxes]), MessageModel.sent_at.is_not(None)
    )
    return bool(boxes) and await session.scalar(sent.limit(1)) is not None


def _foreign(domain: str, stage: str, boxes: Sequence[SenderModel]) -> str:
    named = ", ".join(f"{box.email} ({box.stage.value})" for box in boxes)
    return (
        f"Домен {domain} не записан за «{stage}»: его ящики {named} — другого этапа, и фильтр "
        "отсеял бы их все («домен записан за другим направлением»). Этап домена — этап его ящиков"
    )


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


def _said(row: SendingDomainModel, moment: datetime) -> str:
    """Состояние домена одной строкой — то, что увидит отправка."""
    states = [f"на паузе ({row.pause_reason})"] if row.paused_at else []
    if row.young_until is not None and row.young_until > moment:
        until = f"{row.young_until:%d.%m %H:%M} UTC"
        states.append(f"на выдержке до {until} — первые письма с домена не уходят")
    return f"Домен {row.domain} ({row.stage.value}): лимит {row.daily_limit}, " + (
        ", ".join(states) or "пишет"
    )


def domain_name(text: str) -> str:
    """Домен, как его пишут ящики: строчными, без точки на конце. Адрес — отказ словами."""
    name = text.strip().lower().rstrip(".")
    if "@" in name:
        raise argparse.ArgumentTypeError(
            f"«{text}» — адрес, а не домен: нужен домен ящика, {name.rpartition('@')[2]}"
        )
    if "." not in name or any(char.isspace() for char in name):
        raise argparse.ArgumentTypeError(f"«{text}» — не домен: нужен вида mail-b.example")
    return name


def _at_least(low: int) -> Callable[[str], int]:
    """Целое не меньше `low` — отказ разбора словами, а не трасса базы."""

    def parse(text: str) -> int:
        value = int(text) if text.strip().lstrip("-").isdigit() else None
        if value is None or value < low:
            raise argparse.ArgumentTypeError(f"«{text}» — нужно целое число от {low}")
        return value

    return parse


def _reason(text: str) -> str:
    """Причина паузы — словами: пустая пауза ничего бы не объяснила."""
    if not text.strip():
        raise argparse.ArgumentTypeError("причина паузы — словами, а не пустая строка")
    return text.strip()


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    domain = sub.add_parser("sending-domain", help="домен рассылки: лимит, выдержка, пауза")
    domain.add_argument("--domain", required=True, type=domain_name, help="например mail-b.example")
    domain.add_argument("--stage", choices=[stage.value for stage in Stage], help="этап его ящиков")
    domain.add_argument(
        "--daily-limit", type=_at_least(1), help="первых писем в сутки со всего домена, от 1"
    )
    domain.add_argument(
        "--young-days", type=_at_least(0), help=f"выдержка, дней (новому без писем — {YOUNG_DAYS})"
    )
    switch = domain.add_mutually_exclusive_group()
    switch.add_argument("--pause", type=_reason, help="поставить домен на паузу с причиной")
    switch.add_argument("--resume", action="store_true", help="снять паузу")
