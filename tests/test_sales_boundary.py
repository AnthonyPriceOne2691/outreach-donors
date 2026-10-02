"""Граница модуля «Продажи»: почта не знает продажи — срез 1.2, волна В1.

Контракт `mail-does-not-know-sales` в `.importlinter` судит настоящий гейт слоёв
`scripts/lint/check_layers_gate.sh` — тот, что зовут pre-commit и CI. Обратный
прогон файлом (правило Prepare `reverse-run-must-be-a-file`): импорт продаж
подкладывается в копию `backend/` во временной папке, а не в само дерево —
прерванный тест не оставит нарушения в почте.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GATE = ROOT / "scripts/lint/check_layers_gate.sh"
CONFIG = (ROOT / ".importlinter").read_text(encoding="utf-8")
CONTRACT = "letters / outreach / replies / contacts не импортируют sales"
MAIL = ("letters", "outreach", "replies", "contacts")
SALES_IMPORT = "from backend.features.sales import models\n"
#: Реестр моделей: его законно читает любой, кому нужны таблицы, а он сам
#: импортирует модели продаж — их должен видеть Alembic.
REGISTRY_IMPORT = "from backend.features.core.models import ContactModel\n"


def _section() -> str:
    """Текст контракта в `.importlinter` — от заголовка до следующего."""
    found = re.search(
        r"(?ms)^\[importlinter:contract:mail-does-not-know-sales\].*?(?=^\[|\Z)", CONFIG
    )
    assert found is not None, "в .importlinter нет контракта mail-does-not-know-sales"
    return found.group(0)


def _tree(root: Path, config: str = CONFIG, **plants: str) -> Path:
    """Копия `backend/` и `.importlinter`; `plants` — пакет почты → подложенный модуль."""
    shutil.copytree(
        ROOT / "backend", root / "backend", ignore=shutil.ignore_patterns("__pycache__")
    )
    (root / ".importlinter").write_text(config, encoding="utf-8")
    for package, source in plants.items():
        (root / "backend/features" / package / "planted.py").write_text(source, encoding="utf-8")
    return root


def _gate(root: Path) -> tuple[int, str]:
    """Гейт слоёв в копии: `lint-imports` — из окружения этого прогона, настройки
    гейта — по умолчанию, строгий режим. Ширина вывода задана: import-linter
    переносит строки по ширине терминала. Половина TS в копии не судится — её
    конфига там нет, и гейт называет это вслух."""
    env = {name: value for name, value in os.environ.items() if not name.startswith("LINT_")}
    path = f"{Path(sys.executable).parent}{os.pathsep}{env['PATH']}"
    env |= {"STRICT": "1", "COLUMNS": "200", "PATH": path}
    done = subprocess.run(
        ["bash", str(GATE)], cwd=root, env=env, capture_output=True, text=True, check=False
    )
    return done.returncode, done.stdout + done.stderr


@pytest.mark.parametrize("package", MAIL)
def test_sales_import_planted_in_mail_turns_the_layers_gate_red(
    tmp_path: Path, package: str
) -> None:  # A1
    code, out = _gate(_tree(tmp_path, **{package: SALES_IMPORT}))
    assert code == 1, out
    assert f"{CONTRACT} BROKEN" in out
    assert f"backend.features.{package}.planted -> backend.features.sales" in out


def test_the_same_copy_without_the_import_is_green(tmp_path: Path) -> None:  # A1
    """Положительный контроль: красное выше дал подложенный импорт, а не копия."""
    code, out = _gate(_tree(tmp_path))
    assert code == 0, out
    seen = re.search(r"слои \(python\): OK\S* — просмотрено (\d+) файл", out)
    assert seen is not None, out
    assert int(seen.group(1)) > 0


def test_reverse_run_without_the_contract_the_import_passes(tmp_path: Path) -> None:  # A1
    """Обратный прогон: без контракта тот же импорт гейт пропускает — красное
    даёт этот контракт, а не три соседних."""
    code, out = _gate(_tree(tmp_path, CONFIG.replace(_section(), ""), letters=SALES_IMPORT))
    assert code == 0, out
    assert "слои (python): OK" in out


@pytest.mark.parametrize(("indirect", "code"), [("True", 0), ("False", 1)])
def test_mail_may_read_the_model_registry_that_lists_sales_models(
    tmp_path: Path, indirect: str, code: int
) -> None:
    """Почта законно читает реестр моделей, а реестр импортирует модели продаж:
    путь «почта → core.models → sales.models» косвенный. Его пропускает
    `allow_indirect_imports = True`; при `False` гейт красный."""
    section = _section()
    assert "allow_indirect_imports = True" in section
    config = CONFIG.replace(
        section,
        section.replace("allow_indirect_imports = True", f"allow_indirect_imports = {indirect}"),
    )
    got, out = _gate(_tree(tmp_path, config, letters=REGISTRY_IMPORT))
    assert got == code, out
    assert ("backend.features.core.models -> backend.features.sales.models" in out) is (code == 1)
