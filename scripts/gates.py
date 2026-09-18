"""Архитектурные гейты: правила, которые линтер проверить не умеет.

Все пороги здесь **жёсткие**, без снимка легаси. Это возможно потому, что
гейты разворачиваются рано: нарушений нет, и замораживать нечего. Снимок
понадобился бы, если бы правила вводили в проект с накопленным долгом —
тогда он тает по мере правок. Нам растапливать нечего, и это выигрыш:
жёсткий ноль сильнее любого ратчета.

Запуск: `python scripts/gates.py [файлы...]`. Без аргументов проверяет всё
дерево backend, tests и scripts.
"""

from __future__ import annotations

import ast
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

MAX_LINES_PROD = 500
MAX_LINES_TESTS = 1000

# Имена без темы: такой модуль собирает всё подряд и через полгода никто
# не знает, что в нём лежит.
GRAB_BAG_NAMES = frozenset({"utils.py", "helpers.py", "common.py", "misc.py", "shared.py"})

WEB_PACKAGES = frozenset({"fastapi", "starlette", "uvicorn"})

# Где веб-фреймворку место. Всё остальное — доменные модули и обвязка
# хранилища, они про веб знать не должны.
WEB_ALLOWED_PARTS = frozenset({"api", "web"})


@dataclass(frozen=True, slots=True)
class Violation:
    path: Path
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        where = self.path.relative_to(ROOT)
        return f"{where}:{self.line} [{self.rule}] {self.message}"


def _python_files(targets: Iterable[Path]) -> Iterator[Path]:
    for target in targets:
        if target.is_file() and target.suffix == ".py":
            yield target
        elif target.is_dir():
            for path in sorted(target.rglob("*.py")):
                if "migrations" in path.parts or "__pycache__" in path.parts:
                    continue
                yield path


def check_file_length(path: Path, source: str) -> Iterator[Violation]:
    """Длинный файл — это несколько тем в одном месте."""
    limit = MAX_LINES_TESTS if "tests" in path.parts else MAX_LINES_PROD
    lines = source.count("\n") + 1
    if lines > limit:
        yield Violation(path, lines, "file-length", f"{lines} строк при пределе {limit}")


def check_grab_bag(path: Path, _source: str) -> Iterator[Violation]:
    if path.name in GRAB_BAG_NAMES:
        yield Violation(
            path, 1, "grab-bag", f"имя {path.name} не называет тему — назовите модуль по смыслу"
        )


def check_silent_except(path: Path, source: str) -> Iterator[Violation]:
    """Проглоченное исключение — отложенная отладка по цене рабочего дня."""
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if not isinstance(node, ast.ExceptHandler):
            continue
        if _handler_speaks(node):
            continue
        yield Violation(
            path,
            node.lineno,
            "silent-except",
            "except без лога и без raise — ошибка исчезнет молча",
        )


def _handler_speaks(handler: ast.ExceptHandler) -> bool:
    """Обработчик либо сообщает о проблеме, либо пробрасывает её дальше."""
    for node in ast.walk(handler):
        if isinstance(node, ast.Raise):
            return True
        if isinstance(node, ast.Return | ast.Continue | ast.Break):
            # Ранний выход допустим только вместе с сообщением — его ищем ниже.
            continue
        if isinstance(node, ast.Call) and _is_reporting_call(node):
            return True
    return False


def _is_reporting_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr in {"debug", "info", "warning", "error", "exception", "critical"}
    return isinstance(func, ast.Name) and func.id == "print"


def check_config_access(path: Path, source: str) -> Iterator[Violation]:
    """Переменные окружения читает только config: иначе опечатка в имени
    прячется от типизатора и всплывает в проде.

    Тестовая оснастка исключена намеренно: выбор тестовой базы через
    окружение — это про запуск, а не про конфигурацию сервиса.
    """
    if "config" in path.parts or "tests" in path.parts:
        return
    for node in ast.walk(ast.parse(source, filename=str(path))):
        if not isinstance(node, ast.Attribute) or node.attr not in {"getenv", "environ"}:
            continue
        if isinstance(node.value, ast.Name) and node.value.id == "os":
            yield Violation(
                path,
                node.lineno,
                "config-access",
                "os.getenv вне config/ — добавьте поле в типизированный конфиг",
            )


def check_layers(path: Path, source: str) -> Iterator[Violation]:
    """Ядро не знает про веб. Проверяется тем, что доменные модули
    тестируются без сервера."""
    if WEB_ALLOWED_PARTS & set(path.parts) or "tests" in path.parts:
        return
    for node in ast.walk(ast.parse(source, filename=str(path))):
        names = _imported_roots(node)
        hit = names & WEB_PACKAGES
        if hit:
            yield Violation(
                path,
                node.lineno,
                "layers",
                f"{', '.join(sorted(hit))} в доменном модуле — веб живёт в api/",
            )


def _imported_roots(node: ast.AST) -> frozenset[str]:
    if isinstance(node, ast.Import):
        return frozenset(alias.name.split(".")[0] for alias in node.names)
    if isinstance(node, ast.ImportFrom) and node.module:
        return frozenset({node.module.split(".")[0]})
    return frozenset()


CHECKS = (
    check_file_length,
    check_grab_bag,
    check_silent_except,
    check_config_access,
    check_layers,
)


def run(targets: Iterable[Path]) -> list[Violation]:
    violations: list[Violation] = []
    for path in _python_files(targets):
        source = path.read_text(encoding="utf-8")
        for check in CHECKS:
            violations.extend(check(path, source))
    return violations


def main(argv: list[str]) -> int:
    targets = [Path(a).resolve() for a in argv] or [
        ROOT / "backend",
        ROOT / "tests",
        ROOT / "scripts",
    ]
    violations = run(targets)
    if not violations:
        checked = len(list(_python_files(targets)))
        print(f"Гейты пройдены: {checked} файлов, нарушений нет.")
        return 0

    for violation in violations:
        print(str(violation), file=sys.stderr)
    print(f"\nНарушений: {len(violations)}. Пороги жёсткие — снимка легаси нет.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
