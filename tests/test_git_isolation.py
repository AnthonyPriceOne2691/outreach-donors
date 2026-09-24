"""Тесты зовут git во временных папках — и только там.

24.09.2026 пуш из связанного дерева (`git worktree`) запустил хук, хук —
набор, а тест гейта публичного репозитория сделал `git init` и `git add -A`
во временной папке. Git передаёт хуку из связанного дерева абсолютный
`GIT_DIR` (из основной копии — никакого), тест его унаследовал, и `git init`
переинициализировал настоящий репозиторий: основная копия стала
`core.bare = true` и перестала отвечать на `git status`, а индекс дерева
заменился одним файлом из теста. Зелёный набор при этом ничего не заметил.

Рубежа два: хук сбрасывает привязку сам (`unset $(git rev-parse
--local-env-vars)`), а `conftest` убирает её из окружения до любого теста.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
from tests.conftest import drop_git_binding, git_binding

ROOT = Path(__file__).resolve().parent.parent


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


class TestTheBindingIsDropped:
    def test_binding_goes_and_the_rest_stays(self) -> None:
        environ = {
            "GIT_DIR": "/repo/.git/worktrees/wt",
            "GIT_INDEX_FILE": "/repo/.git/worktrees/wt/index",
            "GIT_EDITOR": "true",
            "PATH": "/usr/bin",
        }

        dropped = drop_git_binding(environ)

        assert sorted(dropped) == ["GIT_DIR", "GIT_INDEX_FILE"]
        assert environ == {"GIT_EDITOR": "true", "PATH": "/usr/bin"}

    def test_the_suite_runs_without_it(self) -> None:
        assert [name for name in git_binding() if name in os.environ] == []

    def test_the_list_comes_from_git_itself(self) -> None:
        """Список у git растёт с версиями; свой запасной его не заменяет."""
        assert {"GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"} <= set(
            git_binding()
        )


class TestTheRealRepositoryIsUntouched:
    def test_git_init_in_a_temp_folder_stays_there(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Как в хуке из связанного дерева: `GIT_DIR` указывает в настоящий
        репозиторий. После очистки `git init` и `git add -A` во временной
        папке его не трогают."""
        real = tmp_path / "real"
        real.mkdir()
        _git("init", "-q", cwd=real)
        (real / "kept.txt").write_text("своё\n", encoding="utf-8")
        _git("add", "kept.txt", cwd=real)
        monkeypatch.setenv("GIT_DIR", str(real / ".git"))

        drop_git_binding(os.environ)
        scratch = tmp_path / "scratch"
        (scratch / "docs").mkdir(parents=True)
        (scratch / "docs" / "note.md").write_text("чужое\n", encoding="utf-8")
        _git("init", "-q", cwd=scratch)
        _git("add", "-A", cwd=scratch)

        assert _git("config", "--bool", "core.bare", cwd=real) == "false"
        assert _git("ls-files", cwd=real) == "kept.txt"
        assert _git("ls-files", cwd=scratch) == "docs/note.md"


class TestTheHook:
    def test_hook_drops_the_binding_before_any_check(self) -> None:
        hook = (ROOT / "scripts" / "hooks" / "pre-push").read_text(encoding="utf-8")

        unset = hook.index("unset $(git rev-parse --local-env-vars)")

        assert unset < hook.index('"$PY/ruff"')
        assert unset < hook.index('"$PY/pytest"')
