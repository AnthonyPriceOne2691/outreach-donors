"""Команды модуля «Продажи».

    outreach sales-hypothesis-add --name "…" --description "…"
    outreach sales-import --hypothesis "…" (--file база.csv | --link …) [--dry-run]
    outreach sales-stoplist-add --file стоп.csv [--note "…"]
    outreach sales-clean [--hypothesis "…"]

Стартовые гипотезы заводятся здесь, данными в базе, а не миграцией:
описание гипотезы — коммерческий текст, а репозиторий публичный. Логика
заведения — `features/sales/hypotheses.py`, загрузки — `features/sales/intake.py`,
стоп-листа — `features/sales/stoplist.py`, очистки — `features/sales/cleaning.py`;
здесь только разбор и печать.

Очистка собирает проверяльщик адресов до первого лида: `live` без ключа —
отказ на старте (`ConfigError`, код 2 из `main`), и ни один лид не тронут.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.sales import cleaning, hypotheses, intake, sheet, stoplist
from backend.features.sales.cleaning import CleaningReport
from backend.features.sales.columns import LeadField, Mapping
from backend.features.sales.models import RejectionReason
from backend.features.sales.verifier import FIXTURE, build_verifier

#: Кто внёс строку из консоли: у команды нет пользователя, есть место.
AUTHOR = "консоль"

EXIT_OK = 0
#: Источник не читается, сопоставление не годится — чинится в файле или в доводах.
EXIT_BAD_INPUT = 2
EXIT_TAKEN = 3
EXIT_BAD_NAME = 4
EXIT_NO_HYPOTHESIS = 5
#: Платная часть очистки остановлена квотой или закрытой учёткой: повтор без
#: человека не поможет, непроверенные лиды остались `new`.
EXIT_VERIFIER_STOPPED = 7
#: Google не ответил: повтор поможет — как отсутствие сети у `contacts-file`.
EXIT_UNAVAILABLE = 8


async def _in_session(work: Callable[[AsyncSession], Awaitable[int]]) -> int:
    check_storage()
    engine = create_async_engine(storage.DSN)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await work(session)
    finally:
        await engine.dispose()


async def cmd_sales_hypothesis_add(args: argparse.Namespace) -> int:
    """Завести гипотезу продаж."""
    return await _in_session(lambda s: run_hypothesis_add(s, args.name, args.description))


async def cmd_sales_import(args: argparse.Namespace) -> int:
    """Загрузить базу лидов продаж из CSV или Google-таблицы."""
    return await _in_session(lambda session: run_import(session, args))


async def cmd_sales_stoplist_add(args: argparse.Namespace) -> int:
    """Пополнить ручной стоп-лист продаж доменами и адресами из файла."""
    return await _in_session(lambda session: run_stoplist_add(session, args))


async def cmd_sales_clean(args: argparse.Namespace) -> int:
    """Очистить лидов `new`: дубли, стоп-листы, годность, почта домена, проверяльщик."""
    return await _in_session(lambda session: run_clean(session, args))


async def run_clean(
    session: AsyncSession, args: argparse.Namespace, *, http: httpx.AsyncClient | None = None
) -> int:
    """Очистка на готовой сессии. Проверяльщик — по настройке, до первого лида."""
    hypothesis_id = None
    if args.hypothesis:
        hypothesis = await hypotheses.find(session, args.hypothesis)
        if hypothesis is None:
            print(f"Гипотезы «{args.hypothesis}» нет — заведите её: outreach sales-hypothesis-add")
            return EXIT_NO_HYPOTHESIS
        hypothesis_id = hypothesis.id
    if http is not None:
        return await _clean_with(session, http, hypothesis_id)
    async with httpx.AsyncClient() as own:
        return await _clean_with(session, own, hypothesis_id)


async def _clean_with(
    session: AsyncSession, http: httpx.AsyncClient, hypothesis_id: int | None
) -> int:
    verifier = build_verifier(http)
    made_up = " — вердикты выдуманные, для писем включите live" if verifier.name == FIXTURE else ""
    print(f"Проверяльщик адресов: {verifier.name}{made_up}")
    report = await cleaning.clean(session, verifier, hypothesis_id=hypothesis_id)
    _print_cleaning(report)
    return EXIT_VERIFIER_STOPPED if report.stopped else EXIT_OK


def _print_cleaning(report: CleaningReport) -> None:
    """Сводка прохода: каждый исход — числом, отказы — по причинам словами."""
    if not report.checked:
        print("Очистка продаж: лидов new нет — проверять нечего.")
        return
    print(
        f"Очистка продаж: проверено {report.checked}; готово {report.ready}; "
        f"отклонено {report.rejected_total}; не проверено {report.unverified}."
    )
    for reason in RejectionReason:
        if count := report.rejected.get(reason.value):
            print(f"  {cleaning.REASON_LABELS[reason]}: {count}")
    if report.mx_unknown:
        print(
            f"  DNS не ответил по доменам: {report.mx_unknown} — адреса прошли дальше непроверенными"
        )
    if report.verified:
        print(f"  вердиктов проверяльщика: {report.verified}, платных: {report.paid_units}")
    _print_unverified(report)


def _print_unverified(report: CleaningReport) -> None:
    if report.stopped:
        print(
            f"\nПлатная часть остановлена: {report.stopped}. Непроверенные лиды остались new — "
            "повторите очистку, когда причина снята."
        )
    elif report.unverified:
        print("\nНе проверенные лиды остались new: следующая очистка повторит их.")


async def run_stoplist_add(session: AsyncSession, args: argparse.Namespace) -> int:
    """Пополнение на готовой сессии. Повтор — не ошибка: «уже было» считается отдельно."""
    try:
        entries = await asyncio.to_thread(
            stoplist.read_entries, args.file, delimiter=args.delimiter
        )
    except stoplist.StoplistError as exc:
        print(f"Стоп-лист не прочитан: {exc}")
        return EXIT_BAD_INPUT
    if not entries.hosts and not entries.emails:
        print(f"Стоп-лист не прочитан: в файле {args.file.name} нет ни домена, ни адреса")
        _print_unreadable(entries.unreadable)
        return EXIT_BAD_INPUT
    loaded = await stoplist.add(session, entries, author=AUTHOR, note=args.note or args.file.name)
    await session.commit()
    print(
        f"Стоп-лист продаж: добавлено {loaded.added}, уже было {loaded.known} "
        f"(доменов {len(entries.hosts)}, адресов {len(entries.emails)})."
    )
    _print_unreadable(loaded.unreadable)
    return EXIT_OK


def _print_unreadable(rows: list[tuple[int, str]]) -> None:
    for line, cell in rows:
        print(f"  строка {line}: {stoplist.NOT_AN_ENTRY} — «{cell}»")


async def run_hypothesis_add(session: AsyncSession, name: str, description: str | None) -> int:
    """Заведение на готовой сессии — отдельно от команды ради теста."""
    try:
        hypothesis = await hypotheses.add(session, name, description)
    except hypotheses.BadNameError as exc:
        print(f"Гипотеза не заведена: {exc}")
        return EXIT_BAD_NAME
    except hypotheses.NameTakenError as exc:
        print(f"Гипотеза не заведена: {exc}")
        return EXIT_TAKEN
    await session.commit()
    print(f"Заведена гипотеза №{hypothesis.id} «{hypothesis.name}».")
    return EXIT_OK


async def run_import(session: AsyncSession, args: argparse.Namespace) -> int:
    """Загрузка на готовой сессии. Отчёт печатается и при `--dry-run`, и перед записью."""
    hypothesis = await hypotheses.find(session, args.hypothesis)
    if hypothesis is None:
        print(f"Гипотезы «{args.hypothesis}» нет — заведите её: outreach sales-hypothesis-add")
        return EXIT_NO_HYPOTHESIS
    try:
        found = await _read(args)
    except (intake.IntakeError, sheet.SheetError) as exc:
        print(f"База не прочитана: {exc}")
        return EXIT_BAD_INPUT
    except sheet.SheetUnavailableError as exc:
        print(f"База не прочитана: {exc}")
        return EXIT_UNAVAILABLE
    _print_preview(found)
    if found.needs_mapping:
        print("\nНет колонки почты — сопоставьте колонки: --map email=<номер или имя колонки>")
        return EXIT_BAD_INPUT
    if args.dry_run:
        print("\nПредпросмотр: в базу ничего не записано.")
        return EXIT_OK
    loaded = await intake.load(session, found, hypothesis.id, author_id=None)
    await session.commit()
    print(f"\nЗагружено лидов: {loaded} — гипотеза «{hypothesis.name}» (№{hypothesis.id}).")
    return EXIT_OK


async def _read(args: argparse.Namespace) -> intake.Preview:
    if args.link:
        async with sheet.client() as http:
            table = await intake.read_link(args.link, http, delimiter=args.delimiter)
    else:
        table = await asyncio.to_thread(intake.read_file, args.file, delimiter=args.delimiter)
    found = intake.preview(table, header=args.header)
    if not args.map:
        return found
    chosen = {**found.mapping, **_mapping(args.map, found.columns)}
    return intake.preview(table, chosen, header=found.header)


def _mapping(pairs: list[str], columns: list[str]) -> Mapping:
    """`email=3` или `email=Почта` → поле и колонка с нуля. Ошибка — словами."""
    titles = [title.lower() for title in columns]
    found: Mapping = {}
    for pair in pairs:
        name, _, column = (part.strip() for part in pair.partition("="))
        try:
            field = LeadField(name)
        except ValueError:
            raise intake.IntakeError(f"поля «{name}» нет; есть: {', '.join(LeadField)}") from None
        if column.isdigit():
            found[field] = int(column) - 1
        elif column.lower() in titles:
            found[field] = titles.index(column.lower())
        else:
            raise intake.IntakeError(f"колонки «{column}» нет; есть: {', '.join(columns)}")
    return found


def _print_preview(found: intake.Preview) -> None:
    fields = {index: field.value for field, index in found.mapping.items()}
    print(f"Источник: {found.source}; первая строка — {'заголовок' if found.header else 'данные'}")
    for index, title in enumerate(found.columns):
        print(f"  {index + 1}. {title} → {fields.get(index, 'без поля')}")
    print(f"\nСтрок: {found.rows}; лидов: {len(found.leads)}; отклонено: {found.rejected}")
    for problem in found.problems:
        kept = " (лид загружен)" if problem.loaded else ""
        print(f"  строка {problem.line}: {problem.reason} — «{problem.cell}»{kept}")


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    add = sub.add_parser("sales-hypothesis-add", help="завести гипотезу продаж: кому и зачем пишем")
    add.add_argument(
        "--name", required=True, help="короткое имя — по нему гипотезу выбирают при загрузке базы"
    )
    add.add_argument("--description", help="кому и зачем пишем, словами; хранится в базе")

    load = sub.add_parser(
        "sales-import", help="загрузить базу лидов продаж из CSV или Google-таблицы"
    )
    load.add_argument("--hypothesis", required=True, help="имя гипотезы, в которую ложатся лиды")
    source = load.add_mutually_exclusive_group(required=True)
    source.add_argument("--file", type=Path, help="CSV с базой")
    source.add_argument("--link", help="ссылка на Google-таблицу, открытую по ссылке")
    load.add_argument(
        "--map",
        action="append",
        default=[],
        metavar="ПОЛЕ=КОЛОНКА",
        help=f"сопоставить руками: email=3 или email=Почта; поля: {', '.join(LeadField)}",
    )
    load.add_argument(
        "--header",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="первая строка — заголовок (--no-header — данные); без флага угадывается",
    )
    load.add_argument("--delimiter", default=None, help="разделитель CSV, если угадан неверно")
    load.add_argument("--dry-run", action="store_true", help="только предпросмотр, без записи")

    stop = sub.add_parser(
        "sales-stoplist-add", help="пополнить стоп-лист продаж: домены и адреса, которым не пишем"
    )
    stop.add_argument(
        "--file", type=Path, required=True, help="CSV или список: в строке домен или адрес"
    )
    stop.add_argument("--note", help="откуда список, словами; по умолчанию — имя файла")
    stop.add_argument("--delimiter", default=None, help="разделитель CSV, если угадан неверно")

    clean = sub.add_parser(
        "sales-clean",
        help="очистить лидов new: дубли, стоп-листы, годность, почта домена, проверяльщик адресов",
    )
    clean.add_argument("--hypothesis", help="имя гипотезы; без него — лиды всех гипотез")
