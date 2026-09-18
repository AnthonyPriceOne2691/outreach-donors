"""Ратчет сложности: код может становиться проще, но не сложнее.

Жёсткие пороги на функцию уже стоят в линтере: сложность 10, двенадцать
ветвлений, пятьдесят операторов, пятьсот строк на файл. Они ловят обвал,
но не ловят сползание. Функция растёт с трёх до девяти, файл — с восьмидесяти
строк до четырёхсот девяноста, и всё это время набор зелёный: формально
нарушения нет. Через полгода каждый файл стоит вплотную к потолку, а момент,
когда это случилось, найти невозможно — его не было, было двести маленьких
шагов.

Ратчет фиксирует достигнутое. Снимок хранит по каждому файлу его длину
и сложность худшей функции; проверка требует, чтобы код был не хуже снимка.
Стало лучше — снимок подтягивается и новый уровень становится новым
потолком. Отсюда и название: храповик крутится в одну сторону.

**Почему расхождение в любую сторону — отказ.** Если пропускать улучшения
молча, снимок остаётся на старом уровне, и код может вернуться к нему,
не встретив ни одного красного. То есть улучшение не будет закреплено,
а ограничение, которое можно обойти, — не ограничение.

**Мера своя, и это осознанно.** Линтер называет только нарушителей порога,
а ратчету нужны числа по всем файлам. Точное совпадение с mccabe нам не
нужно: важно, чтобы мера была одна и та же вчера и сегодня.

Запуск:
    python scripts/complexity.py            # проверить
    python scripts/complexity.py --update   # принять текущий уровень
"""

from __future__ import annotations

import ast
import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOT_PATH = ROOT / "delivery" / "complexity-snapshot.json"

#: Что меряем. Тесты исключены намеренно: там длина и ветвление — это
#: параметризация и данные, а не сложность решения.
TARGETS = ("backend", "scripts")

#: Узлы, каждый из которых добавляет ветку исполнения.
BRANCH_NODES = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.ExceptHandler,
    ast.IfExp,
    ast.comprehension,
    ast.Assert,
    ast.match_case,
)


@dataclass(frozen=True, slots=True)
class FileMetrics:
    """Длина файла и сложность худшей функции в нём."""

    lines: int
    complexity: int
    worst: str  # имя функции и строка — чтобы отказ показывал, куда смотреть

    def as_snapshot(self) -> dict[str, object]:
        return {"lines": self.lines, "complexity": self.complexity, "worst": self.worst}


def _own_nodes(node: ast.AST) -> Iterator[ast.AST]:
    """Узлы функции без тел вложенных функций.

    Обход свой, а не `ast.walk`: тот спускается всюду, и ветвления
    вложенной функции приписывались бы внешней — дважды, потому что
    у вложенной есть собственная запись.

    Лямбды, наоборот, считаются вместе с хозяином: отдельной записи
    у них нет, и, пропустив их, мы потеряли бы ветвления совсем.
    """
    stack = list(ast.iter_child_nodes(node))
    while stack:
        current = stack.pop()
        if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        yield current
        stack.extend(ast.iter_child_nodes(current))


def function_complexity(node: ast.AST) -> int:
    """Число путей исполнения функции. Единица плюс ветка за каждое ветвление."""
    score = 1
    for child in _own_nodes(node):
        if isinstance(child, BRANCH_NODES):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += len(child.values) - 1
    return score


def _functions(tree: ast.AST) -> Iterator[ast.FunctionDef | ast.AsyncFunctionDef]:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            yield node


def measure_file(path: Path) -> FileMetrics:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(path))

    worst_name, worst_score = "—", 0
    for node in _functions(tree):
        score = function_complexity(node)
        if score > worst_score:
            worst_name, worst_score = f"{node.name}:{node.lineno}", score

    return FileMetrics(
        lines=source.count("\n") + 1,
        complexity=worst_score,
        worst=worst_name,
    )


def _python_files(targets: Iterable[str]) -> Iterator[Path]:
    for target in targets:
        for path in sorted((ROOT / target).rglob("*.py")):
            if {"migrations", "__pycache__", ".venv"} & set(path.parts):
                continue
            yield path


def measure(targets: Iterable[str] = TARGETS) -> dict[str, FileMetrics]:
    return {str(path.relative_to(ROOT)): measure_file(path) for path in _python_files(targets)}


def load_snapshot() -> dict[str, dict[str, object]]:
    if not SNAPSHOT_PATH.exists():
        return {}
    data = json.loads(SNAPSHOT_PATH.read_text(encoding="utf-8"))
    files = data.get("files") if isinstance(data, dict) else None
    return files if isinstance(files, dict) else {}


def save_snapshot(current: dict[str, FileMetrics]) -> None:
    payload = {
        "_comment": (
            "Ратчет сложности: код может становиться проще, но не сложнее. "
            "Обновлять командой python scripts/complexity.py --update."
        ),
        "files": {name: metrics.as_snapshot() for name, metrics in sorted(current.items())},
    }
    SNAPSHOT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _int(value: object) -> int:
    return value if isinstance(value, int) else 0


def compare(current: dict[str, FileMetrics], snapshot: dict[str, dict[str, object]]) -> list[str]:
    """Чем код расходится со снимком. Пустой список — совпадает.

    Расхождения называются по существу: «стало хуже» и «стало лучше» —
    разные сообщения, потому что делать с ними надо разное.
    """
    problems: list[str] = []

    for name, metrics in sorted(current.items()):
        recorded = snapshot.get(name)
        if recorded is None:
            problems.append(
                f"{name}: новый файл (строк {metrics.lines}, сложность {metrics.complexity}) — "
                "принять уровень командой --update"
            )
            continue

        for title, now, was in (
            ("длина", metrics.lines, _int(recorded.get("lines"))),
            ("сложность", metrics.complexity, _int(recorded.get("complexity"))),
        ):
            if now > was:
                where = f" (худшая функция {metrics.worst})" if title == "сложность" else ""
                problems.append(f"{name}: {title} выросла {was} → {now}{where}")
            elif now < was:
                problems.append(
                    f"{name}: {title} упала {was} → {now} — закрепить командой --update"
                )

    for name in sorted(set(snapshot) - set(current)):
        problems.append(f"{name}: файла больше нет — убрать из снимка командой --update")

    return problems


def main(argv: list[str]) -> int:
    current = measure()

    if "--update" in argv:
        save_snapshot(current)
        worst = max(current.values(), key=lambda m: m.complexity, default=None)
        print(f"Снимок обновлён: {len(current)} файлов.")
        if worst is not None:
            print(
                f"Потолок теперь: сложность {worst.complexity}, длина {max(m.lines for m in current.values())}."
            )
        return 0

    snapshot = load_snapshot()
    if not snapshot:
        print(
            "Снимка сложности нет. Создать: python scripts/complexity.py --update",
            file=sys.stderr,
        )
        return 1

    problems = compare(current, snapshot)
    if not problems:
        print(f"Ратчет сложности: {len(current)} файлов, расхождений со снимком нет.")
        return 0

    for problem in problems:
        print(problem, file=sys.stderr)
    print(
        f"\nРасхождений: {len(problems)}. Рост — это долг, который надо снять или объяснить; "
        "падение — достижение, которое надо закрепить снимком.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
