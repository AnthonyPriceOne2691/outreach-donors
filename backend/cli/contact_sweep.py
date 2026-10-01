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
import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

from backend.config import contacts as cfg
from backend.features.contacts import file_sweep

logger = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_BAD_INPUT = 2
#: Прервано — тот же код, что у точки входа (`cli/main.py`).
EXIT_CANCELLED = 6
#: Сети нет: прогон остановлен, чекпойнт цел, попытки доменов не сгорели.
EXIT_NO_NETWORK = 8

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
        "--out",
        type=Path,
        default=None,
        help="куда писать итог (по умолчанию <файл>.contacts.csv рядом со списком)",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=None,
        help="JSONL с пройденными доменами (по умолчанию <файл>.checkpoint.jsonl); "
        "повторный запуск продолжает с места обрыва",
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
    parser.add_argument(
        "--retry-unreachable",
        action="store_true",
        help="ещё одна попытка доменам, на которых прогон сдался (статус unreachable); "
        "остальное пройденное не трогается",
    )


def _print_report(report: file_sweep.SweepReport, out: Path, rows: int) -> None:
    for note in report.notes:
        print(note)
    print(f"\nДоменов в файле:       {report.total + report.skipped}")
    if report.skipped:
        print(f"Пропущено (пройдены):  {report.skipped}")
    print(f"Обойдено сейчас:       {report.walked}")
    if report.retry:
        # Не «не нашли»: сайт не ответил, закрылся или обход оборван. Причина —
        # в колонке retry_reason, повтор — следующим запуском той же команды.
        print(f"Повторить:             {report.retry} — следующий запуск пройдёт их снова")
    if report.unreachable:
        print(
            f"Недоступны:            {report.unreachable} — попытки кончились, причина "
            "в retry_reason; ещё одна попытка — --retry-unreachable"
        )
    if report.failed:
        print(
            f"Упало с ошибкой:       {report.failed} — трассировка в логе, следующий запуск повторит"
        )
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
    #: Сколько из `pending` прошлый запуск оставил «повторить».
    retrying: int = 0
    #: Сколько из `pending` возвращено `--retry-unreachable`.
    reviving: int = 0
    #: Чекпойнт взят под прежним именем — сказать об этом.
    inherited: bool = False


def _plan(args: argparse.Namespace) -> _Plan:
    """Разобрать доводы и файл. Синхронно — работа с диском не идёт в корутине."""
    source: Path = args.source
    if not source.exists():
        raise _BadInputError(f"Файла нет: {source}")

    out, checkpoint, inherited = _paths(args)
    listed = file_sweep.read_list(source, column=args.column, delimiter=args.delimiter)
    hosts = listed.hosts
    if not hosts:
        _print_unreadable(listed)
        raise _BadInputError("В файле нет ни одного домена.")
    if args.limit is not None:
        hosts = hosts[: max(0, args.limit)]

    _check_paths(source, out, checkpoint)
    # Стирается чекпойнт, только когда вход уже прочитан: опечатка в имени
    # колонки не должна стоить прогресса прежнего прогона.
    if args.restart and checkpoint.exists():
        checkpoint.unlink()

    already = file_sweep.done_hosts(checkpoint)
    given_up = file_sweep.unreachable_hosts(checkpoint) if args.retry_unreachable else set()
    pending = [host for host in hosts if host not in already or host in given_up]
    # Сколько пропущено — не то же, что «сколько уже в чекпойнте»: тот мог
    # собраться по другому файлу, и число из него врало бы про этот.
    return _Plan(
        out=out,
        checkpoint=checkpoint,
        pending=pending,
        skipped=len(hosts) - len(pending),
        listed=listed,
        retrying=len(file_sweep.retry_hosts(checkpoint).intersection(pending)),
        reviving=len(given_up.intersection(pending)),
        inherited=inherited,
    )


def _paths(args: argparse.Namespace) -> tuple[Path, Path, bool]:
    """Итог и чекпойнт; третье — взят ли чекпойнт под прежним именем.

    Имя по умолчанию — от имени файла целиком, а не от основы: `list.csv`
    и `list.txt` — разные списки, и с `with_suffix` они делили бы один
    чекпойнт и один итог. Прогон, начатый до этой смены имён (01.10.2026),
    иначе пошёл бы с нуля молча: прежний файл по новому имени не находится.
    """
    source: Path = args.source
    out: Path = args.out or source.with_name(f"{source.name}.contacts.csv")
    if args.checkpoint:
        return out, args.checkpoint, False
    checkpoint = source.with_name(f"{source.name}.checkpoint.jsonl")
    legacy = source.with_suffix(".checkpoint.jsonl")
    if args.restart or checkpoint.exists() or not legacy.exists():
        return out, checkpoint, False
    return out, legacy, True


def _check_paths(source: Path, out: Path, checkpoint: Path) -> None:
    """Пути итога и чекпойнта — до обхода, а не через часы на первой записи.

    Пишется в них пробой, а не проверкой прав: права бывают в порядке,
    а запись — нет (файловая система только на чтение, это папка).
    """
    if len({source.resolve(), out.resolve(), checkpoint.resolve()}) < 3:
        raise _BadInputError(
            "Список, итог и чекпойнт должны быть разными файлами: "
            f"{source}, {out}, {checkpoint}. Иначе один затрёт другой."
        )
    for what, path in (("Итог (--out)", out), ("Чекпойнт (--checkpoint)", checkpoint)):
        if not path.parent.is_dir():
            raise _BadInputError(f"{what}: папки {path.parent} нет.")
        existed = path.exists()
        try:
            with path.open("ab"):
                pass
        except OSError as exc:
            raise _BadInputError(f"{what}: в {path} не записать — {exc.strerror or exc}.") from exc
        if not existed:
            path.unlink()


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


def _print_plan(plan: _Plan) -> None:
    """Что прочитано и что из прежнего прогона берётся — до первого запроса."""
    _print_unreadable(plan.listed)
    if plan.inherited:
        print(f"Чекпойнт прежнего имени: {plan.checkpoint} — продолжаем по нему.")
    if plan.skipped:
        print(f"Чекпойнт: {plan.skipped} домен(ов) уже пройдены, продолжаем с остальных.")
    if plan.retrying:
        print(f"Повторяем {plan.retrying} домен(ов), которые в прошлый раз не ответили.")
    if plan.reviving:
        print(f"Ещё одна попытка {plan.reviving} домен(ам), на которых прогон сдался.")


def _write_result(plan: _Plan) -> int:
    """Собрать CSV из чекпойнта. Синхронно — работа с диском не идёт в корутине.

    Строки — только доменов этого списка: чекпойнт бывает общим у разных
    списков, и итог одного не должен молча включать другой. Список берётся
    целиком, а не после `--limit`: проба на десяти доменах не должна
    затирать итог по уже пройденным тысячам.
    """
    return file_sweep.write_csv(file_sweep.rows_for(plan.listed.hosts, plan.checkpoint), plan.out)


async def cmd_contacts_file(args: argparse.Namespace) -> int:
    """Обойти домены из файла. Повтор безопасен: пройденное не переспрашивается."""
    try:
        plan = _plan(args)
    except (_BadInputError, ValueError) as exc:
        print(str(exc))
        return EXIT_BAD_INPUT

    _print_plan(plan)
    try:
        report = await file_sweep.sweep(
            plan.pending,
            checkpoint=plan.checkpoint,
            # Настройка включает браузер и здесь, как у поиска по базе:
            # одна переменная окружения не должна значить разное для двух команд.
            use_browser=args.browser or cfg.BROWSER_ENABLED,
            concurrency=args.concurrency,
            on_progress=lambda done, total: print(f"  пройдено {done} из {total}"),
        )
    except file_sweep.NetworkDownError as exc:
        print(
            f"Сеть недоступна: {exc}. Проверьте подключение (VPN, выход в интернет) и "
            "запустите ту же команду снова — пройденное сохранено, попытки доменов не сгорели.\n"
            f"Проверялись: {', '.join(file_sweep.NETWORK_PROBES)}. Если сеть есть, а их "
            "закрывает фильтр, — свои адреса через запятую в CONTACTS_NETWORK_PROBES "
            "или пустое значение, чтобы не проверять."
        )
        if exc.report is not None:
            exc.report.skipped = plan.skipped
            _print_report(exc.report, plan.out, _write_result(plan))
        return EXIT_NO_NETWORK
    except asyncio.CancelledError:
        # Ctrl-C: `asyncio.run` отменяет задачу команды. Пройденное уже
        # в чекпойнте, и итог по нему пишется и сейчас — прерванный прогон
        # на часы без файла стоил бы этих часов. Дальше отмена не идёт:
        # точка входа сказала бы «домены остались в базе», а базы эта
        # команда не касается.
        rows = _write_result(plan)
        print(
            f"\nПрервано. Пройденное — в чекпойнте {plan.checkpoint}, итог по нему "
            f"({rows} строк) — в {plan.out}. Повторный запуск продолжит с места обрыва."
        )
        return EXIT_CANCELLED
    report.skipped = plan.skipped
    _print_report(report, plan.out, _write_result(plan))
    return EXIT_OK
