"""Команды кандидатов в рекламодатели: пересчитать и решить спорное.

Экран ручной проверки — впереди; до него та же работа делается здесь,
и делается тем же кодом: порядок живёт в ядре (`features/crawl/gate.py`),
а тут только доводы командной строки и печать. Пока порядок жил бы
в консоли, у экрана появился бы свой — и разошлись бы они молча.

**Пересчёт не ходит в сеть.** Ссылки уже в базе; веса скоринга будут
меняться, и прогонять ради этого чужие сайты заново незачем.
"""

from __future__ import annotations

import argparse
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl.gate import judge_run

logger = logging.getLogger(__name__)

EXIT_NOT_FOUND = 4

MEANING: dict[Verdict, str] = {
    Verdict.BOUGHT: "куплена — пишем",
    Verdict.PENDING: "спорно — смотрит человек",
    Verdict.SKIPPED: "мимо — баллов мало",
    Verdict.BLOCKED: "кому не пишем",
}


def add_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    judge = sub.add_parser("advertisers", help="пересчитать кандидатов по обходам и показать их")
    judge.add_argument("--run", type=int, default=None, help="номер обхода; без него — все")
    judge.add_argument(
        "--verdict",
        choices=[v.value for v in Verdict],
        default=None,
        help="показать только с этим вердиктом",
    )
    judge.add_argument("--limit", type=int, default=30, help="сколько строк печатать")

    decide = sub.add_parser("advertiser-decide", help="решение человека по спорному кандидату")
    decide.add_argument("--run", type=int, required=True, help="номер обхода")
    decide.add_argument("--domain", required=True, help="домен-получатель")
    decide.add_argument("--by", required=True, help="кто решил: почта оператора")
    group = decide.add_mutually_exclusive_group(required=True)
    group.add_argument("--yes", action="store_true", help="это рекламодатель, пишем")
    group.add_argument("--no", action="store_true", help="не рекламодатель")


def _print_candidate(row: CandidateModel) -> None:
    mark = {True: "ПОДТВЕРЖДЁН", False: "ОТКЛОНЁН"}.get(row.confirmed, "")  # type: ignore[arg-type]
    print(f"\n  {row.points:>3}  {row.target_root}  [{MEANING[row.verdict]}] {mark}")
    print(f"       донор {row.donor_host}, ссылок {row.links} с {row.pages} страниц")
    if row.best_anchor:
        print(f"       анкор {row.best_anchor[:60]!r}")
    if row.best_page_url:
        print(f"       страница {row.best_page_url}")
    for reason in row.reasons or []:
        print(f"       · {reason}")


async def cmd_advertisers(args: argparse.Namespace) -> int:
    """Пересчитать вердикты и показать кандидатов."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            runs = await _runs(session, args.run)
            if not runs:
                print("Обходов в базе нет — сначала `outreach crawl <домен> --save`.")
                return EXIT_NOT_FOUND

            for run in runs:
                await judge_run(session, run.id)
            await session.commit()

            rows = await _candidates(session, [r.id for r in runs], args.verdict)
            _print_summary(rows, runs)
            for row in rows[: args.limit]:
                _print_candidate(row)
            if len(rows) > args.limit:
                print(f"\n  … ещё {len(rows) - args.limit}; поднять потолок — `--limit`")
    finally:
        await engine.dispose()
    return 0


async def _runs(session: object, run_id: int | None) -> list[CrawlRunModel]:
    query = select(CrawlRunModel).order_by(CrawlRunModel.id)
    if run_id is not None:
        query = query.where(CrawlRunModel.id == run_id)
    return list((await session.execute(query)).scalars().all())  # type: ignore[attr-defined]


async def _candidates(
    session: object, run_ids: list[int], verdict: str | None
) -> list[CandidateModel]:
    query = (
        select(CandidateModel)
        .where(CandidateModel.crawl_run_id.in_(run_ids))
        .order_by(CandidateModel.points.desc(), CandidateModel.target_root)
    )
    if verdict is not None:
        query = query.where(CandidateModel.verdict == Verdict(verdict))
    return list((await session.execute(query)).scalars().all())  # type: ignore[attr-defined]


def _print_summary(rows: list[CandidateModel], runs: list[CrawlRunModel]) -> None:
    print(f"Обходов: {len(runs)}, кандидатов: {len(rows)}")
    for verdict in Verdict:
        number = sum(1 for row in rows if row.verdict is verdict)
        if number:
            print(f"  {MEANING[verdict]:<28} {number}")
    waiting = sum(1 for row in rows if row.verdict is Verdict.PENDING and row.confirmed is None)
    if waiting:
        print(
            f"\nЖдут человека: {waiting}. Решение — "
            "`outreach advertiser-decide --run N --domain X --by почта --yes|--no`"
        )


async def cmd_advertiser_decide(args: argparse.Namespace) -> int:
    """Записать решение человека. Оно сильнее вердикта скоринга."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            row = (
                await session.execute(
                    select(CandidateModel).where(
                        CandidateModel.crawl_run_id == args.run,
                        CandidateModel.target_root == args.domain.lower(),
                    )
                )
            ).scalar_one_or_none()
            if row is None:
                print(
                    f"Кандидата {args.domain} в обходе {args.run} нет. "
                    "Список — `outreach advertisers --run N`.",
                )
                return EXIT_NOT_FOUND

            row.confirmed = bool(args.yes)
            row.decided_by = args.by
            row.decided_at = datetime.now(UTC)
            await session.commit()
            print(
                f"{row.target_root}: {'подтверждён' if row.confirmed else 'отклонён'} "
                f"({args.by}). Балл скоринга {row.points} остался как был — "
                "по нему считается, как часто он ошибается."
            )
    finally:
        await engine.dispose()
    return 0
