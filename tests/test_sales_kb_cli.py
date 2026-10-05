"""Загрузка базы знаний из файла — срез 3.1: чтение, всё или ничего, повтор, журнал, консоль.

Файлы пишутся во временную папку теста — вне репозитория, как и положено файлу
с фактами компании; тексты записей выдуманы. Путь консоли — тот же, что в терминале:
доводы разбирает `build_parser`. База настоящая.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from backend.cli.main import _COMMANDS, EXIT_CANCELLED, build_parser, main
from backend.cli.sales import EXIT_BAD_INPUT, EXIT_OK, run_kb_load
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.sales import kb, kb_load
from backend.features.sales.kb_load import KbFileError, Problem
from backend.features.sales.models import SalesKbEntryModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parent.parent

BRIEF = {
    "kind": "brief",
    "language": "ru",
    "title": "Кто мы",
    "text": "Студия примеров для тестов.",
}
PRICE = {
    "kind": "price_policy",
    "language": "ru",
    "title": "Цена аудита",
    "text": "Цену называем после короткого созвона.",
    "tags": ["цена"],
}
CASE = {
    "kind": "case",
    "language": "en",
    "title": "Made-up shop",
    "text": "Doubled made-up traffic.",
    "active": False,
}
RECORDS = [BRIEF, PRICE, CASE]


def _file(tmp_path: Path, records: Any = RECORDS, *, raw: bytes | None = None) -> Path:
    path = tmp_path / "база.json"
    path.write_bytes(raw if raw is not None else json.dumps(records, ensure_ascii=False).encode())
    return path


async def _run(session: AsyncSession, path: Path, *extra: str) -> int:
    args = ["sales-kb-load", "--file", str(path), *extra]
    return await run_kb_load(session, build_parser().parse_args(args))


async def _rows(session: AsyncSession) -> list[tuple[str, str, bool, str | None]]:
    """Записи прямо из базы: объекты сессии перечитываются, а не берутся из памяти."""
    rows = await session.scalars(
        select(SalesKbEntryModel)
        .order_by(SalesKbEntryModel.id)
        .execution_options(populate_existing=True)
    )
    return [(row.title, row.text, row.active, row.updated_by) for row in rows]


async def _journal(session: AsyncSession) -> list[tuple[str | None, dict[str, Any] | None]]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.SALES_KB_CHANGED)
        .order_by(AuditLogModel.id)
    )
    return [(row.target, row.details) for row in rows]


# --- чтение файла ---------------------------------------------------------------------


def test_file_is_read_into_entries_brought_to_one_form(tmp_path: Path) -> None:
    entries, problems = kb_load.read(_file(tmp_path))

    assert problems == []
    assert entries == [kb.entry(**BRIEF), kb.entry(**PRICE), kb.entry(**CASE)]


def test_every_bad_record_is_named_by_its_number(tmp_path: Path) -> None:
    records = [
        BRIEF,
        "просто строка",
        BRIEF | {"titel": "опечатка"},
        {"kind": "cta", "language": "ru", "title": "Звать на созвон"},
        BRIEF | {"text": 7},
        PRICE | {"tags": "цена"},
        PRICE | {"active": "да"},
        PRICE | {"language": "Russian"},
        BRIEF | {"title": " Кто   мы "},
    ]

    entries, problems = kb_load.read(_file(tmp_path, records))

    assert entries == [kb.entry(**BRIEF)]
    assert problems == [
        Problem(
            2, None, 'не запись: ждём объект {"kind": …, "language": …, "title": …, "text": …}'
        ),
        Problem(3, "Кто мы", "лишние поля titel: ждём kind, language, title, text, tags, active"),
        Problem(4, "Звать на созвон", "нет полей text"),
        Problem(5, "Кто мы", "вид, язык, заголовок и текст — строки"),
        Problem(6, "Цена аудита", "теги — список строк"),
        Problem(7, "Цена аудита", "включена — true или false"),
        Problem(8, "Цена аудита", "язык «Russian» — не код языка: ждём en, ru, pt-br"),
        Problem(9, "Кто мы", "та же запись, что №1: вид, язык и заголовок совпадают"),
    ]


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        ("Кто мы".encode("cp1251"), "файл база.json не в UTF-8 (байт 0)"),
        (b'[{"kind": "brief",', "файл база.json — не JSON: Expecting property name"),
        (b'{"kind": "brief"}', "в файле база.json ждём непустой список записей"),
        (b"[]", "в файле база.json ждём непустой список записей"),
        (b"[" + b" " * (2 * 1024 * 1024) + b"]", "файл база.json больше 2 МБ — это не база знаний"),
    ],
)
def test_unreadable_file_is_refused_whole_in_words(tmp_path: Path, raw: bytes, words: str) -> None:
    with pytest.raises(KbFileError) as refused:
        kb_load.read(_file(tmp_path, raw=raw))

    assert str(refused.value).startswith(words)


def test_missing_file_is_refused_in_words(tmp_path: Path) -> None:
    with pytest.raises(KbFileError) as refused:
        kb_load.read(tmp_path / "нет.json")

    assert str(refused.value).startswith(f"файл {tmp_path / 'нет.json'} не открылся: No such file")


def test_a_file_inside_the_repository_is_not_read_at_all() -> None:
    """Файла нет и не создаётся: отказ — по месту, до чтения."""
    inside = ROOT / "delivery" / "база.json"

    with pytest.raises(KbFileError) as refused:
        kb_load.read(inside)

    assert str(refused.value) == (
        f"файл {inside} лежит в копии репозитория {ROOT} — факты компании уехали бы "
        "в публичную историю; положите файл вне репозитория"
    )
    assert not inside.exists()


# --- консоль: запись, повтор, отличия ------------------------------------------------------


async def test_first_load_writes_all_with_one_journal_line_and_names_both_versions(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = await kb.version(session)

    assert await _run(session, _file(tmp_path)) == EXIT_OK

    after = kb.version_of([kb.entry(**BRIEF), kb.entry(**PRICE)])  # выключенная не в счёт
    assert capsys.readouterr().out == (
        "База знаний из база.json: записей 3.\n"
        "  добавлено 3, без изменений 0, отличается 0 — не тронуто (перезаписать: --update)\n"
        f"Версия базы: {empty} → {after}.\n"
    )
    assert await _rows(session) == [
        ("Кто мы", BRIEF["text"], True, "консоль"),
        ("Цена аудита", PRICE["text"], True, "консоль"),
        ("Made-up shop", CASE["text"], False, "консоль"),
    ]
    assert await _journal(session) == [
        (
            "sales_kb",
            {
                "источник": "база.json",
                "добавлено": 3,
                "обновлено": 0,
                "без изменений": 0,
                "отличается, не тронуто": 0,
                "версия": {"было": empty, "стало": after},
            },
        )
    ]


async def test_repeat_does_not_double_and_writes_no_journal(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _file(tmp_path)
    await _run(session, path)
    capsys.readouterr()

    assert await _run(session, path) == EXIT_OK

    assert "добавлено 0, без изменений 3, отличается 0" in capsys.readouterr().out
    assert (len(await _rows(session)), len(await _journal(session))) == (3, 1)


async def test_differing_entry_is_left_alone_without_update_and_named(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Запись могли поправить на экране после первой загрузки: старый файл не
    откатывает правку молча — называет запись и ждёт `--update`."""
    await _run(session, _file(tmp_path))
    price = await session.scalar(
        select(SalesKbEntryModel).where(SalesKbEntryModel.title == "Цена аудита")
    )
    assert price is not None
    capsys.readouterr()
    edited = _file(tmp_path, [BRIEF, PRICE | {"text": "Цену называем сразу."}])

    assert await _run(session, edited) == EXIT_OK

    assert capsys.readouterr().out.splitlines()[1:4] == [
        "  добавлено 0, без изменений 1, отличается 1 — не тронуто (перезаписать: --update)",
        f"    №{price.id} «Цена аудита» (price_policy, ru)",
        "  в базе, но не в файле: 1 — не тронуты",
    ]
    assert [text for title, text, *_ in await _rows(session) if title == "Цена аудита"] == [
        PRICE["text"]
    ]
    assert len(await _journal(session)) == 1


async def test_update_overwrites_the_differing_entry_and_journals_it(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await _run(session, _file(tmp_path))
    before = await kb.version(session)

    assert (
        await _run(session, _file(tmp_path, [PRICE | {"text": "Цену называем сразу."}]), "--update")
        == EXIT_OK
    )

    assert [text for title, text, *_ in await _rows(session) if title == "Цена аудита"] == [
        "Цену называем сразу."
    ]
    _, details = (await _journal(session))[-1]
    assert details is not None
    assert (details["обновлено"], details["версия"]["было"]) == (1, before)
    assert details["версия"]["стало"] == await kb.version(session) != before
    assert "обновлено 1" in capsys.readouterr().out


async def test_dry_run_reports_and_writes_nothing(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(session, _file(tmp_path), "--dry-run") == EXIT_OK

    assert capsys.readouterr().out.endswith("Предпросмотр: в базу ничего не записано.\n")
    assert (await _rows(session), await _journal(session)) == ([], [])


async def test_file_with_a_bad_record_writes_nothing_and_names_the_record(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _file(tmp_path, [BRIEF, PRICE | {"kind": "pricing"}])

    assert await _run(session, path) == EXIT_BAD_INPUT

    assert capsys.readouterr().out == (
        "Файл не загружен: записей с ошибками 1 из 2 — в базе ничего не изменилось.\n"
        "  №2 «Цена аудита»: вида «pricing» нет; есть: brief, service, case, objection, "
        "price_policy, forbidden, cta\n"
    )
    assert await _rows(session) == []


async def test_unreadable_file_is_refused_by_the_console_in_words(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(session, tmp_path / "нет.json") == EXIT_BAD_INPUT

    assert capsys.readouterr().out.startswith("База знаний не прочитана: файл ")


def test_ctrl_c_says_the_knowledge_base_is_written_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Подсказка точки входа — своя у команды: общее умолчание про домены к ней не относится."""

    async def interrupted(_args: argparse.Namespace) -> int:
        raise KeyboardInterrupt

    monkeypatch.setitem(_COMMANDS, "sales-kb-load", interrupted)
    # Настройка журнала точки входа подменена: настоящая повесила бы обработчик
    # на поток вывода этого теста, и соседи по прогону получили бы его ошибки.
    monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)

    assert main(["sales-kb-load", "--file", "база.json"]) == EXIT_CANCELLED
    err = capsys.readouterr().err
    assert "База знаний пишется одной транзакцией: в базе ничего не осталось." in err
    assert "домены остались" not in err
