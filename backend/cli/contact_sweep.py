"""Команда `contacts-file`: обойти домены из CSV и сложить контакты в CSV.

Отличие от `contacts` — в том, откуда берётся список и куда уезжает итог:
та команда идёт по очереди в базе и пишет исход донорам, эта не касается
базы вовсе. На входе чей-то экспорт, на выходе файл; инструмент остаётся
универсальным и ничего не знает о том, чей это список.

Порядок работы живёт в ядре (`features/contacts/file_sweep.py`), здесь —
доводы командной строки и печать отчёта.
"""

from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from pathlib import Path

from backend.features.contacts import file_sweep

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_BAD_INPUT = 2

#: Сколько неразобранных строк назвать поимённо. Остальные — числом и в лог:
#: файл, где мусора тысячи, не должен вытеснять с экрана сам отчёт.
UNREADABLE_SHOWN = 20


def add_parser(sub: argparse._SubParsersAction) -> None:  # type: ignore[type-arg]
    parser = sub.add_parser(
        "contacts-file",
        help="обойти домены из CSV и записать контакты в CSV, не касаясь базы",
    )
    parser.add_argument("source", type=Path, help="CSV со списком доменов")
    parser.add_argument(
        "--out", type=Path, default=None, help="куда писать итог (по умолчанию <файл>.contacts.csv)"
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="JSONL с пройденными доменами; повторный запуск продолжает с места обрыва",
    )
    parser.add_argument(
        "--column", default=None, help="колонка с доменом, если её имя нестандартное"
    )
    parser.add_argument("--delimiter", default=None, help="разделитель CSV, если угадан неверно")
    parser.add_argument(
        "--limit", type=int, default=None, help="взять только первые N доменов (проба)"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=file_sweep.CONCURRENCY,
        help=f"сколько доменов одновременно (по умолчанию {file_sweep.CONCURRENCY})",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="ступень браузера для сайтов, которые не открылись обычным запросом",
    )
    parser.add_argument(
        "--restart",
        action="store_true",
        help="начать заново: чекпойнт прежнего прогона не читается, а перезаписывается",
    )


def _print_report(report: file_sweep.SweepReport, out: Path, rows: int) -> None:
    print(f"\nДоменов в файле:       {report.total + report.skipped}")
    if report.skipped:
        print(f"Пропущено (пройдены):  {report.skipped}")
    print(f"Обойдено сейчас:       {report.walked}")
    print(f"С адресом:             {report.with_email}")
    print(f"Только с мессенджером: {report.with_handle}")
    counters = report.counters
    print(
        f"\nMX: проверено {counters.get('mx_checked', 0)}, "
        f"не принимают почту {counters.get('mx_stopped', 0)}, "
        f"DNS молчал {counters.get('mx_unknown', 0)}"
    )
    print(
        f"Страницы: вошло {counters.get('pages_entered', 0)}, "
        f"нашли адрес {counters.get('pages_found', 0)}, "
        f"форм без адреса {counters.get('form_only', 0)}"
    )
    print(f"Адресов отсеяно фильтром: {counters.get('rejected_emails', 0)}")
    print(f"\nИтог: {rows} строк в {out}")


class _BadInputError(Exception):
    """Со входом что-то не так, и это сказано словами, а не трассировкой."""


@dataclass(frozen=True, slots=True)
class _Plan:
    """Что и куда, посчитанное до начала обхода."""

    out: Path
    checkpoint: Path
    pending: list[str]
    skipped: int
    listed: file_sweep.DomainList


def _plan(args: argparse.Namespace) -> _Plan:
    """Разобрать доводы и файл. Синхронно — работа с диском не идёт в корутине."""
    source: Path = args.source
    if not source.exists():
        raise _BadInputError(f"Файла нет: {source}")

    out: Path = args.out or source.with_suffix(".contacts.csv")
    checkpoint: Path = args.checkpoint or source.with_suffix(".checkpoint.jsonl")
    if args.restart and checkpoint.exists():
        checkpoint.unlink()

    listed = file_sweep.read_list(source, column=args.column, delimiter=args.delimiter)
    hosts = listed.hosts
    if not hosts:
        _print_unreadable(listed)
        raise _BadInputError("В файле нет ни одного домена.")
    if args.limit is not None:
        hosts = hosts[: max(0, args.limit)]

    already = file_sweep.done_hosts(checkpoint)
    pending = [host for host in hosts if host not in already]
    # Сколько пропущено — не то же, что «сколько уже в чекпойнте»: тот мог
    # собраться по другому файлу, и число из него врало бы про этот.
    return _Plan(
        out=out,
        checkpoint=checkpoint,
        pending=pending,
        skipped=len(hosts) - len(pending),
        listed=listed,
    )


def _print_unreadable(listed: file_sweep.DomainList) -> None:
    """Строки, из которых домена не вышло, — номер и ячейка как есть."""
    if listed.unreadable:
        print(f"Не разобрано строк: {len(listed.unreadable)} — домена в ячейке не нашлось:")
        for line, cell in listed.unreadable[:UNREADABLE_SHOWN]:
            print(f"  строка {line}: {cell!r}")
        rest = listed.unreadable[UNREADABLE_SHOWN:]
        if rest:
            print(f"  …и ещё {len(rest)} — поимённо в логе.")
        for line, cell in rest:
            logger.warning("список: в строке %s домена нет (%r) — строка пропущена", line, cell)
    if listed.empty:
        print(f"Строк с пустой ячейкой домена: {listed.empty}")


def _finish(plan: _Plan, report: file_sweep.SweepReport) -> None:
    """Собрать CSV из чекпойнта и напечатать отчёт. Тоже синхронно."""
    rows = file_sweep.write_csv(file_sweep.rows_from_checkpoint(plan.checkpoint), plan.out)
    _print_report(report, plan.out, rows)


async def cmd_contacts_file(args: argparse.Namespace) -> int:
    """Обойти домены из файла. Повтор безопасен: пройденное не переспрашивается."""
    try:
        plan = _plan(args)
    except (_BadInputError, ValueError) as exc:
        print(str(exc))
        return EXIT_BAD_INPUT

    _print_unreadable(plan.listed)
    if plan.skipped:
        print(f"Чекпойнт: {plan.skipped} домен(ов) уже пройдены, продолжаем с остальных.")

    report = await file_sweep.sweep(
        plan.pending,
        checkpoint=plan.checkpoint,
        use_browser=args.browser,
        concurrency=args.concurrency,
        on_progress=lambda done, total: print(f"  пройдено {done} из {total}"),
    )
    report.skipped = plan.skipped
    _finish(plan, report)
    return EXIT_OK
