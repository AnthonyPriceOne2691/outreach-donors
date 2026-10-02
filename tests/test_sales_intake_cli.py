"""Команда `outreach sales-import` — срез 1.3: отчёт по строкам, правка руками, коды выхода.

Путь тот же, что в консоли: доводы разбирает `build_parser`, база — настоящая.
Вывод сверяется целиком: человек читает его глазами, и каждая строка — обещание.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from backend.cli.main import build_parser
from backend.cli.sales import run_import
from backend.features.sales.models import SalesHypothesisModel, SalesLeadModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

#: Строки 2–7: в третьей страна словом, в седьмой нет адреса (A2).
BASE = "Почта;Имя;Страна;Заметка\n" + "".join(
    f"{'' if n == 5 else f'lead{n}@firm{n}.example.test'};Лид {n};{'Germany' if n == 1 else 'de'};x\n"
    for n in range(6)
)
PREVIEW = """Источник: база.csv; первая строка — заголовок
  1. Почта → email
  2. Имя → name
  3. Страна → country
  4. Заметка → без поля

Строк: 6; лидов: 5; отклонено: 1
  строка 3: страна не записана: ждём код — de, us — «Germany» (лид загружен)
  строка 7: нет адреса — «»
"""


@pytest.fixture(autouse=True)
async def hypothesis(session: AsyncSession) -> SalesHypothesisModel:
    found = SalesHypothesisModel(name="сайты EN")
    session.add(found)
    await session.flush()
    return found


def _source(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "база.csv"
    path.write_text(text, encoding="utf-8")
    return path


async def _run(session: AsyncSession, source: Path, *extra: str, name: str = "сайты  EN") -> int:
    args = ["sales-import", "--hypothesis", name, "--file", str(source), *extra]
    return await run_import(session, build_parser().parse_args(args))


async def _leads(session: AsyncSession) -> int:
    return int(await session.scalar(select(func.count()).select_from(SalesLeadModel)) or 0)


async def test_dry_run_prints_the_whole_report_and_writes_nothing(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # A2
    assert await _run(session, _source(tmp_path, BASE), "--dry-run") == 0
    assert capsys.readouterr().out == PREVIEW + "\nПредпросмотр: в базу ничего не записано.\n"
    assert await _leads(session) == 0


async def test_load_prints_the_report_first_and_then_what_was_written(
    session: AsyncSession,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    hypothesis: SalesHypothesisModel,
) -> None:
    assert await _run(session, _source(tmp_path, BASE)) == 0
    loaded = f"\nЗагружено лидов: 5 — гипотеза «сайты EN» (№{hypothesis.id}).\n"
    assert capsys.readouterr().out == PREVIEW + loaded
    assert await _leads(session) == 5


async def test_file_without_a_header_loads_after_mapping_by_hand(
    session: AsyncSession, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:  # A6
    source = _source(tmp_path, "ivan@acme.example.test;Иван\n")

    assert await _run(session, source) == 2
    assert capsys.readouterr().out == (
        "Источник: база.csv; первая строка — данные\n"
        "  1. колонка 1 → без поля\n  2. колонка 2 → без поля\n\n"
        "Строк: 1; лидов: 0; отклонено: 0\n\n"
        "Нет колонки почты — сопоставьте колонки: --map email=<номер или имя колонки>\n"
    )

    assert await _run(session, source, "--map", "email=1", "--map", "name=2") == 0
    assert "  1. колонка 1 → email\n  2. колонка 2 → name\n" in capsys.readouterr().out
    assert await session.scalar(select(SalesLeadModel.name)) == "Иван"


@pytest.mark.parametrize(
    ("extra", "name", "code", "line"),
    [
        (["--map", "email=Почта"], "сайты EN", 0, "Загружено лидов: 1 — гипотеза «сайты EN»"),
        (
            ["--no-header", "--map", "email=1"],
            "сайты EN",
            0,
            "  строка 1: не адрес почты — «Почта»\n",
        ),
        (
            [],
            "нет такой",
            5,
            "Гипотезы «нет такой» нет — заведите её: outreach sales-hypothesis-add\n",
        ),
        (
            ["--map", "почта=1"],
            "сайты EN",
            2,
            "База не прочитана: поля «почта» нет; есть: email, name",
        ),
        (
            ["--map", "email=Mail"],
            "сайты EN",
            2,
            "База не прочитана: колонки «Mail» нет; есть: Почта, Имя\n",
        ),
    ],
)
async def test_each_outcome_has_its_words_and_its_code(
    session: AsyncSession,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
    name: str,
    code: int,
    line: str,
) -> None:
    source = _source(tmp_path, "Почта;Имя\nivan@acme.example.test;Иван\n")
    assert await _run(session, source, *extra, name=name) == code
    assert line in capsys.readouterr().out
