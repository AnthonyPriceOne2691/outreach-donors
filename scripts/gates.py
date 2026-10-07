"""Архитектурные гейты: правила, которые линтер проверить не умеет.

Все пороги здесь **жёсткие**, без снимка легаси. Это возможно потому, что
гейты разворачиваются рано: нарушений нет, и замораживать нечего. Снимок
понадобился бы, если бы правила вводили в проект с накопленным долгом —
тогда он тает по мере правок. Нам растапливать нечего, и это выигрыш:
жёсткий ноль сильнее любого ратчета.

Запуск: `python scripts/gates.py [файлы...] [--commits BASE] [--commits-warn BASE]
[--pr-env]`. Без файлов проверяет всё дерево backend, tests и scripts; с `--commits`
— ещё и сообщения коммитов `BASE..HEAD` (CI на PR и pre-push), с `--commits-warn` —
то же предупреждением (push в main), с `--pr-env` — заголовок и тело PR из
окружения. Один `--pr-env` дерево не обходит: это шаг на секунды.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

# Признаки закрытого и откуда брать текст — в `public_repo.py`: стандартная
# библиотека без соседей. Каталог скрипта — в пути импорта: из тестов и под
# `python -I` его там нет.
sys.path.append(str(Path(__file__).resolve().parent))
import public_repo

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
    #: Файл — или место вне дерева: «коммит <sha>», «заголовок PR», «тело PR».
    path: Path | str
    line: int
    rule: str
    message: str

    def __str__(self) -> str:
        where = self.path
        if isinstance(where, Path) and where.is_relative_to(ROOT):
            where = where.relative_to(ROOT)
        return f"{where}:{self.line} [{self.rule}] {self.message}"


#: Механика контура: НАШ репозиторий, ЧУЖОЕ авторство. Она приезжает
#: перевендориванием из репозитория канона и чинится там же — правка, сделанная
#: здесь, разойдётся со снимком, и доктор контура объявит адаптацию, которой не
#: делали. Замер 30.09, первое развёртывание: этот гейт дал 36 нарушений, ВСЕ в
#: payload (scripts/okf_*.py, scripts/delivery_*.py) при нуле в коде проекта.
#: Тот же принцип уже стоит у ruff в pyproject.toml (CQG §6, третий принцип).
CONTOUR_PREFIXES = (
    "scripts/lint/",
    "scripts/delivery_",
    "scripts/okf_",
    "scripts/merge_guard",
    "docs/canon/",
    "extract_payload.py",
)


def _is_contour(path: Path) -> bool:
    # Без try/except: голый `except` здесь ловил бы собственный гейт
    # silent-except — прибор обязан жить по правилу, которое требует от других.
    if not path.is_relative_to(ROOT):
        return False
    return path.relative_to(ROOT).as_posix().startswith(CONTOUR_PREFIXES)


def _python_files(targets: Iterable[Path]) -> Iterator[Path]:
    for target in targets:
        if target.is_file() and target.suffix == ".py":
            if not _is_contour(target):
                yield target
        elif target.is_dir():
            for path in sorted(target.rglob("*.py")):
                if "migrations" in path.parts or "__pycache__" in path.parts:
                    continue
                if _is_contour(path):
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


#: Кому окружение — вход проверки, а не конфигурация: источник текста PR. Заголовок
#: и тело приходят в шаг CI только через `env:` — подстановка в `run:` исполнила бы
#: их как код.
ENV_INPUT = frozenset({"scripts/public_repo.py"})


def check_config_access(path: Path, source: str) -> Iterator[Violation]:
    """Переменные окружения читает только config: иначе опечатка в имени
    прячется от типизатора и всплывает в проде.

    Тестовая оснастка исключена намеренно: выбор тестовой базы через
    окружение — это про запуск, а не про конфигурацию сервиса. Источник
    текста PR (`ENV_INPUT`) — по той же причине.
    """
    where = (path.relative_to(ROOT) if path.is_relative_to(ROOT) else path).as_posix()
    if "config" in path.parts or "tests" in path.parts or where in ENV_INPUT:
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


def check_public_repo(root: Path) -> Iterator[Violation]:
    """Закрытое не называется в публичном репозитории.

    Правило было записано словами и продержалось ровно до первого среза,
    который его не помнил: четырнадцать файлов уехали в `main` со ссылками
    на закрытый документ. Правило, которое обязан помнить человек или
    агент, не исполняется — исполняется то, что роняет пуш.
    """
    for path in public_repo.tracked_text_files(root):
        # Payload контура исключён по той же причине, что и в _python_files:
        # слово «заказчик» в docstring чужого по авторству файла — не наш текст
        # и правится не здесь. Замер: единственное срабатывание правила на
        # свежем развёртывании было в scripts/delivery_history.py.
        if _is_contour(path):
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for number, what in public_repo.private_lines(text):
            yield Violation(
                path, number, "public-repo", f"{what} в публичном файле — написать обезличенно"
            )


#: Что делать с именем в сообщении, которое уже в main: остановка его не исправит.
PUBLISHED = "уже опубликовано: остановка не исправит, разобрать вручную"


def check_commit_messages(
    root: Path, base: str, advice: str = "переписать сообщение обезличенно"
) -> Iterator[Violation]:
    """Закрытое не называется и в сообщениях коммитов `base..HEAD`.

    Сообщение уходит в публичную историю так же, как файл, и после слияния его
    не переписать, а гейт по файлам его не видит: «grep по сообщениям перед
    пушем» держался на памяти. Число прочитанных сообщений печатается — «чисто»
    не должно выглядеть так же, как «ничего не прочитано».
    """
    messages = public_repo.commit_messages(root, base)
    print(f"public-repo: сообщений коммитов {base}..HEAD — {len(messages)}", flush=True)
    for sha, message in messages:
        for number, what in public_repo.private_lines(message):
            yield Violation(
                f"коммит {sha[:9]}",
                number,
                "public-repo",
                f"{what} в сообщении коммита — {advice}",
            )


def check_pr_text(title: str, body: str) -> Iterator[Violation]:
    """Заголовок и тело PR: из них GitHub собирает squash-коммит, который уйдёт в main.

    Пустой заголовок — не «чисто», а непереданный вход: у PR заголовок есть всегда.
    Тела нет — пустая строка, судить в ней нечего.
    """
    if not title.strip():
        raise public_repo.NotJudgedError("заголовок PR пуст — шагу не передан PR_TITLE")
    lines = len(body.splitlines())
    print(f"public-repo: текст PR — заголовок и тело, строк в теле: {lines}", flush=True)
    for where, place, text in (("заголовок PR", "заголовке", title), ("тело PR", "теле", body)):
        for number, what in public_repo.private_lines(text):
            yield Violation(
                where, number, "public-repo", f"{what} в {place} PR — переписать обезличенно"
            )


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


@dataclass(slots=True)
class Verdict:
    """Итог прогона. Нарушение краснит. «Не судил» — тоже: непрочитанный вход
    не должен выглядеть чистым. Предупреждение видно и код не меняет."""

    violations: list[Violation] = field(default_factory=list)
    warnings: list[Violation] = field(default_factory=list)
    unjudged: int = 0

    def collect(self, found: Iterable[Violation], *, warn: bool = False) -> None:
        """Находки проверки; непрочитанный вход — вслух и в счёт «не судил».

        `warn` — вход уже опубликован (сообщение коммита в main): и находка, и
        «не судил» видны в выводе, а код выхода не меняют.
        """
        try:
            (self.warnings if warn else self.violations).extend(found)
        except public_repo.NotJudgedError as exc:
            print(f"{'⚠ предупреждение: ' if warn else ''}гейт не судил: {exc}", file=sys.stderr)
            if not warn:
                self.unjudged += 1


def _report(verdict: Verdict, targets: list[Path] | None) -> int:
    """Итог в вывод и код выхода. Без файлов (`targets is None`) — только текст PR."""
    for warning in verdict.warnings:
        print(f"⚠ предупреждение: {warning}")
    if not (verdict.violations or verdict.unjudged):
        judged = "текст PR" if targets is None else f"{len(list(_python_files(targets)))} файлов"
        warned = f"; предупреждений: {len(verdict.warnings)}" if verdict.warnings else ""
        print(f"Гейты пройдены: {judged}, нарушений нет{warned}.")
        return 0
    for violation in verdict.violations:
        print(str(violation), file=sys.stderr)
    tail = f"; не судил: {verdict.unjudged} — это не «чисто»" if verdict.unjudged else ""
    count = len(verdict.violations)
    print(f"\nНарушений: {count}{tail}. Пороги жёсткие — снимка легаси нет.", file=sys.stderr)
    return 1


def _args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Архитектурные гейты проекта.")
    parser.add_argument("targets", nargs="*", type=Path, help="файлы; без них — всё дерево")
    parser.add_argument(
        "--commits", metavar="BASE", help="и сообщения коммитов BASE..HEAD; не прочитаны — красное"
    )
    parser.add_argument(
        "--commits-warn", metavar="BASE", help="то же предупреждением, код 0 (push в main)"
    )
    parser.add_argument(
        "--pr-env", action="store_true", help="заголовок и тело PR из PR_TITLE и PR_BODY"
    )
    return parser.parse_args(argv)


def _judges_files(args: argparse.Namespace) -> bool:
    """Файлы судятся всегда, кроме одного случая: задан только текст PR. Шаг
    `pr-text.yml` судит заголовок и тело за секунды — обход дерева там лишний."""
    return not args.pr_env or bool(args.targets or args.commits or args.commits_warn)


def main(argv: list[str]) -> int:
    args = _args(argv)
    verdict = Verdict()
    targets = None
    if _judges_files(args):
        targets = [path.resolve() for path in args.targets] or [
            ROOT / "backend",
            ROOT / "tests",
            ROOT / "scripts",
        ]
        verdict.collect(run(targets))
        verdict.collect(check_env_example(ROOT / ".env.example"))
        verdict.collect(check_public_repo(ROOT))
    if args.commits:
        verdict.collect(check_commit_messages(ROOT, args.commits))
    if args.commits_warn:
        verdict.collect(check_commit_messages(ROOT, args.commits_warn, PUBLISHED), warn=True)
    if args.pr_env:
        verdict.collect(check_pr_text(*public_repo.pr_text_from_env()))
    return _report(verdict, targets)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
