"""Команды кандидатов в рекламодатели: пересчитать и решить спорное.

Экран ручной проверки — впереди; до него та же работа делается здесь,
и делается тем же кодом: порядок живёт в ядре (`features/crawl/gate.py`),
а тут только доводы командной строки и печать. Пока порядок жил бы
в консоли, у экрана появился бы свой — и разошлись бы они молча.

**Пересчёт не обходит сайты заново.** Ссылки уже в базе; веса скоринга
будут меняться, и прогонять ради этого чужие сайты незачем. В сеть он
ходит за одним — DR новых «куплено» и «спорно» для «DR > 80 — не пишем»:
пакетом, только ещё не спрошенный, юниты — в журнал расхода (`--no-dr` —
не спрашивать).
"""

from __future__ import annotations

import argparse
import functools
import logging
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import advertisers as suppliers_cfg
from backend.config import ahrefs as ahrefs_cfg
from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.ahrefs.client import AhrefsClient
from backend.features.ahrefs.units import UsageCollector
from backend.features.contacts.search import search_contacts
from backend.features.core import usage
from backend.features.core.domain import Verdict
from backend.features.core.models.advertiser import CandidateModel
from backend.features.core.models.crawl import CrawlRunModel
from backend.features.crawl import review, supplier_import, suppliers
from backend.features.crawl.big_sites import Ratings, ahrefs_ratings
from backend.features.crawl.contacts import AdvertiserContactRepository
from backend.features.crawl.gate import judge_run, latest_crawls
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
    judge.add_argument(
        "--run", type=int, default=None, help="номер обхода; без него — последний у каждого донора"
    )
    judge.add_argument(
        "--verdict",
        choices=[v.value for v in Verdict],
        default=None,
        help="показать только с этим вердиктом",
    )
    judge.add_argument("--limit", type=int, default=30, help="сколько строк печатать")
    judge.add_argument(
        "--no-dr",
        action="store_true",
        help="не спрашивать DR у Ahrefs (без юнитов): «DR > 80 — не пишем» не проверяется, "
        "и в причинах кандидатов так и будет написано",
    )

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
        "suppliers-import",
        help="стоп-лист доноров-поставщиков из файла (по домену в строке) — в стоп-лист экрана",
    )
    suppliers.add_argument("path", type=Path, help="файл со списком: домен, дальше через # причина")
    suppliers.add_argument("--by", required=True, help="кто внёс список")
    suppliers.add_argument(
        "--forever",
        action="store_true",
        help="текущие партнёры — без срока; без флага запись держит "
        "ADVERTISERS_SUPPLIER_TERM_MONTHS (12 месяцев): «размещались за 12 мес.»",
    )

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


def _ahrefs(args: argparse.Namespace, spent: UsageCollector) -> AhrefsClient | None:
    """Клиент для DR кандидатов — или `None`, и тогда сказано почему."""
    if args.no_dr:
        print("DR не спрашиваем (--no-dr): «DR > 80 — не пишем» не проверяется.")
        return None
    if not ahrefs_cfg.API_KEY:
        print("Ключа Ahrefs нет: «DR > 80 — не пишем» не проверяется.")
        return None
    return AhrefsClient(on_usage=spent)


async def cmd_advertisers(args: argparse.Namespace) -> int:
    """Пересчитать вердикты и показать кандидатов."""
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    spent = UsageCollector()
    client = _ahrefs(args, spent)
    ratings: Ratings | None = functools.partial(ahrefs_ratings, client) if client else None
    try:
        async with factory() as session:
            runs = await _runs(session, args.run)
            if not runs:
                print("Обходов в базе нет — сначала `outreach crawl <домен> --save`.")
                return EXIT_NOT_FOUND

            for run in runs:
                await judge_run(session, run.id, ratings=ratings)
            # Расход — в журнал той же транзакцией, что и вердикты: DR,
            # за который заплачено, без строки расхода не остаётся.
            for operation, cost in spent.drain():
                usage.record(session, operation=operation, units=cost.billable)
            await session.commit()

            rows = await _candidates(session, [r.id for r in runs], args.verdict)
            _print_summary(rows, runs)
            for row in rows[: args.limit]:
                _print_candidate(row)
            if len(rows) > args.limit:
                print(f"\n  … ещё {len(rows) - args.limit}; поднять потолок — `--limit`")
    finally:
        if client is not None:
            await client.aclose()
        await engine.dispose()
    return 0


async def _runs(session: AsyncSession, run_id: int | None) -> list[CrawlRunModel]:
    """Какие обходы считать: названный — или последний пригодный у каждого
    донора (`gate.latest_crawls`): кандидаты прежних обходов он заменяет."""
    if run_id is None:
        return await latest_crawls(session)
    query = select(CrawlRunModel).where(CrawlRunModel.id == run_id)
    return list((await session.execute(query)).scalars().all())


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
    """Записать решение человека. Оно сильнее вердикта скоринга.

    Тем же путём, что экран (`crawl/review.decide`): «не пишем» снимает
    рекламодателя домена и его письма, если других подходящих кандидатов нет.
    Команда — явное решение, поэтому прежнее она переписывает (`force`), как и раньше.
    """
    check_storage()
    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            found = await session.scalar(
                select(CandidateModel.id).where(
                    CandidateModel.crawl_run_id == args.run,
                    CandidateModel.target_root == args.domain.lower(),
                )
            )
            if found is None:
                print(
                    f"Кандидата {args.domain} в обходе {args.run} нет. "
                    "Список — `outreach advertisers --run N`.",
                )
                return EXIT_NOT_FOUND

            decided = await review.decide(
                session, found, confirmed=bool(args.yes), by=args.by, force=True
            )
            await session.commit()
            row = decided.candidate
            print(
                f"{row.target_root}: {'подтверждён' if row.confirmed else 'отклонён'} "
                f"({args.by}). Балл скоринга {row.points} остался как был — "
                "по нему считается, как часто он ошибается."
            )
            if decided.withdrawn.removed:
                print("Рекламодатель снят: других подходящих кандидатов у домена нет.")
            if decided.withdrawn.stopped:
                print(f"Снято писем по найденной ссылке: {decided.withdrawn.stopped}.")
            if decided.warning:
                print(decided.warning)
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


async def cmd_suppliers_import(args: argparse.Namespace) -> int:
    """Внести стоп-лист доноров-поставщиков — в стоп-лист экрана, причиной «поставщик».

    Их рекламодатели — чужие клиенты и свои же размещения. Запись та же, что
    заводит экран «Стоп-лист» (`crawl/supplier_import.py`): поставщик виден там,
    держит письма ему самому, его не обходят, его рекламодателей не переводят,
    и отбор прогона его не берёт. Строка без домена называется, а не теряется.
    """
    check_storage()
    if not args.path.exists():
        print(f"Файла {args.path} нет.")
        return EXIT_NOT_FOUND

    lines = args.path.read_text(encoding="utf-8").splitlines()
    parsed = [found for found in map(supplier_import.parse_line, lines) if found is not None]
    if not any(host for host, _ in parsed):
        print("В файле нет ни одного домена — список не тронут.")
        return EXIT_NOT_FOUND

    engine = create_async_engine(storage.DSN)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            report = await supplier_import.import_hosts(
                session, lines, by=args.by, forever=args.forever
            )
            await session.commit()
            total = len(await suppliers.supplier_hosts(session))
        _print_import(report, forever=args.forever, total=total)
    finally:
        await engine.dispose()
    return 0


#: Этапы писем словами отчёта импорта.
_STAGE_WORDS = {"donors": "Этап 1", "advertisers": "рекламодатели", "sales": "продажи"}


def _print_import(report: supplier_import.SupplierImport, *, forever: bool, total: int) -> None:
    term = (
        "бессрочно — текущие партнёры"
        if forever
        else f"на {suppliers_cfg.SUPPLIER_TERM_MONTHS} мес. — размещались"
    )
    print(
        f"В стоп-лист поставщиками: добавлено {report.added}, продлено {report.renewed}, "
        f"уже было {report.kept}. Срок: {term}."
    )
    if report.stopped:
        stages = ", ".join(
            f"{_STAGE_WORDS.get(stage, stage)} {count}"
            for stage, count in sorted(report.stopped_by_stage.items())
        )
        print(f"Снято писем им из очереди и добивок: {report.stopped} ({stages}).")
    if report.new_domains:
        print(
            f"Новых доменов: {len(report.new_domains)} — донора с таким доменом в базе нет, "
            f"сверьте написание: {', '.join(report.new_domains[:10])}"
        )
    for host, reason in report.elsewhere.items():
        longer = report.extended.get(host)
        tail = f"; запись продлена до срока поставщика — {longer}" if longer else ""
        print(
            f"  {host}: уже в стоп-листе («{reason}») — поставщиком отмечен в списке консоли{tail}"
        )
    for line in report.rejected:
        print(f"  не домен — пропущено: {line!r}")
    print(
        f"Поставщиков действует: {total}. Они видны на экране «Стоп-лист»; обход, перевод "
        "и отбор прогона отсеивают их сами."
    )
