"""Питон ставится только из uv.lock — и в CI, и в образе, одной версией uv.

Инвариант — про **соответствие трёх файлов**: `uv.lock` фиксирует версии,
а ставят по нему `.github/workflows/ci.yml` и `Dockerfile`. Разойтись им
легко и незаметно: одна строка `pip install` в образе ради одной
библиотеки ставит её и всё, что она тянет, свежим, мимо lock-файла, — и на
прод уезжают версии, которых не видел ни один тест. Так 25.09.2026 CI сам
поставил только что вышедшую SQLAlchemy 2.1.0; тогда упал mypy, а в образе
падать было бы нечему.

Что lock-файл совпадает с pyproject.toml, проверяет не этот тест, а шаг CI
`uv lock --check` и хук перед отправкой: сверке нужен сам uv.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_DOCKERFILE = _ROOT / "Dockerfile"
_CI = _ROOT / ".github" / "workflows" / "ci.yml"

#: Образ, из которого Dockerfile берёт uv: `FROM ghcr.io/astral-sh/uv:<версия> AS uv`.
_UV_IMAGE = re.compile(r"ghcr\.io/astral-sh/uv:(\d[\w.+-]*)")

#: Установка питон-пакетов мимо lock-файла: pip в любом виде и `uv pip install`,
#: который тоже выбирает версии сам.
_BYPASS = re.compile(r"\b(?:pip3?|python3?\s+-m\s+pip|uv\s+pip)\s+install\b")


def _dockerfile_code() -> list[str]:
    """Строки Dockerfile без комментариев: в них слово `pip` законно."""
    lines = _DOCKERFILE.read_text(encoding="utf-8").splitlines()
    return [line for line in lines if line.strip() and not line.lstrip().startswith("#")]


def _check_steps() -> list[dict[str, Any]]:
    """Шаги работы `check` — той, что гоняет тесты."""
    workflow = yaml.safe_load(_CI.read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = workflow["jobs"]["check"]["steps"]
    return steps


def _shell(run: str) -> str:
    """Команды шага без комментариев оболочки."""
    return "\n".join(line.split("#", 1)[0] for line in run.splitlines())


def test_ci_and_image_use_the_same_uv() -> None:
    """Lock-файл сверяет и ставит одна и та же версия uv.

    Две версии — два разных ответа на вопрос «что стоит»: CI проверил бы
    lock одним инструментом, а образ поставил бы по нему другим.
    """
    in_image = {version for line in _dockerfile_code() for version in _UV_IMAGE.findall(line)}
    in_ci = {
        str(step.get("with", {}).get("version"))
        for step in _check_steps()
        if str(step.get("uses", "")).startswith("astral-sh/setup-uv@")
    }

    assert len(in_image) == 1, f"в Dockerfile не одна версия uv: {sorted(in_image)}"
    assert in_ci == in_image, (
        f"uv в CI {sorted(in_ci)}, в образе {sorted(in_image)} — "
        "поднимать в ci.yml и Dockerfile одним коммитом"
    )


def test_image_installs_python_only_from_the_lock() -> None:
    code = _dockerfile_code()

    bypass = [line.strip() for line in code if _BYPASS.search(line)]
    syncs = [line.strip() for line in code if "uv sync" in line]

    assert bypass == [], f"образ ставит пакеты мимо uv.lock: {bypass}"
    assert syncs, "Dockerfile не ставит зависимости из uv.lock (`uv sync --locked`)"
    loose = [line for line in syncs if "--locked" not in line and "--frozen" not in line]
    assert loose == [], f"`uv sync` без --locked обновит lock-файл сам, а не упадёт: {loose}"


def test_ci_checks_the_lock_then_installs_from_it() -> None:
    """Сверка — отдельным шагом и раньше установки.

    Отдельным — чтобы красный шаг назывался причиной; раньше — чтобы тесты
    не шли на lock-файле, который не совпадает с pyproject.toml.
    """
    runs = [_shell(str(step.get("run", ""))) for step in _check_steps()]
    checks = [i for i, run in enumerate(runs) if "uv lock --check" in run]
    installs = [i for i, run in enumerate(runs) if "uv sync" in run]

    assert checks, "в CI нет шага `uv lock --check` — pyproject.toml и uv.lock разойдутся молча"
    assert installs, "CI не ставит зависимости из uv.lock"
    assert checks[0] < installs[0], "сверка lock-файла должна идти до установки"
    assert all("--frozen" in runs[i] or "--locked" in runs[i] for i in installs)
    assert not any(_BYPASS.search(run) for run in runs), "CI ставит пакеты мимо uv.lock"
