"""Посмотреть меню главных у очереди: `outreach doors`.

Новый прогон делает это сам (`donors.doors`), а очередь, положенная
до того, двери не знает: у неё признак «продаёт размещение» есть только
по ответу сайта и по судье. Команда досматривает главные тех, кто ждёт
решения, — бесплатно, по одному запросу на домен; уже известное второй
раз не качается. Повтор безопасен.
"""

from __future__ import annotations

import argparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.models.run import RunCandidateModel, RunModel
from backend.features.donors.doors import door_check
from backend.features.donors.repository import DonorRepository
from backend.features.review.candidates import Decision, RunReview

EXIT_OK = 0
EXIT_NO_RUN = 2
EXIT_OFF = 3


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "doors", help="посмотреть меню главных у очереди: кто зовёт авторов и рекламодателей"
    )
    parser.add_argument(
        "--run", type=int, default=None, help="номер прогона; без него — все, где есть очередь"
    )


async def _run_ids(session: AsyncSession, run: int | None) -> list[int]:
    if run is not None:
        return [run]
    rows = await session.execute(
        select(RunCandidateModel.run_id)
        .where(RunCandidateModel.status == Decision.PENDING.value)
        .distinct()
        .order_by(RunCandidateModel.run_id)
    )
    return list(rows.scalars().all())


async def cmd_doors(args: argparse.Namespace) -> int:
    check_storage()
    engine = create_async_engine(storage.DSN)
    totals = {"checked": 0, "found": 0, "unreached": 0, "opened": 0}
    try:
        async with (
            async_sessionmaker(engine, expire_on_commit=False)() as session,
            door_check() as doors,
        ):
            if doors is None:
                print("Главные выключены настройкой JUDGE_HOME_CHECK — смотреть нечем.")
                return EXIT_OFF
            if args.run is not None and await session.get(RunModel, args.run) is None:
                print(f"Прогона №{args.run} нет.")
                return EXIT_NO_RUN
            review, donors = RunReview(session), DonorRepository(session)
            for run_id in await _run_ids(session, args.run):
                report = await doors(
                    donors, await review.pending_hosts(run_id), checkpoint=session.commit
                )
                for key, value in report.as_dict().items():
                    totals[key] += value
                print(
                    f"Прогон №{run_id}: главных проверено {report.checked}, "
                    f"зовут авторов или рекламодателей {report.found}, "
                    f"не открылись {report.unreached}, "
                    f"отказов «продаёт своё» отдано человеку {report.opened}."
                )
    finally:
        await engine.dispose()

    print(
        f"Итого: проверено {totals['checked']}, дверь у {totals['found']}, "
        f"не открылись {totals['unreached']}, отдано человеку {totals['opened']}."
    )
    return EXIT_OK
