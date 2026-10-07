"""Проверка волн контура модуля «Продажи» (`scripts/contour_waves.py`).

По тесту на каждый пример спеки среза `sales-v0-contour`: id примера стоит
в строке `def`, по нему дайджест утверждений находит ожидание, подписанное
до кода. Сценарии с базой PR — во временном git-репозитории из двух коммитов.
"""

from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest
from scripts import contour_waves as cw

SALES_INIT = "backend/features/sales/__init__.py"
LEADS = "backend/features/sales/leads.py"
PROMPT = "backend/features/sales/prompts/reply_kind.md"
LETTERS = "backend/features/letters/sending.py"
LESSONS = "## Уроки\n- L7: объявление, живущее только в STATUS, умирает с поставкой\n"
#: Волна → (триггеры, предел) — как в реестре репозитория; это держит последний тест.
ROWS = {
    "В0": (("sales-code",), "this-pr"),
    "В1": (("sales-code",), "sales-in-base"),
    "В3а": (("sales-prompt", "sales-llm"), "this-pr"),
    "В3б": (("sales-agent-prompt",), "this-pr"),
    "В3в": (("sales-segment-prompt",), "this-pr"),
    "В4": (("sales-autosend",), "this-pr"),
    "В2": (("manual",), "sales-thresholds"),
    "В-обн": (("canon-ahead",), "manual"),
}
Capture = pytest.CaptureFixture[str]


def registry(states: dict[str, str] | None = None) -> str:
    """Реестр, где все волны `pending`, кроме названных. Строка В0 — пятая."""
    states = states or {}
    rows = [
        f"| {wave} | ось | {', '.join(f'`{t}`' for t in triggers)} | `{limit}` "
        f"| {states.get(wave, 'pending')} | {'#1' if states.get(wave) == 'deployed' else '—'} |"
        for wave, (triggers, limit) in ROWS.items()
    ]
    head = "| " + " | ".join(cw.COLUMNS) + " |\n|---|---|---|---|---|---|\n"
    return "# Реестр\n\n" + head + "\n".join(rows) + "\n"


def docs(
    *,
    slug: str = "donors-x",
    reg: str = "",
    surface: str = "n/a reason=модели нет",
    extra: str = "",
    tasks: str = "",
    acceptance: str = "**Stack:** delivery@1.95 · cqg@2.43\n\nstack-selftest: external (`~/canon`)\n",
) -> dict[str, str]:
    return {
        cw.REGISTRY: reg or registry(),
        cw.STATUS: f"- **slug:** {slug}\n- **model_surface:** {surface}\n{extra}",
        cw.TASKS: tasks,
        cw.ACCEPTANCE: acceptance,
    }


def write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")


def repo(root: Path, base: dict[str, str], head: dict[str, str]) -> str:
    """Два коммита — база PR и голова. Возвращает sha базы."""
    root.mkdir(parents=True, exist_ok=True)

    def git(*args: str) -> str:
        command = ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args]
        return subprocess.run(command, cwd=root, check=True, capture_output=True, text=True).stdout

    git("init", "-q")
    write(root, base)
    git("add", "-A")
    git("commit", "-qm", "база")
    sha = git("rev-parse", "HEAD").strip()
    write(root, head)
    git("add", "-A")
    git("commit", "-qm", "голова", "--allow-empty")
    return sha


def run(root: Path, capsys: Capture, *flags: str) -> tuple[int, str]:
    code = cw.main(["--root", str(root), "--canon", str(root / "нет-канона"), *flags])
    return code, capsys.readouterr().out


def test_tree_without_sales_is_green_and_says_so(tmp_path: Path, capsys: Capture) -> None:  # A1
    write(tmp_path, docs())
    code, out = run(tmp_path, capsys)
    assert code == 0
    assert "○ продаж в дереве нет — судить нечего" in out
    assert all(f"{wave} pending" in out for wave in ROWS)


@pytest.mark.parametrize(
    ("flags", "code", "mark"), [((), 1, "✗ "), (("--warn",), 0, "⚠ предупреждение: ")]
)
def test_first_sales_pr_without_v0(
    tmp_path: Path, capsys: Capture, flags: tuple[str, ...], code: int, mark: str
) -> None:  # A2 A3
    base = repo(tmp_path, docs(), {SALES_INIT: ""})
    got, out = run(tmp_path, capsys, "--base", base, *flags)
    assert got == code
    expected = "В0: в дереве есть backend/features/sales/, волна не развёрнута — разверни или запиши weak/absent/n/a reason=…"
    assert mark + expected in out


@pytest.mark.parametrize(
    ("in_base", "code", "line"),
    [
        (False, 0, "○ В1: триггер сработал, предел — следующий PR продаж"),
        (True, 1, "✗ В1: в базе PR уже есть backend/features/sales/, волна не развёрнута"),
    ],
)
def test_v1_limit_is_the_next_sales_pr(
    tmp_path: Path, capsys: Capture, in_base: bool, code: int, line: str
) -> None:  # A4 A5
    base_files = docs(reg=registry({"В0": "deployed"}))
    if in_base:
        base_files[SALES_INIT] = ""
    base = repo(tmp_path, base_files, {LEADS: ""})
    got, out = run(tmp_path, capsys, "--base", base)
    assert got == code
    assert line in out


NOT_NAMED = "✗ промпт reply_kind.md не назван в model_surface"


def run_with_prompt(tmp_path: Path, capsys: Capture, surface: str) -> tuple[int, str]:
    """PR приносит промпт продаж при развёрнутой В3а и такой строке `model_surface`."""
    files = docs(reg=registry({"В0": "deployed", "В3а": "deployed"}), surface=surface)
    base = repo(tmp_path, files, {PROMPT: "Разбери ответ."})
    return run(tmp_path, capsys, "--base", base)


@pytest.mark.parametrize(
    ("surface", "code"), [("`backend/features/keywords/prompts/` (guides)", 1), (f"`{PROMPT}`", 0)]
)
def test_prompt_must_be_named_in_model_surface(
    tmp_path: Path, capsys: Capture, surface: str, code: int
) -> None:  # A6
    got, out = run_with_prompt(tmp_path, capsys, surface)
    assert got == code
    assert (NOT_NAMED in out) is (code == 1)


@pytest.mark.parametrize(
    "surface",
    [
        "`backend/features/sales/prompts/`",
        "backend/features/keywords/prompts/, backend/features/sales/prompts <!-- пины — в llm.py -->",
        "backend/features/sales/",
        "backend/features/sales/prompts/*.md",
    ],
    ids=["directory", "list-and-comment", "parent-directory", "glob"],
)
def test_declared_directory_covers_its_prompts(
    tmp_path: Path, capsys: Capture, surface: str
) -> None:
    """Каталог или маска покрывают промпт без имени файла — так `model_surface`
    читает фазовый гейт (`runtime_touched`), и правильная строка не красит волны."""
    got, out = run_with_prompt(tmp_path, capsys, surface)
    assert got == 0
    assert NOT_NAMED not in out


@pytest.mark.parametrize(
    "surface",
    [
        f"backend/features/keywords/prompts/ <!-- {PROMPT} -->",
        f"<!-- {PROMPT} -->",
        f"backend/features/keywords/prompts/\n  <!-- {PROMPT} -->",
    ],
    ids=["after-path", "comment-only", "next-line"],
)
def test_path_in_comment_is_not_declared(tmp_path: Path, capsys: Capture, surface: str) -> None:
    """Пояснение в `<!-- … -->` — не объявление: путь из комментария промпт не покрывает."""
    got, out = run_with_prompt(tmp_path, capsys, surface)
    assert got == 1
    assert NOT_NAMED in out


@pytest.mark.parametrize(
    "surface",
    [
        "backend/features/keywords/prompts/reply_kind.md",
        "backend/features/sales/prompts/archive/reply_kind.md",
    ],
    ids=["same-name-other-module", "same-name-subdirectory"],
)
def test_prompt_outside_declared_paths_stays_a_violation(
    tmp_path: Path, capsys: Capture, surface: str
) -> None:
    """Совпало имя файла, а путь другой — промпт не объявлен, нарушение остаётся."""
    got, out = run_with_prompt(tmp_path, capsys, surface)
    assert got == 1
    assert NOT_NAMED in out


@pytest.mark.parametrize(
    ("state", "code"), [("absent", 1), ("absent reason=канона шестого слоя нет", 0)]
)
def test_recorded_wave_needs_a_reason(
    tmp_path: Path, capsys: Capture, state: str, code: int
) -> None:  # A7
    write(tmp_path, docs(reg=registry({"В4": state})))
    got, out = run(tmp_path, capsys)
    assert got == code
    assert ("✗ реестр: строка 10 — В4: absent без reason=" in out) is (code == 1)


def test_sales_code_without_registry_is_red(tmp_path: Path, capsys: Capture) -> None:  # A8
    files = docs()
    del files[cw.REGISTRY]
    base = repo(tmp_path, files, {SALES_INIT: ""})
    got, out = run(tmp_path, capsys, "--base", base)
    assert got == 1
    assert "✗ реестр волн не найден" in out


@pytest.mark.parametrize(
    ("extra", "code"), [("", 1), ("- **stack-selftest:** external (`~/canon`)\n", 0)]
)
def test_stack_selftest_must_be_declared_somewhere(
    tmp_path: Path, capsys: Capture, extra: str, code: int
) -> None:  # A9
    write(tmp_path, docs(acceptance="**Stack:** delivery@1.95\n", extra=extra))
    got, out = run(tmp_path, capsys)
    assert got == code
    assert ("✗ stack-selftest: нет ни в delivery/STACK-ACCEPTANCE.md, ни в STATUS" in out) is (
        code == 1
    )


@pytest.mark.parametrize(
    ("tasks", "code"), [("# Tasks\n- [ ] T1\n", 1), ("## Уроки\n- …\n", 1), (LESSONS, 0)]
)
def test_sales_slice_needs_lessons(
    tmp_path: Path, capsys: Capture, tasks: str, code: int
) -> None:  # A10
    base = repo(tmp_path, docs(slug="sales-x"), {cw.TASKS: tasks})
    got, out = run(tmp_path, capsys, "--base", base)
    assert got == code
    assert ("✗ срез sales-x: в delivery/active/tasks.md нет раздела «Уроки»" in out) is (code == 1)


def test_canon_ahead_is_a_pre_push_warning_only(tmp_path: Path, capsys: Capture) -> None:  # A11
    canon = tmp_path / "Prepare"
    write(
        canon,
        {"AGENT_DELIVERY_HARNESS.md": "**Canon version:** `delivery@1.96` · журнал в конце\n"},
    )
    write(tmp_path, docs())
    warned = run(tmp_path, capsys, "--warn", "--canon", str(canon))
    judged = run(tmp_path, capsys, "--canon", str(canon))
    assert warned[0] == 0
    assert "⚠ предупреждение: канон впереди: delivery 1.95 → 1.96" in warned[1]
    assert judged[0] == 0
    assert "канон впереди" not in judged[1]


@pytest.mark.parametrize(
    "row",
    [
        "| В0 | ось | `sales-code` | `this-pr` | pending | — | лишняя |",
        "| В0 | ось | `sales-code` | `this-pr` | готово | — |",
        "| <волна> | <ось> | <триггер> | <предел> | <состояние> | <доказательство> |",
    ],
)
def test_broken_registry_names_the_line(tmp_path: Path, capsys: Capture, row: str) -> None:  # A12
    clean = registry()
    write(tmp_path, docs(reg=clean.replace(clean.splitlines()[4], row)))
    got, out = run(tmp_path, capsys)
    assert got == 1
    assert "✗ реестр не разобран: строка 5 — " in out
    assert "нарушений нет" not in out


DECLARED = (
    f"- **shared_changes:** `{LETTERS}` — общая правка; согласовано: сессия outreach-donors\n"
)


@pytest.mark.parametrize(
    ("extra", "code", "line"),
    [
        (
            "",
            1,
            "✗ общий код тронут без объявления: letters/sending.py — объяви shared_changes и с кем согласовано",
        ),
        (
            f"- **shared_changes:** `{LETTERS}` — общая правка\n",
            1,
            "✗ shared_changes без «согласовано: …»",
        ),
        (DECLARED, 0, "contour-waves: нарушений нет"),
    ],
)
def test_sales_slice_declares_shared_code(
    tmp_path: Path, capsys: Capture, extra: str, code: int, line: str
) -> None:  # A13
    files = docs(slug="sales-x", reg=registry({"В0": "deployed"}), tasks=LESSONS, extra=extra)
    base = repo(tmp_path, files, {LETTERS: "x = 1\n", LEADS: ""})
    got, out = run(tmp_path, capsys, "--base", base)
    assert got == code
    assert line in out


def test_donor_pr_under_active_sales_slice_is_never_red(
    tmp_path: Path, capsys: Capture
) -> None:  # A13
    """PR доноров идёт при активном срезе продаж: общий код — его, чужой коммит не роняем."""
    files = {
        **docs(slug="sales-x", reg=registry({"В0": "deployed"}), tasks=LESSONS),
        SALES_INIT: "",
    }
    base = repo(tmp_path, files, {LETTERS: "x = 1\n"})
    got, out = run(tmp_path, capsys, "--base", base)
    assert got == 0
    assert "общий код тронут" not in out
    assert "⚠ предупреждение: В1: в базе PR уже есть backend/features/sales/" in out
    assert "○ PR не несёт работы продаж" in out


def test_reverse_run_empty_sales_mask_turns_a2_and_a5_green(
    tmp_path: Path, capsys: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:  # A2 A5
    """Обратный прогон файлом: маска кода продаж не совпадает ни с чем — сценарии A2 и A5
    зеленеют, то есть их тесты выше упали бы. Красное там даёт детектор, а не случай."""
    for name in ("sales-code", "sales-in-base"):
        monkeypatch.setitem(
            cw.DETECTORS, name, dataclasses.replace(cw.DETECTORS[name], pattern="(?!)")
        )
    a2 = repo(tmp_path / "a2", docs(), {SALES_INIT: ""})
    a5 = repo(
        tmp_path / "a5", {**docs(reg=registry({"В0": "deployed"})), SALES_INIT: ""}, {LEADS: ""}
    )
    assert run(tmp_path / "a2", capsys, "--base", a2)[0] == 0
    assert run(tmp_path / "a5", capsys, "--base", a5)[0] == 0


LINTER = "[importlinter]\nroot_packages =\n    backend\n"
FORBIDS = "[importlinter:contract:x]\ntype = forbidden\nsource_modules =\n    backend.features.{}\nforbidden_modules =\n    backend.features.{}\n"  # fmt: skip


@pytest.mark.parametrize(
    ("contract", "code"),
    [
        (FORBIDS.format("letters", "sales"), 0),
        ("# контракт про backend.features.sales убран, комментарий остался\n", 1),
        (FORBIDS.format("sales", "letters"), 1),
    ],
    ids=["contract", "comment", "sales-as-source"],
)
def test_v1_evidence_is_a_contract_not_a_comment(
    tmp_path: Path, capsys: Capture, contract: str, code: int
) -> None:  # A3
    """Улика В1 — контракт, запрещающий продажи. Комментарий со словами модуля
    и контракт, где продажи — источник, а не запрет, уликой не считаются."""
    files = docs(reg=registry({"В0": "deployed", "В1": "deployed"}))
    write(tmp_path, {**files, ".importlinter": LINTER + contract})
    got, out = run(tmp_path, capsys)
    assert got == code
    assert ("✗ В1: в .importlinter нет контракта с backend.features.sales" in out) is (code == 1)


def test_repository_registry_mirrors_these_fixtures() -> None:  # A12
    waves, errors = cw.parse_registry((cw.ROOT / cw.REGISTRY).read_text(encoding="utf-8"))
    assert errors == []
    assert {w.name: (w.triggers, w.limit) for w in waves} == ROWS
    assert {w.name for w in waves if w.state == "deployed"} == {"В0", "В1", "В3а", "В3б", "В-обн"}


AGENT_PROMPT = "backend/features/sales/agent/prompts/judge.md"
AGENT_NOT_NAMED = "✗ промпт judge.md не назван в model_surface"


@pytest.mark.parametrize(
    ("states", "surface", "line"),
    [
        (
            {"В3а": "deployed"},
            f"`{AGENT_PROMPT}`",
            "✗ В3б: в sales/agent/ есть промпт агента или судьи, волна не развёрнута",
        ),
        (
            {"В3а": "deployed", "В3б": "deployed"},
            "backend/features/keywords/prompts/",
            AGENT_NOT_NAMED,
        ),
        (
            {"В3а": "n/a reason=разбор ответов — другим PR", "В3б": "deployed"},
            "backend/features/keywords/prompts/",
            AGENT_NOT_NAMED,
        ),
        (
            {"В3а": "deployed", "В3б": "deployed"},
            "backend/features/sales/agent/prompts/ <!-- судья judge.md -->",
            None,
        ),
    ],
    ids=["pending", "not-named", "not-named-v3b-alone", "named"],
)
def test_agent_prompt_brings_wave_v3b(
    tmp_path: Path, capsys: Capture, states: dict[str, str], surface: str, line: str | None
) -> None:  # В3б
    """Промпт агента продаж — триггер В3б (срез `sales-v3b` назвал детектор): волна
    обязана быть развёрнута в том же PR, а промпт — назван в model_surface. Промпт,
    не названный ни для В3а, ни для В3б, — одна находка, а не две."""
    files = docs(reg=registry({"В0": "deployed", **states}), surface=surface)
    base = repo(tmp_path, files, {AGENT_PROMPT: "Проверь черновик."})
    got, out = run(tmp_path, capsys, "--base", base)
    assert got == (0 if line is None else 1)
    assert line is None or line in out
    assert out.count(AGENT_NOT_NAMED) == (line == AGENT_NOT_NAMED)


def test_reverse_run_without_the_agent_prompt_detector_v3b_is_silent(
    tmp_path: Path, capsys: Capture, monkeypatch: pytest.MonkeyPatch
) -> None:  # В3б
    """Обратный прогон: детектор промпта агента не видит ничего — сценарий «pending»
    зеленеет. Красное там даёт детектор, а не случай."""
    name = "sales-agent-prompt"
    monkeypatch.setitem(cw.DETECTORS, name, dataclasses.replace(cw.DETECTORS[name], pattern="(?!)"))
    files = docs(reg=registry({"В0": "deployed", "В3а": "deployed"}), surface=f"`{AGENT_PROMPT}`")
    base = repo(tmp_path, files, {AGENT_PROMPT: "Проверь черновик."})
    assert run(tmp_path, capsys, "--base", base)[0] == 0
