"""Очередь писем из командной строки: собрать и отправить.

Те же действия, что на экране писем, и тем же кодом. Консоль нужна не
вместо экрана, а до него: очередь собирается минутами и стоит вызовов
модели, а такую работу запускают из терминала или из очереди задач,
а не из открытой вкладки.

**Отправка по одному письму и с подтверждением.** Пакетная отправка
«всей очереди» не сделана намеренно: смысл экрана писем в том, что
спорное решение видит человек, и команда, отправляющая двести писем
одним нажатием, этот смысл отменяет.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import MessageStatus
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import MessageModel
from backend.features.letters.building import BuildReport, BuildRequest, QueueBuilder
from backend.features.letters.rewrite import RewriteClient
from backend.features.letters.sending import SendError, Sending
from backend.features.letters.transport import build_transport
from backend.features.letters.uniqueness import corridor_verdict

EXIT_OK = 0
EXIT_NOT_SENT = 7


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    build = sub.add_parser("letters-build", help="собрать очередь писем по базе доноров")
    build.add_argument("--campaign", required=True, help="имя кампании: одноимённая дополняется")
    build.add_argument("--limit", type=int, default=50, help="сколько писем готовить за раз")
    build.add_argument("--country", default="us", help="страна прогона: от неё язык письма")
    build.add_argument(
        "--niche",
        default="",
        help="ключи прогона через запятую — ниша, по которой донор нашёлся",
    )

    show = sub.add_parser("letters", help="что стоит в очереди на отправку")
    show.add_argument("--limit", type=int, default=20, help="сколько строк показать")

    send = sub.add_parser("letters-send", help="отправить одно письмо из очереди")
    send.add_argument("--id", type=int, required=True, help="номер письма")


def _sessions() -> async_sessionmaker:  # type: ignore[type-arg]
    check_storage()
    return async_sessionmaker(create_async_engine(storage.DSN), expire_on_commit=False)


async def cmd_letters_build(args: argparse.Namespace) -> int:
    """Собрать очередь. Ничего не отправляет."""
    niche = tuple(x.strip() for x in args.niche.split(",") if x.strip())
    factory = _sessions()
    rewriter = RewriteClient()
    try:
        async with factory() as session:
            report = await QueueBuilder(session, rewriter).build(
                BuildRequest(
                    campaign_name=args.campaign,
                    country=args.country,
                    niche=niche,
                    limit=args.limit,
                )
            )
            await session.commit()
    finally:
        await rewriter.aclose()

    _print_build(report)
    return EXIT_OK


def _print_build(report: BuildReport) -> None:
    """Отчёт сборки. Отдельно от самой сборки: печатать и считать —
    разные темы, и растёт из них обычно печать."""
    print(f"Кампания №{report.campaign_id}: подготовлено писем {report.prepared}.")
    print("Отбор: " + ", ".join(f"{name} {count}" for name, count in report.funnel.items()))
    print(f"Токенов потрачено: {report.tokens_spent}.")

    if report.blocked_by:
        print(
            "Отправить нельзя ни одно из них: не заполнено "
            + ", ".join(report.blocked_by)
            + ". Письма собраны и видны, но отправка откажет."
        )
    if report.off_corridor:
        print(f"Вне коридора отличия: {report.off_corridor} — посмотреть перед отправкой.")
    for note, times in sorted(report.notes.items(), key=lambda pair: -pair[1]):
        print(f"  {note}: {times}")


async def cmd_letters(args: argparse.Namespace) -> int:
    """Показать очередь."""
    factory = _sessions()
    async with factory() as session:
        rows = await session.execute(
            select(MessageModel.id, DomainModel.host, MessageModel.uniqueness_pct)
            .join(DomainModel, DomainModel.id == MessageModel.domain_id)
            .where(MessageModel.status == MessageStatus.QUEUED)
            .order_by(MessageModel.id)
            .limit(args.limit)
        )
        found = rows.all()

    if not found:
        print("Очередь пуста. Собрать: outreach letters-build --campaign «имя»")
        return EXIT_OK

    for message_id, host, uniqueness in found:
        share = uniqueness or 0.0
        verdict = corridor_verdict(share)
        mark = f"  ← {verdict}" if verdict else ""
        print(f"№{message_id:>5}  {host:<40} отличие {round(share * 100):>3}%{mark}")
    return EXIT_OK


async def cmd_letters_send(args: argparse.Namespace) -> int:
    """Отправить одно письмо."""
    transport = build_transport()
    if not transport.real:
        print(
            f"Транспорт «{transport.name}» ничего не отправляет: письмо будет помечено "
            f"отправленным, но наружу не уйдёт. Работает только на выдуманных доменах."
        )

    factory = _sessions()
    async with factory() as session:
        try:
            outcome = await Sending(session, transport).send(args.id)
        except SendError as exc:
            print(f"Письмо не отправлено: {exc}")
            return EXIT_NOT_SENT

    fate = "ушло" if outcome.real else "НЕ ушло (нулевой транспорт)"
    print(f"Письмо №{outcome.message_id} {fate}, ящик {outcome.sender_email}.")
    return EXIT_OK
