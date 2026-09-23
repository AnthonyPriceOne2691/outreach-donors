"""Положить на рассмотрение прогон, сделанный до очереди: `outreach review-queue`.

Прогоны до 23.09.2026 кончались базой, а не очередью: годные по порогам
сразу считались донорами. Команда кладёт годных такого прогона на
рассмотрение — ничего не покупает, ни юнитов, ни токенов. Повтор безопасен.
"""

from __future__ import annotations

import argparse

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.models.run import RunModel
from backend.features.review.candidates import RunReview
from backend.features.runs.exclusions import Exclusions

EXIT_OK = 0
EXIT_NO_RUN = 2


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "review-queue", help="положить на рассмотрение годных уже прошедшего прогона"
    )
    parser.add_argument("--run", type=int, required=True, help="номер прогона")


async def cmd_review_queue(args: argparse.Namespace) -> int:
    check_storage()
    engine = create_async_engine(storage.DSN)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            run = await session.get(RunModel, args.run)
            if run is None:
                print(f"Прогона №{args.run} нет.")
                return EXIT_NO_RUN
            hosts = list((run.candidates or {}).get("hosts") or [])
            # Те же исключения, что у нового прогона: госсайт, платформа
            # и отклонённый человеком на рассмотрение не попадают.
            excluded = await Exclusions(session).excluded_hosts(hosts, stage=run.stage)
            report = await RunReview(session).queue_run(
                run.id, [host for host in hosts if host not in excluded]
            )
            await session.commit()
    finally:
        await engine.dispose()

    print(f"Прогон №{args.run}: доменов в выдаче {len(hosts)}, исключено {len(excluded)}.")
    print(f"На рассмотрение:      {report.pending}")
    for decision, count in report.carried.items():
        print(f"  решено раньше ({decision}): {count}")
    return EXIT_OK
