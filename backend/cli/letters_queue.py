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
from collections.abc import AsyncIterator, Callable, Coroutine
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import MessageStatus, Stage
from backend.features.core.models.domain import DomainModel
from backend.features.core.models.outreach import MessageModel
from backend.features.crawl.niche import LINKS, NICHE
from backend.features.letters import autosend
from backend.features.letters.building import BuildReport, BuildRequest, QueueBuilder
from backend.features.letters.chain import FIRST_STEP
from backend.features.letters.rewrite import RewriteClient
from backend.features.letters.rewrite_queue import (
    SENDING_ITSELF,
    QueueRewriter,
    RewriteReport,
    RewriteRequest,
)
from backend.features.letters.sending import SendError, Sending
from backend.features.letters.transport import MaybeSentError
from backend.features.letters.transport_factory import Transports, in_use
from backend.features.letters.uniqueness import corridor_verdict, percent_text

EXIT_OK = 0
EXIT_NOT_SENT = 7


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    build = sub.add_parser("letters-build", help="собрать очередь писем донорам или рекламодателям")
    build.add_argument("--campaign", required=True, help="имя кампании: одноимённая дополняется")
    build.add_argument(
        "--stage",
        choices=[stage.value for stage in Stage],
        default=Stage.DONORS.value,
        help="кому: donors — вопрос о цене принятым донорам, advertisers — оффер "
        "рекламодателям под найденную ссылку (без --runs)",
    )
    build.add_argument("--limit", type=int, default=50, help="сколько писем готовить за раз")
    build.add_argument("--country", default="us", help="страна прогона: от неё язык письма")
    build.add_argument(
        "--niche",
        default="",
        help="ключи прогона через запятую — ниша, по которой донор нашёлся",
    )
    build.add_argument(
        "--runs",
        default="",
        help="номера прогонов через запятую: в рассылку идут их принятые доноры, "
        "страна и ниша берутся из прогонов",
    )

    show = sub.add_parser("letters", help="что стоит в очереди на отправку")
    show.add_argument("--limit", type=int, default=20, help="сколько строк показать")

    send = sub.add_parser("letters-send", help="отправить одно письмо из очереди")
    send.add_argument("--id", type=int, required=True, help="номер письма")

    rewrite = sub.add_parser(
        "letters-rewrite", help="переписать моделью письма очереди, оставшиеся шаблоном"
    )
    rewrite.add_argument(
        "--stage",
        choices=[Stage.DONORS.value, Stage.ADVERTISERS.value],
        default=Stage.DONORS.value,
        help="чья очередь: donors — доноров, advertisers — рекламодателей",
    )
    rewrite.add_argument(
        "--audience",
        choices=[LINKS, NICHE],
        default=LINKS,
        help="аудитория рекламодателей: links — по найденной ссылке, niche — бизнесы ниши",
    )
    rewrite.add_argument(
        "--country", default="us", help="страна письма рекламодателю: прогонов у Этапа 2 нет"
    )


@asynccontextmanager
async def _sessions() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Сессии команды — и движок, закрытый после неё: незакрытый оставлял
    соединения сборщику мусора, и в тестах они всплывали в чужом тесте."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


async def cmd_letters_build(args: argparse.Namespace) -> int:
    """Собрать очередь. Ничего не отправляет."""
    niche = tuple(x.strip() for x in args.niche.split(",") if x.strip())
    rewriter = RewriteClient()
    try:
        async with _sessions() as factory, factory() as session:
            report = await QueueBuilder(session, rewriter).build(
                BuildRequest(
                    campaign_name=args.campaign,
                    stage=Stage(args.stage),
                    country=args.country,
                    niche=niche,
                    limit=args.limit,
                    run_ids=tuple(int(x) for x in args.runs.split(",") if x.strip()),
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
    if report.selected_letters:
        # Не отказ модели, поэтому не нотой ниже: подбор сам оставил зоны текстом шаблона.
        # Число после слова, как в строках выше: «писем 1» верно при любом числе.
        print(
            f"Подбор под коридор: писем {report.selected_letters}, "
            f"шаблонными оставлено зон {report.dropped_zones}."
        )
    for reason, times in report.bad_addresses.items():
        print(f"Пропущено, адрес не прошёл фильтр — {reason}: {times}")
    if report.address_check:
        said = ", ".join(f"{name} {count}" for name, count in report.address_check.items())
        print(f"Проверка адресов перед письмом: {said}.")
    if report.unchecked:
        print(f"Письма непроверенным адресам не собраны — {report.unchecked}.")
    for note, times in sorted(report.notes.items(), key=lambda pair: -pair[1]):
        print(f"  {note}: {times}")


async def cmd_letters(args: argparse.Namespace) -> int:
    """Показать очередь — первые письма, как на экране: у добивки и ответа
    свой путь и свой ящик (`letters/mailbox.py`)."""
    async with _sessions() as factory, factory() as session:
        rows = await session.execute(
            select(MessageModel.id, DomainModel.host, MessageModel.uniqueness_pct)
            .join(DomainModel, DomainModel.id == MessageModel.domain_id)
            .where(MessageModel.status == MessageStatus.QUEUED, MessageModel.step == FIRST_STEP)
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
        print(f"№{message_id:>5}  {host:<40} отличие {percent_text(share):>6}{mark}")
    return EXIT_OK


async def cmd_letters_send(args: argparse.Namespace) -> int:
    """Отправить одно письмо — учёткой почты его этапа."""
    async with (
        _sessions() as factory,
        factory() as session,
        in_use(Transports()) as transports,
    ):
        try:
            outcome = await Sending(session, transports).send(args.id)
        except SendError as exc:
            # Словами, а не трассировкой: «транспорт sendgrid, ключа нет» — это
            # настройка, и отказ уже говорит, какой переменной не хватает.
            print(f"Письмо не отправлено: {exc}")
            return EXIT_NOT_SENT
        except MaybeSentError as exc:
            print(f"Исход неизвестен, письмо осталось «отправляется»: {exc}")
            return EXIT_NOT_SENT

    fate = "ушло" if outcome.real else "НЕ ушло (нулевой транспорт)"
    print(f"Письмо №{outcome.message_id} {fate}, ящик {outcome.sender_email}.")
    return EXIT_OK


async def cmd_letters_rewrite(args: argparse.Namespace) -> int:
    """Переписать письма очереди, оставшиеся шаблоном из-за отказа модели, — тем же путём, что
    сборка (`letters/rewrite_queue.py`). Ничего не отправляет. Очередь уходит сама — отказ, как
    у кнопки: шаблонные письма уходили бы раньше, чем их перепишет модель."""
    stage = Stage(args.stage)
    rewriter = RewriteClient()
    try:
        async with _sessions() as factory, factory() as session:
            if await autosend.running(session, stage, args.audience) is not None:
                print(f"Не переписано: {SENDING_ITSELF}.")
                return EXIT_NOT_SENT
            report = await QueueRewriter(session, rewriter).rewrite(
                RewriteRequest(stage=stage, audience=args.audience, country=args.country)
            )
            await session.commit()
    finally:
        await rewriter.aclose()

    _print_rewrite(report)
    return EXIT_OK


def _print_rewrite(report: RewriteReport) -> None:
    """Итог переписывания — словами, как отчёт сборки."""
    print(
        f"Шаблонных писем взято {report.found}, переписано {report.rewritten}, "
        f"токенов {report.tokens_spent}."
    )
    if report.off_corridor:
        print(f"Вне коридора отличия: {report.off_corridor} — посмотреть перед отправкой.")
    for reason, letters in report.left.items():
        print(f"Не переписано, {reason}: {letters}")
    if report.stopped:
        print(f"Остановлено: {report.stopped}. Остальные письма — как были.")
    print(f"Шаблонных в очереди осталось: {report.remaining}.")


#: Команды писем → что выполнить (`cli/main._COMMANDS`).
COMMANDS: dict[str, Callable[[argparse.Namespace], Coroutine[Any, Any, int]]] = {
    "letters-build": cmd_letters_build,
    "letters": cmd_letters,
    "letters-send": cmd_letters_send,
    "letters-rewrite": cmd_letters_rewrite,
}
