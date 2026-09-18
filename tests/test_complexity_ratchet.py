"""Ратчет сложности: закрепляет достигнутое и не пускает сползание.

Жёсткие пороги ловят обвал, ратчет — медленный рост. Проверяется то и
другое: что мера считает ветвления, что рост отвергается, и что улучшение
не проходит молча. Последнее важнее прочего: пропустив улучшение,
мы оставили бы снимок на старом уровне, и код мог бы вернуться к нему,
не встретив ни одного красного.
"""

from __future__ import annotations

import ast

from scripts.complexity import (
    FileMetrics,
    compare,
    function_complexity,
    load_snapshot,
    measure,
    measure_file,
)


def _complexity(source: str) -> int:
    tree = ast.parse(source)
    function = next(
        node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    )
    return function_complexity(function)


class TestMeasure:
    def test_straight_line_function_is_one(self) -> None:
        assert _complexity("def f():\n    return 1\n") == 1

    def test_every_branch_counts(self) -> None:
        source = (
            "def f(x):\n    if x:\n        return 1\n    for i in x:\n        pass\n    return 0\n"
        )
        assert _complexity(source) == 3

    def test_boolean_operators_count(self) -> None:
        """`a and b and c` — это два ветвления, а не одно."""
        assert _complexity("def f(a, b, c):\n    return a and b and c\n") == 3

    def test_lambda_counts_with_its_owner(self) -> None:
        """У лямбды нет своей записи: пропустив её, мы потеряли бы ветвление."""
        assert _complexity("def f(items):\n    return sorted(items, key=lambda x: x or 0)\n") == 2

    def test_nested_function_belongs_to_itself(self) -> None:
        """Иначе сложность внешней функции растёт от чужого кода — и вдвойне:
        у вложенной функции есть собственная запись."""
        source = (
            "def outer(x):\n"
            "    def inner(y):\n"
            "        if y:\n"
            "            return 1\n"
            "        return 0\n"
            "    return inner\n"
        )
        assert _complexity(source) == 1

    def test_file_metrics_name_the_worst_function(self, tmp_path: object) -> None:
        path = tmp_path / "sample.py"  # type: ignore[operator]
        path.write_text(
            "def simple():\n    return 1\n\n\ndef hard(x):\n    if x:\n        return 1\n    return 0\n",
            encoding="utf-8",
        )
        metrics = measure_file(path)
        assert metrics.complexity == 2
        assert metrics.worst.startswith("hard:")


class TestRatchet:
    def test_matching_snapshot_is_silent(self) -> None:
        current = {"a.py": FileMetrics(lines=10, complexity=3, worst="f:1")}
        snapshot = {"a.py": {"lines": 10, "complexity": 3, "worst": "f:1"}}
        assert compare(current, snapshot) == []

    def test_growth_is_refused_and_says_where(self) -> None:
        current = {"a.py": FileMetrics(lines=10, complexity=7, worst="heavy:42")}
        snapshot = {"a.py": {"lines": 10, "complexity": 3, "worst": "f:1"}}

        problems = compare(current, snapshot)
        assert len(problems) == 1
        assert "сложность выросла 3 → 7" in problems[0]
        assert "heavy:42" in problems[0]

    def test_length_growth_is_refused(self) -> None:
        current = {"a.py": FileMetrics(lines=480, complexity=3, worst="f:1")}
        snapshot = {"a.py": {"lines": 100, "complexity": 3, "worst": "f:1"}}
        assert "длина выросла 100 → 480" in compare(current, snapshot)[0]

    def test_improvement_must_be_locked_in(self) -> None:
        """Молча пропустив улучшение, мы оставили бы старый потолок —
        и код мог бы вернуться к нему законно."""
        current = {"a.py": FileMetrics(lines=10, complexity=2, worst="f:1")}
        snapshot = {"a.py": {"lines": 10, "complexity": 6, "worst": "f:1"}}

        problems = compare(current, snapshot)
        assert "упала 6 → 2" in problems[0]
        assert "--update" in problems[0]

    def test_new_file_must_be_recorded(self) -> None:
        current = {"new.py": FileMetrics(lines=20, complexity=4, worst="f:1")}
        problems = compare(current, {})
        assert "новый файл" in problems[0]

    def test_deleted_file_must_leave_the_snapshot(self) -> None:
        snapshot = {"gone.py": {"lines": 10, "complexity": 3, "worst": "f:1"}}
        problems = compare({}, snapshot)
        assert "файла больше нет" in problems[0]


class TestRealSnapshot:
    def test_repository_matches_its_snapshot(self) -> None:
        """Тот же вопрос, что задаёт хук и CI, — но здесь он виден в наборе.
        Расхождение означает: либо снимок забыли обновить, либо код пополз."""
        assert compare(measure(), load_snapshot()) == []
