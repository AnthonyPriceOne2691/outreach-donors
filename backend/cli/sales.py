"""Команды модуля «Продажи».

    outreach sales-hypothesis-add --name "…" --description "…"
    outreach sales-import --hypothesis "…" (--file база.csv | --link …) [--dry-run]

Стартовые гипотезы заводятся здесь, данными в базе, а не миграцией:
описание гипотезы — коммерческий текст, а репозиторий публичный. Логика
заведения — `features/sales/hypotheses.py`, загрузки — `features/sales/intake.py`;
здесь только разбор и печать.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Awaitable, Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.config import storage
from backend.config.startup_checks import check_storage
from backend.features.sales import hypotheses, intake, sheet
from backend.features.sales.columns import LeadField, Mapping

EXIT_OK = 0
#: Источник не читается, сопоставление не годится — чинится в файле или в доводах.
EXIT_BAD_INPUT = 2
EXIT_TAKEN = 3
EXIT_BAD_NAME = 4
EXIT_NO_HYPOTHESIS = 5
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
