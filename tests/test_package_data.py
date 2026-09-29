"""Каждый не-питоновый файл пакета объявлен в `package-data`.

Образ ставит проект колесом (`uv sync --no-editable`, Dockerfile), а колесо
берёт не-питоновые файлы только по списку `[tool.setuptools.package-data]`
в `pyproject.toml`. 29.09.2026 списка не было, и колесо ушло в образ с одними
`.py`: консольная команда на проде (`outreach mail-test`, `letters-build`)
не находила ни одного шаблона письма и ни одного промпта ключей. Рабочая
копия этого не показывает — в ней файлы лежат на месте, — поэтому проверка
здесь сверяет сам список с файлами, а шаг CI «Шаблоны писем и промпты —
внутри образа» читает их из собранного образа.
"""

from __future__ import annotations

import tomllib
from fnmatch import fnmatch
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
#: Где лежат файлы, которые код читает во время работы. Миграции сюда
#: не входят: alembic читает их из рабочего каталога по `alembic.ini`,
#: а не из установленного пакета.
RUNTIME = ROOT / "backend" / "features"
SKIPPED_SUFFIXES = {".py", ".pyc"}


def _declared() -> dict[str, list[str]]:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return config["tool"]["setuptools"].get("package-data", {})


def _data_files() -> list[Path]:
    return sorted(
        path
        for path in RUNTIME.rglob("*")
        if path.is_file()
        and path.suffix not in SKIPPED_SUFFIXES
        and "__pycache__" not in path.parts
    )


def _covered(path: Path, declared: dict[str, list[str]]) -> bool:
    """Файл подпадает под объявление своего пакета."""
    for package, patterns in declared.items():
        base = ROOT.joinpath(*package.split("."))
        if base not in path.parents:
            continue
        inside = path.relative_to(base).as_posix()
        if any(fnmatch(inside, pattern) for pattern in patterns):
            return True
    return False


def test_there_are_data_files_to_check() -> None:
    """Проверка без файлов прошла бы всегда — она обязана что-то видеть."""
    names = {path.name for path in _data_files()}
    assert {"price_request.txt", "advertiser_offer.txt", "topics.md"} <= names


def test_every_data_file_is_declared() -> None:
    declared = _declared()
    missing = [
        str(path.relative_to(ROOT)) for path in _data_files() if not _covered(path, declared)
    ]
    assert not missing, (
        "Эти файлы не попадут в колесо, а значит, и в образ — консольная команда на "
        "проде их не найдёт. Допишите их в [tool.setuptools.package-data] "
        f"в pyproject.toml: {missing}"
    )


def test_every_declaration_matches_something() -> None:
    """Объявление, под которое не подпадает ни один файл, — опечатка в пути:
    файл, ради которого его писали, в колесо всё равно не попадёт."""
    files = _data_files()
    for package, patterns in _declared().items():
        base = ROOT.joinpath(*package.split("."))
        for pattern in patterns:
            matched = [
                path
                for path in files
                if base in path.parents and fnmatch(path.relative_to(base).as_posix(), pattern)
            ]
            assert matched, f"«{package}»: «{pattern}» не находит ни одного файла"
