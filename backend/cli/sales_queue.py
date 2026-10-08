"""Команда очереди писем продаж: `outreach sales-queue --hypothesis "…" [--limit N]`.

Собирает первые письма цепочки лидам `ready` гипотезы и ставит их в очередь — ничего не
отправляет: очередь уходит общей отправкой (пачкой этапа продаж — `send_queue` — или по
одному письму). Логика — `features/sales/queue.py`; здесь только разбор и печать.

Продажи не подключены (выключатель, учётка, отписка, «Отправитель») — отказ словами и
код 9 из `main`, ни одного письма. Модель переписывает зоны `rewrite` — общий клиент
доноров (`LLM_API_KEY`); без ключа письма выходят шаблонными, отличие — ноль, и в
очередь они не встают: отчёт называет это числом «вне коридора».
"""

from __future__ import annotations

import argparse

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.letters.rewrite import RewriteClient
from backend.features.sales import hypotheses, queue

EXIT_OK = 0
#: Гипотезы с таким именем нет — чинится в доводах (как у остальных команд продаж).
EXIT_NO_HYPOTHESIS = 5


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    """Разбор аргументов команды."""
    build = sub.add_parser(
        "sales-queue", help="собрать очередь писем продаж: первые письма лидам гипотезы"
    )
    build.add_argument("--hypothesis", required=True, help="имя гипотезы, лидам которой пишем")
    build.add_argument("--limit", type=int, default=50, help="писем за раз, по умолчанию 50")


async def cmd_sales_queue(args: argparse.Namespace) -> int:
    """Собрать очередь писем продаж гипотезы."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    rewriter = RewriteClient()
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await run_sales_queue(session, rewriter, args.hypothesis, args.limit)
    finally:
        await rewriter.aclose()
        await engine.dispose()


async def run_sales_queue(
    session: AsyncSession, rewriter: RewriteClient, name: str, limit: int
) -> int:
    """Сборка на данной сессии и клиенте модели — их подменяет тест."""
    hypothesis = await hypotheses.find(session, name)
    if hypothesis is None:
        print(f"Гипотезы «{name}» нет — заведите её: outreach sales-hypothesis-add")
        return EXIT_NO_HYPOTHESIS
    report = await queue.build(session, rewriter, hypothesis_id=hypothesis.id, limit=limit)
    print(f"Очередь продаж гипотезы «{hypothesis.name}» (рассылка №{report.campaign_id}):")
    print(f"  новых писем: {report.prepared}, собрано заново: {report.refreshed}")
    print(f"  токенов модели: {report.tokens_spent}")
    for why, count in report.waiting.most_common():
        print(f"  ждут — {why}: {count}")
    if report.stopped:
        print(f"Сборка остановлена: {report.stopped}")
    print(
        "Ничего не отправлено: очередь уходит общей отправкой — пачкой этапа продаж или по письму."
    )
    return EXIT_OK
