"""Ручной стоп-лист продаж — срез 1.4: файл, запись с повтором, журнал, консоль, схема.

Домены и адреса выдуманы, на `*.example.test`. База настоящая: своя таблица
`sales_stoplist` с проверкой «домен или адрес, ровно одно», журнал — общий.
Путь консоли — тот же, что в терминале: доводы разбирает `build_parser`.

Утверждения точные: испорченное правило чтения или записи краснеет.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.cli.main import build_parser
from backend.cli.sales import EXIT_BAD_INPUT, EXIT_OK, run_stoplist_add
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.sales import stoplist
from backend.features.sales.models import SalesStoplistModel
from backend.features.sales.stoplist import Entries, Loaded
from sqlalchemy import Connection, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parent.parent
MIGRATION = ROOT / "backend/migrations/versions/95ee6522e0de_sales_cleaning_and_stoplist.py"
#: Что миграция добавляет: таблица, колонки лида, индекс экрана.
SCHEMA = frozenset(
    {
        "sales_stoplist",
        "rejection_reason",
        "cleaning_note",
        "verification_status",
        "verification_score",
        "verified_at",
        "idx_sales_leads_status_reason",
    }
)

#: Заголовок, домены как попало, адрес с пробелами, повтор, мусор, домен не в первой ячейке.
FILE = (
    "Домен,Примечание\n"
    "acme.example.test,клиент\n"
    "https://www.Beta.example.test/contacts,\n"
    "  Boss@Gamma.example.test ,партнёр\n"
    "acme.example.test,повтор\n"
    "не домен,мусор\n"
    ",delta.example.test\n"
)
HOSTS = ["acme.example.test", "beta.example.test", "delta.example.test"]
EMAILS = ["boss@gamma.example.test"]
UNREADABLE = [(6, "не домен")]
ENTRIES = Entries(HOSTS, EMAILS, UNREADABLE)


def _file(tmp_path: Path, text: str = FILE, *, encoding: str = "utf-8") -> Path:
    path = tmp_path / "стоп.csv"
    path.write_bytes(text.encode(encoding))
    return path


async def _stored(
    session: AsyncSession,
) -> list[tuple[str | None, str | None, str | None, str | None]]:
    rows = await session.execute(
        select(
            SalesStoplistModel.host,
            SalesStoplistModel.email,
            SalesStoplistModel.created_by,
            SalesStoplistModel.note,
        ).order_by(SalesStoplistModel.id)
    )
    return [tuple(row) for row in rows.tuples()]  # type: ignore[misc]


async def _journal(
    session: AsyncSession,
) -> list[tuple[int | None, str | None, dict[str, object] | None]]:
    rows = await session.scalars(
        select(AuditLogModel).where(AuditLogModel.action == AuditAction.SUPPRESSION_ADDED)
    )
    return [(row.user_id, row.target, row.details) for row in rows]


# --- чтение файла -------------------------------------------------------------------


def test_file_with_a_header_junk_and_repeats_is_read_into_hosts_and_addresses(
    tmp_path: Path,
) -> None:
    # A8 — пример спеки
    assert stoplist.read_entries(_file(tmp_path)) == ENTRIES


def test_first_line_is_data_when_it_is_a_domain_and_a_header_otherwise(tmp_path: Path) -> None:
    # A8 — пример спеки
    """Как у общего `read_list`: строка с доменом — данные; первая строка без
    домена и адреса — заголовок, а не замечание. Со второй строки мусор назван."""
    data_first = stoplist.read_entries(_file(tmp_path, "acme.example.test\nмусор\n"))
    assert data_first == Entries(["acme.example.test"], [], [(2, "мусор")])

    header_first = stoplist.read_entries(_file(tmp_path, "Кому не пишем\nмусор\n\n;\n"))
    assert header_first == Entries([], [], [(2, "мусор"), (4, ";")])  # пустая строка не в счёт


@pytest.mark.parametrize(
    ("text", "encoding", "words"),
    [
        ("Домен\nacme.example.test\n", "cp1251", "файл стоп.csv не в UTF-8 (строка 1)"),
        (
            "Домен\n" + "я" * 140_000 + "\n",
            "utf-8",
            "файл стоп.csv не читается как CSV: field larger",
        ),
    ],
)
def test_unreadable_file_is_refused_in_words(
    tmp_path: Path, text: str, encoding: str, words: str
) -> None:
    # A8 — пример спеки
    with pytest.raises(stoplist.StoplistError) as refused:
        stoplist.read_entries(_file(tmp_path, text, encoding=encoding))
    assert str(refused.value).startswith(words)


def test_missing_file_is_refused_in_words(tmp_path: Path) -> None:
    # A8 — пример спеки
    with pytest.raises(stoplist.StoplistError) as refused:
        stoplist.read_entries(tmp_path / "нет.csv")
    assert str(refused.value).startswith(f"файл {tmp_path / 'нет.csv'} не открылся: No such file")


# --- запись --------------------------------------------------------------------------


async def test_entries_are_written_once_with_author_note_and_one_journal_line(
    session: AsyncSession,
) -> None:
    # A8 — пример спеки
    loaded = await stoplist.add(session, ENTRIES, author="тест", note="клиенты")

    assert loaded == Loaded(added=4, known=0, unreadable=UNREADABLE)
    assert await _stored(session) == [
        ("acme.example.test", None, "тест", "клиенты"),
        ("beta.example.test", None, "тест", "клиенты"),
        ("delta.example.test", None, "тест", "клиенты"),
        (None, "boss@gamma.example.test", "тест", "клиенты"),
    ]
    assert await _journal(session) == [
        (
            None,
            "sales_stoplist",
            {"источник": "клиенты", "доменов": 3, "адресов": 1, "добавлено": 4},
        )
    ]


async def test_repeat_is_counted_as_known_and_writes_nothing_not_even_the_journal(
    session: AsyncSession,
) -> None:
    # A8 — пример спеки
    await stoplist.add(session, ENTRIES, author="тест", note=None)

    again = await stoplist.add(session, ENTRIES, author="тест", note=None)
    partly = await stoplist.add(
        session,
        Entries(["acme.example.test", "new.example.test"], [], []),
        author="тест",
        note=None,
    )

    assert (again, partly) == (Loaded(0, 4, UNREADABLE), Loaded(1, 1, []))
    assert [host for host, *_ in await _stored(session)] == [*HOSTS, None, "new.example.test"]
    sources = [details["источник"] for _, _, details in await _journal(session) if details]
    assert sources == ["тест", "тест"]  # без заметки источник — автор; повтор строки не даёт


@pytest.mark.parametrize(
    "row",
    [
        SalesStoplistModel(host="acme.example.test", email="ivan@acme.example.test"),
        SalesStoplistModel(),
    ],
)
async def test_a_row_is_a_domain_or_an_address_and_exactly_one(
    session: AsyncSession, row: SalesStoplistModel
) -> None:
    # A8 — пример спеки
    with pytest.raises(IntegrityError, match="ck_sales_stoplist_one_key"):
        async with session.begin_nested():
            session.add(row)
            await session.flush()
    assert await _stored(session) == []


# --- консоль -------------------------------------------------------------------------


async def _run(session: AsyncSession, path: Path, *extra: str) -> int:
    args = ["sales-stoplist-add", "--file", str(path), *extra]
    return await run_stoplist_add(session, build_parser().parse_args(args))


async def test_console_reports_added_known_and_unreadable_lines(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A8 — пример спеки
    path = _file(tmp_path)

    assert await _run(session, path, "--note", "клиенты") == EXIT_OK
    assert capsys.readouterr().out == (
        "Стоп-лист продаж: добавлено 4, уже было 0 (доменов 3, адресов 1).\n"
        "  строка 6: не домен и не адрес — «не домен»\n"
    )

    assert await _run(session, path) == EXIT_OK
    assert capsys.readouterr().out.startswith("Стоп-лист продаж: добавлено 0, уже было 4 ")
    assert {note for *_, note in await _stored(session)} == {"клиенты"}


async def test_console_takes_the_file_name_as_the_note_by_default(
    session: AsyncSession, tmp_path: Path
) -> None:
    # A8 — пример спеки
    assert await _run(session, _file(tmp_path, "acme.example.test\n")) == EXIT_OK
    assert await _stored(session) == [("acme.example.test", None, "консоль", "стоп.csv")]


async def test_console_refuses_a_file_without_a_single_entry_and_names_the_lines(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A8 — пример спеки
    assert await _run(session, _file(tmp_path, "Домен\nмусор\n")) == EXIT_BAD_INPUT
    assert capsys.readouterr().out == (
        "Стоп-лист не прочитан: в файле стоп.csv нет ни домена, ни адреса\n"
        "  строка 2: не домен и не адрес — «мусор»\n"
    )
    assert await _stored(session) == []


async def test_console_refuses_a_missing_file_in_words(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # A8 — пример спеки
    assert await _run(session, tmp_path / "нет.csv") == EXIT_BAD_INPUT
    assert capsys.readouterr().out.startswith(
        f"Стоп-лист не прочитан: файл {tmp_path / 'нет.csv'} не открылся: No such file"
    )


# --- схема -----------------------------------------------------------------------------


def _migration() -> ModuleType:
    spec = importlib.util.spec_from_file_location("sales_cleaning_migration", MIGRATION)
    assert spec is not None, MIGRATION
    assert spec.loader is not None, MIGRATION
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _present(connection: Connection) -> set[str]:
    """Что из добавленного миграцией есть в базе сейчас."""
    tables = connection.execute(
        text("SELECT tablename FROM pg_tables WHERE tablename = 'sales_stoplist'")
    )
    columns = connection.execute(
        text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'sales_leads' AND column_name = ANY(:names)"
        ),
        {"names": sorted(SCHEMA)},
    )
    indexes = connection.execute(
        text("SELECT indexname FROM pg_indexes WHERE indexname = 'idx_sales_leads_status_reason'")
    )
    return set(tables.scalars()) | set(columns.scalars()) | set(indexes.scalars())


def _down_and_up(connection: Connection) -> tuple[set[str], set[str]]:
    migration = _migration()
    with Operations.context(MigrationContext.configure(connection)):
        migration.downgrade()
        after_downgrade = _present(connection)
        migration.upgrade()
    return after_downgrade, _present(connection)


async def test_migration_adds_the_table_columns_and_index_and_takes_them_back(
    session: AsyncSession,
) -> None:
    """Схему сьюта поднял `alembic upgrade head`; здесь цикл откат → подъём на
    соединении теста (DDL в Postgres транзакционный — откат теста вернёт схему)."""
    connection = await session.connection()
    assert await connection.run_sync(_present) == SCHEMA

    after_downgrade, after_upgrade = await connection.run_sync(_down_and_up)
    assert (after_downgrade, after_upgrade) == (set(), SCHEMA)
