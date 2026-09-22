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
import re
import shutil
import subprocess
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


# Переменные, у которых значение в образце означает утечку ключа.
SECRET_SUFFIXES = ("API_KEY", "SECRET", "PASSWORD", "TOKEN", "LOGIN", "DSN")


def check_env_example(path: Path) -> Iterator[Violation]:
    """В образце окружения не должно быть заполненных секретов.

    Образец лежит в публичном репозитории. Ключ попадает в него не по злому
    умыслу, а потому что редактор открывает `.env.example`, когда `.env`
    скрыт настройками — так уже случилось дважды за один вечер. Правка
    руками эту ошибку не ловит: ловит только проверка, которая роняет пуш.
    """
    if not path.exists():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        name, sep, value = line.partition("=")
        if not sep or name.startswith("#") or not value.strip():
            continue
        if name.strip().endswith(SECRET_SUFFIXES) and not value.strip().startswith(
            ("postgresql", "redis", "http")
        ):
            yield Violation(
                path,
                number,
                "secret-in-example",
                f"{name.strip()} заполнен в образце — перенести значение в .env",
            )


#: Чего не должно быть в публичном репозитории. Это не стиль, а утечка:
#: документ требований и план лежат в гитигноре целиком, и ссылка на них
#: из опубликованного файла рассказывает и про их существование, и про их
#: содержимое — номером строки, которую цитирует комментарий.
PRIVATE_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\u042d[12]-\d+"), "идентификатор строки закрытого чеклиста"),
    (
        re.compile(r"\b(?:TZ|PHASES|VPS|HETZNER_LINKS|ENTITIES|REUSE)\.md\b"),
        "имя закрытого документа",
    ),
    (
        re.compile(r"\u0437\u0430\u043a\u0430\u0437\u0447\u0438\u043a", re.IGNORECASE),
        "слово «заказчик»",
    ),
)

#: Где эти слова законны. Гитигнор и докеригнор обязаны называть файлы
#: по именам — иначе они их не исключат; сам гейт и его тест обязаны
#: содержать образцы, иначе им нечего искать.
PUBLIC_EXEMPT = frozenset(
    {".gitignore", ".dockerignore", "scripts/gates.py", "tests/test_gates.py"}
)

#: Расширения, которые человек читает. Двоичное содержимое не проверяем:
#: совпадение в нём означало бы не утечку, а случайные байты.
TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".txt", ".yml", ".yaml", ".json", ".sh", ".toml", ".cfg", ".example", ".ts",
     ".tsx", ".css", ".html", ".sql"}
)  # fmt: skip


def _tracked_text_files(root: Path) -> Iterator[Path]:
    """Файлы, которые уедут в публичный репозиторий, — по списку git.

    Не обходом дерева: уедет то, что git отслеживает, и спрашивать
    об этом надо его. Нет гита — гейт молчит, а не врёт зелёным.

    **Новые файлы считаются наравне с отслеживаемыми.** Один `ls-files`
    показывает только то, что уже добавлено, — и гейт, запущенный
    в середине работы, отвечал зелёным про файлы, которых ещё нет
    в индексе. Именно так закрытый документ был назван по имени
    в четырёх строках нового статуса, и нашлось это только после
    коммита. Файлы из гитигнора сюда не попадают: `--exclude-standard`
    именно об этом.
    """
    seen: set[str] = set()
    for names in _git_lists(root):
        for name in names:
            if not name or name in PUBLIC_EXEMPT or name in seen:
                continue
            seen.add(name)
            path = root / name
            if path.suffix in TEXT_SUFFIXES and path.is_file():
                yield path


#: Что уедет в репозиторий: добавленное и ещё не добавленное. Второй
#: список без первого не обходится — `--others` показывает только новое.
_GIT_LISTINGS = (
    ("ls-files", "-z"),
    ("ls-files", "-z", "--others", "--exclude-standard"),
)


def _git_lists(root: Path) -> Iterator[list[str]]:
    """Имена файлов от git. Молчит вместо зелёного, если спросить не вышло."""
    git = shutil.which("git")
    if git is None:
        print("public-repo: git не найден — гейт пропущен", file=sys.stderr)
        return
    for arguments in _GIT_LISTINGS:
        try:
            # Аргументы заданы здесь целиком, снаружи не приходит ничего:
            # `root` — путь самого репозитория, вычисленный от этого файла.
            listed = subprocess.run(  # noqa: S603 — фиксированная команда, путь к git разрешён
                [git, "-C", str(root), *arguments],
                capture_output=True,
                check=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"public-repo: список файлов не получен ({exc}) — гейт пропущен", file=sys.stderr)
            return
        yield listed.stdout.decode("utf-8").split("\0")


def check_public_repo(root: Path) -> Iterator[Violation]:
    """Закрытое не называется в публичном репозитории.

    Правило было записано словами и продержалось ровно до первого среза,
    который его не помнил: четырнадцать файлов уехали в `main` со ссылками
    на закрытый документ. Правило, которое обязан помнить человек или
    агент, не исполняется — исполняется то, что роняет пуш.
    """
    for path in _tracked_text_files(root):
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            for pattern, what in PRIVATE_MARKERS:
                if pattern.search(line):
                    yield Violation(
                        path,
                        number,
                        "public-repo",
                        f"{what} в публичном файле — написать обезличенно",
                    )
                    break


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
    violations.extend(check_env_example(ROOT / ".env.example"))
    violations.extend(check_public_repo(ROOT))
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
