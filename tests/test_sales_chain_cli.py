"""Загрузка цепочки писем из файла — срез 4.6, часть 1: чтение, всё или ничего, повтор, журнал.

Файлы пишутся во временную папку теста — вне репозитория, как и положено файлу с
текстами писем; тексты выдуманы. Путь консоли — тот же, что в терминале: доводы
разбирает `build_parser`. База настоящая.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import pytest
from backend.cli.main import _COMMANDS, EXIT_CANCELLED, build_parser, main
from backend.cli.sales import EXIT_BAD_INPUT, EXIT_NO_HYPOTHESIS, EXIT_OK, run_chain_load
from backend.features.core.domain import AuditAction
from backend.features.core.models.access import AuditLogModel
from backend.features.sales import chain, chain_load, hypotheses, sender
from backend.features.sales.chain_load import ChainFileError, Problem
from backend.features.sales.chain_text import step_template
from backend.features.sales.models import SalesChainTemplateModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

ROOT = Path(__file__).resolve().parent.parent

FIRST_BODY = (
    "[greeting] rewrite\nHello {{name}},\n\n"
    "[opening] rewrite\nThis is a made-up test opening about {{site}} for {{company}} only.\n\n"
    "[offer] fixed\nTest offer: nothing real is sold here."
)
FIRST = {"step": 1, "language": "en", "subject": "Test for {{company}}", "body": FIRST_BODY}
SECOND = {"step": 2, "language": "en", "body": "[reminder] fixed\nJust a made-up test reminder."}
THIRD = {
    "step": 3,
    "language": "en",
    "body": "[last] fixed\nLast made-up test note.",
    "active": False,
}
RECORDS = [FIRST, SECOND, THIRD]
NO_SENDER = sender.Sender()


def _file(tmp_path: Path, records: Any = RECORDS, *, raw: bytes | None = None) -> Path:
    path = tmp_path / "цепочка.json"
    path.write_bytes(raw if raw is not None else json.dumps(records, ensure_ascii=False).encode())
    return path


async def _run(session: AsyncSession, path: Path, *extra: str) -> int:
    args = ["sales-chain-load", "--file", str(path), *extra]
    return await run_chain_load(session, build_parser().parse_args(args))


async def _rows(session: AsyncSession) -> list[tuple[int | None, int, str, bool, str | None]]:
    """Шаблоны прямо из базы: объекты сессии перечитываются, а не берутся из памяти."""
    rows = await session.scalars(
        select(SalesChainTemplateModel)
        .order_by(SalesChainTemplateModel.id)
        .execution_options(populate_existing=True)
    )
    return [(r.hypothesis_id, r.step, r.language, r.active, r.updated_by) for r in rows]


async def _journal(session: AsyncSession) -> list[tuple[str | None, dict[str, Any] | None]]:
    rows = await session.scalars(
        select(AuditLogModel)
        .where(AuditLogModel.action == AuditAction.SALES_CHAIN_CHANGED)
        .order_by(AuditLogModel.id)
    )
    return [(row.target, row.details) for row in rows]


# --- чтение файла ---------------------------------------------------------------------------


def test_file_is_read_into_checked_templates(tmp_path: Path) -> None:
    templates, problems = chain_load.read(_file(tmp_path), NO_SENDER)

    assert problems == []
    assert templates == [step_template(**FIRST), step_template(**SECOND), step_template(**THIRD)]


def test_every_bad_template_is_named_by_its_number(tmp_path: Path) -> None:
    records = [
        FIRST,
        FIRST | {"subject": "Re: test"},
        "не шаблон",
        SECOND | {"subjekt": "x"},
        {"step": 3, "language": "en"},
        SECOND | {"step": True},
        SECOND | {"language": 7},
        FIRST | {"subject": 7},
        SECOND | {"active": "yes"},
        SECOND | {"body": "[reminder] fixed\nOur DR 45 test"},
        FIRST | {"language": "ru", "body": FIRST_BODY.replace("{{site}}", "{{host}}")},
    ]

    templates, problems = chain_load.read(_file(tmp_path, records), NO_SENDER)

    assert templates == [step_template(**FIRST)]
    assert [(p.number, p.where) for p in problems] == [
        (2, "шаг 1, en"),
        (3, None),
        (4, "шаг 2, en"),
        (5, "шаг 3, en"),
        (6, "шаг True, en"),
        (7, "шаг 2, 7"),
        (8, "шаг 1, en"),
        (9, "шаг 2, en"),
        (10, "шаг 2, en"),
        (11, "шаг 1, ru"),
    ]
    reasons = [p.reason for p in problems]
    assert "начинается с «Re:»" in reasons[0]
    assert reasons[1:8] == [
        'не шаблон: ждём объект {"step": 1, "language": "en", "subject": …, "body": …}',
        "лишние поля subjekt: ждём step, language, subject, body, active",
        "нет полей body",
        "шаг — число: 1 — первое письмо, 2 и 3 — добивки",
        "язык и текст — строки",
        "тема — строка; у добивок её нет вовсе",
        "включён — true или false",
    ]
    assert "метрики Ahrefs" in reasons[8]
    assert reasons[9] == "подстановки {{host}} нет; есть только {{name}}, {{company}}, {{site}}"


def test_the_same_step_and_language_twice_is_named(tmp_path: Path) -> None:
    _, problems = chain_load.read(_file(tmp_path, [FIRST, SECOND, FIRST]), NO_SENDER)

    assert problems == [Problem(3, "шаг 1, en", "тот же шаг и язык, что у №1: в наборе он один")]


def test_the_sender_signature_inside_a_template_is_named(tmp_path: Path) -> None:
    signed = sender.Sender(dict.fromkeys(sender.FIELDS) | {"signature": "Iva Testova"})
    records = [FIRST | {"body": FIRST_BODY + "\nIva Testova"}]

    _, problems = chain_load.read(_file(tmp_path, records), signed)

    assert [p.reason for p in problems] == [
        "в тексте — подпись из настроек отправителя: сборка допишет это сама, и в письме "
        "оно встало бы дважды; уберите из шаблона"
    ]


@pytest.mark.parametrize(
    ("raw", "words"),
    [
        ("Тест".encode("cp1251"), "файл цепочка.json не в UTF-8 (байт 0)"),
        (b'[{"step": 1,', "файл цепочка.json — не JSON: Expecting property name"),
        (b'{"step": 1}', "в файле цепочка.json ждём непустой список шаблонов"),
        (b"[]", "в файле цепочка.json ждём непустой список шаблонов"),
        (
            b"[" + b" " * (1024 * 1024) + b"]",
            "файл цепочка.json больше 1 МБ — это не цепочка писем",
        ),
    ],
)
def test_unreadable_file_is_refused_whole_in_words(tmp_path: Path, raw: bytes, words: str) -> None:
    with pytest.raises(ChainFileError) as refused:
        chain_load.read(_file(tmp_path, raw=raw), NO_SENDER)

    assert str(refused.value).startswith(words)


def test_a_file_inside_the_repository_is_not_read_at_all() -> None:
    """Файла нет и не создаётся: отказ — по месту, до чтения."""
    inside = ROOT / "delivery" / "цепочка.json"

    with pytest.raises(ChainFileError) as refused:
        chain_load.read(inside, NO_SENDER)

    assert str(refused.value) == (
        f"файл {inside} лежит в копии репозитория {ROOT} — тексты писем уехали бы "
        "в публичную историю; положите файл вне репозитория"
    )
    assert not inside.exists()


# --- консоль: запись, повтор, отличия --------------------------------------------------------


async def test_first_load_writes_the_common_set_with_one_journal_line(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    empty = chain.version_of([])

    assert await _run(session, _file(tmp_path)) == EXIT_OK

    after = chain.version_of([step_template(**FIRST), step_template(**SECOND)])  # третий выключен
    assert capsys.readouterr().out == (
        "Цепочка писем из цепочка.json: шаблонов 3, общий набор.\n"
        "  добавлено 3, без изменений 0, отличается 0 — не тронуто (перезаписать: --update)\n"
        f"  цепочка ru: версия {empty} → {empty}\n"
        f"  цепочка en: версия {empty} → {after}\n"
    )
    assert await _rows(session) == [
        (None, 1, "en", True, "консоль"),
        (None, 2, "en", True, "консоль"),
        (None, 3, "en", False, "консоль"),
    ]
    assert await _journal(session) == [
        (
            "sales_chain",
            {
                "источник": "цепочка.json",
                "набор": "общий набор",
                "добавлено": 3,
                "обновлено": 0,
                "без изменений": 0,
                "отличается, не тронуто": 0,
                "версия": {
                    "ru": {"было": empty, "стало": empty},
                    "en": {"было": empty, "стало": after},
                },
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


async def test_differing_template_is_left_alone_without_update_and_named(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await _run(session, _file(tmp_path))
    edited = [FIRST | {"subject": "Edited test subject"}, SECOND]
    capsys.readouterr()

    assert await _run(session, _file(tmp_path, edited)) == EXIT_OK

    (row_id,) = [r.id for r in await chain.rows(session, None) if r.step == 1]
    out = capsys.readouterr().out
    assert "отличается 1 — не тронуто (перезаписать: --update)" in out
    assert f"    №{row_id}: первое письмо, en\n" in out
    assert "  в наборе, но не в файле: 1 — не тронуты\n" in out
    assert (await chain.rows(session, None))[0].subject == "Test for {{company}}"


async def test_update_overwrites_the_differing_template_and_journals_it(
    session: AsyncSession, tmp_path: Path
) -> None:
    await _run(session, _file(tmp_path))

    edited = [FIRST | {"subject": "Edited test subject"}]
    assert await _run(session, _file(tmp_path, edited), "--update") == EXIT_OK

    first_row = (await chain.rows(session, None))[0]
    assert (first_row.subject, first_row.updated_by) == ("Edited test subject", "консоль")
    (_, details) = (await _journal(session))[-1]
    assert details is not None
    assert (details["обновлено"], details["без изменений"]) == (1, 0)


async def test_dry_run_reports_and_writes_nothing(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(session, _file(tmp_path), "--dry-run") == EXIT_OK

    assert capsys.readouterr().out.endswith("Предпросмотр: в базу ничего не записано.\n")
    assert (await _rows(session), await _journal(session)) == ([], [])


async def test_hypothesis_set_goes_to_the_named_hypothesis(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    owner = await hypotheses.add(session, "Тестовая гипотеза", None)

    assert await _run(session, _file(tmp_path), "--hypothesis", " Тестовая  гипотеза ") == EXIT_OK

    assert f"шаблонов 3, набор гипотезы №{owner.id}." in capsys.readouterr().out
    assert {row[0] for row in await _rows(session)} == {owner.id}
    found = await chain.resolve(session, hypothesis_id=owner.id, language="en")
    assert (found.hypothesis_id, sorted(found.steps)) == (owner.id, [1, 2])


async def test_unknown_hypothesis_writes_nothing(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(session, _file(tmp_path), "--hypothesis", "Нет такой") == EXIT_NO_HYPOTHESIS

    assert capsys.readouterr().out == (
        "Гипотезы «Нет такой» нет — её набору некуда лечь: заведите гипотезу\n"
    )
    assert await _rows(session) == []


async def test_file_with_a_bad_template_writes_nothing_and_names_it(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = _file(tmp_path, [FIRST, SECOND | {"subject": "Test"}])

    assert await _run(session, path) == EXIT_BAD_INPUT

    assert capsys.readouterr().out == (
        "Файл не загружен: шаблонов с ошибками 1 из 2 — в базе ничего не изменилось.\n"
        "  №2 (шаг 2, en): у добивки темы нет — она уходит в той же переписке, "
        "тему даёт первое письмо\n"
    )
    assert await _rows(session) == []


async def test_template_with_the_sender_signature_is_refused_by_the_console(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    await sender.save(session, {"signature": "Iva Testova"}, author="тест", author_id=None)
    path = _file(tmp_path, [FIRST | {"body": FIRST_BODY + "\nIva Testova"}])

    assert await _run(session, path) == EXIT_BAD_INPUT

    assert "подпись из настроек отправителя" in capsys.readouterr().out
    assert await _rows(session) == []


async def test_unreadable_file_is_refused_by_the_console_in_words(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert await _run(session, tmp_path / "нет.json") == EXIT_BAD_INPUT

    assert capsys.readouterr().out.startswith(
        f"Цепочка писем не прочитана: файл {tmp_path / 'нет.json'} не открылся"
    )


def test_ctrl_c_says_the_chain_is_written_in_one_transaction(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Подсказка точки входа — своя у команды: общее умолчание про домены к ней не относится."""

    async def interrupted(_args: argparse.Namespace) -> int:
        raise KeyboardInterrupt

    monkeypatch.setitem(_COMMANDS, "sales-chain-load", interrupted)
    # Настройка журнала точки входа подменена: настоящая повесила бы обработчик
    # на поток вывода этого теста, и соседи по прогону получили бы его ошибки.
    monkeypatch.setattr("backend.cli.main.setup_logging", lambda: None)

    assert main(["sales-chain-load", "--file", "цепочка.json"]) == EXIT_CANCELLED
    err = capsys.readouterr().err
    assert "Цепочка писем пишется одной транзакцией: в базе ничего не осталось." in err
    assert "домены остались" not in err
