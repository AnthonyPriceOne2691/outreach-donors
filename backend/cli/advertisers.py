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
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.contacts.search import search_contacts
from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.advertisers import SupplierDonorModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.crawl.gate import judge_run
from backend.features.crawl.promote import promote as promote_candidates

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

    promote = sub.add_parser(
        "advertisers-promote", help="перевести подходящих кандидатов в рекламодателей"
    )
    promote.add_argument(
        "--contacts",
        action="store_true",
        help="сразу искать им адреса той же лестницей, что и донорам (платная ступень)",
    )
    promote.add_argument(
        "--limit", type=int, default=100, help="сколько рекламодателей взять на поиск адреса"
    )
    promote.add_argument(
        "--no-paid",
        action="store_true",
        help="только бесплатные ступени лестницы: MX, страницы (и RDAP, если включена)",
    )

    suppliers = sub.add_parser(
        "suppliers-import", help="стоп-лист доноров-поставщиков из файла (по домену в строке)"
    )
    suppliers.add_argument("path", type=Path, help="файл со списком: домен, дальше через # причина")
    suppliers.add_argument("--by", required=True, help="кто внёс список")

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


async def cmd_advertisers_promote(args: argparse.Namespace) -> int:
    """Перевести кандидатов в рекламодателей и, если просят, найти адреса."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            report = await promote_candidates(session)
            await session.commit()

            print("\nПеревод кандидатов в рекламодателей:")
            for name, number in report.as_dict().items():
                print(f"  {name:<34} {number}")
            if report.hosts:
                print(f"  новые: {', '.join(report.hosts[:10])}")

            queue = AdvertiserContactRepository(session)
            waiting = await queue.pending_count()
            print(f"\nБез адреса: {waiting}")

            if not args.contacts:
                if waiting:
                    print(
                        "Искать адреса: тот же вызов с `--contacts` (платная ступень стоит денег)"
                    )
                return 0

            found = await search_contacts(
                session, limit=args.limit, queue=queue, no_paid=args.no_paid
            )
            print(f"Пройдено доменов: {found.walked}, адресов сохранено: {found.saved}")
            for note in found.notes:
                print(note)
    finally:
        await engine.dispose()
    return 0


def _read_suppliers(path: Path) -> list[tuple[str, str | None]]:
    """Домены из файла: по одному в строке, причина после решётки.

    Пустые строки и строки-комментарии пропускаются молча — список
    приходит от людей, и шапка «Доноры, где размещались» в нём будет.
    """
    out: list[tuple[str, str | None]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        host, _, note = line.partition("#")
        host = host.strip().lower().removeprefix("www.")
        if host:
            out.append((host, note.strip() or None))
    return out


async def cmd_suppliers_import(args: argparse.Namespace) -> int:
    """Внести стоп-лист доноров-поставщиков.

    Их рекламодатели — чужие клиенты и свои же размещения. Отсев идёт
    при переводе кандидата в рекламодатели, то есть до поиска контакта:
    платная ступень не тратится на того, кому не напишем.
    """
    check_storage()
    if not args.path.exists():
        print(f"Файла {args.path} нет.")
        return EXIT_NOT_FOUND

    rows = _read_suppliers(args.path)
    if not rows:
        print("В файле нет ни одного домена — список не тронут.")
        return EXIT_NOT_FOUND

    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            known = set((await session.execute(select(SupplierDonorModel.host))).scalars().all())
            added = 0
            for host, note in rows:
                if host in known:
                    continue
                session.add(SupplierDonorModel(host=host, note=note, added_by=args.by))
                added += 1
            await session.commit()

            total = len((await session.execute(select(SupplierDonorModel.host))).scalars().all())
            print(f"Добавлено: {added}, уже было: {len(rows) - added}. Всего в списке: {total}.")
            print("Отсев идёт при переводе кандидатов — `outreach advertisers-promote`.")
    finally:
        await engine.dispose()
    return 0
