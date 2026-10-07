"""Проверка волн контура модуля «Продажи»: волну не пропустили.

Модуль наследует контур репозитория, но не его полноту: граница модуля,
поведение модели, минимум эксплуатации и понятия OKF приходят волнами. Реестр
`delivery/contour-waves.md` называет у волны триггер («нужна, когда…») и предел
(«не позже чем») — событиями в дереве, а не датами: дату машина не проверит.

Правило «помнить про волну» не исполняется — исполняется то, что краснеет (урок
`check_public_repo` в `scripts/gates.py`). По протоколу сети проектов на pre-push
(`--warn`) это предупреждение и пуш проходит, в CI на PR — красный. Без файлов
`backend/features/sales/` проверка зелёная и говорит это словами.

    python scripts/contour_waves.py --base origin/main          # как CI
    python scripts/contour_waves.py --warn --base origin/main   # как pre-push

Ядро — разбор реестра, детекторы, судья, печать — не читает файлов и не зовёт
git: это делает одна функция `collect`.
"""

from __future__ import annotations

import argparse
import configparser
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

# `model_surface` разбирают функции фазового гейта `delivery_check`, а не свой парсер:
# свой читал строку иначе — каталог без имени файла не засчитывал, путь из `<!-- … -->`
# засчитывал. Модули канона импортируют соседей голым именем, поэтому `scripts/`
# добавлен в `sys.path` (при запуске файлом он там уже есть).
sys.path.append(str(Path(__file__).resolve().parent))
from delivery_runtime import declared_surfaces, runtime_touched

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = "delivery/contour-waves.md"
STATUS = "delivery/active/STATUS.md"
TASKS = "delivery/active/tasks.md"
ACCEPTANCE = "delivery/STACK-ACCEPTANCE.md"
SALES = "backend/features/sales/"
COLUMNS = ["Волна", "Ось", "Триггер", "Предел", "Состояние", "Доказательство"]
#: Волны, которые обязаны стоять в реестре: удалённая строка — та же тишина,
#: что непришедшая волна без записи, только незаметнее.
REQUIRED = ("В0", "В1", "В3а", "В3б", "В3в", "В4", "В2", "В-обн")
STATE = re.compile(r"(deployed|pending|weak|absent|n/a)\b(.*)")
SALES_PATHS = ("backend/features/sales/*", "frontend/src/sales/*", "tests/test_sales_*",
               "scripts/*sales*", "okf/sales-*")  # fmt: skip
#: Не общий код: всё остальное в диффе PR среза `sales-*` объявляется (A13).
OWN_PATHS = (*SALES_PATHS, "delivery/*")
#: PR несёт работу продаж, если дифф трогает эти пути (или файлы среза продаж).
#: Краснеет только такой PR: PR доноров идут при активном срезе продаж и правят
#: общий код законно, а соседу нельзя ронять чужой коммит (протокол сети проектов).
WORK_PATHS = (*SALES_PATHS, REGISTRY, "scripts/contour_waves.py", "tests/test_contour_waves.py")
#: Где канон пишет свою версию: «**Canon version:** `слой@x.y`».
CANON_FILES = {"delivery": "AGENT_DELIVERY_HARNESS.md", "cqg": "CODE_QUALITY_GATES.md",
               "okf": "OKF_KNOWLEDGE_BUNDLE.md"}  # fmt: skip

#: Тексты находок — одно место. Каждое нарушение говорит, что делать.
MSG = {
    "row": "реестр не разобран: строка {n} — {why}",
    "columns": "колонок {got}, а нужно {need}: " + " | ".join(COLUMNS),
    "ids": "{name}: триггер и предел — id детекторов в обратных кавычках ({detail})",
    "state": "неизвестное состояние «{state}»: deployed | pending | weak/absent/n/a reason=…",
    "reason": "реестр: строка {n} — {name}: {kind} без reason= — непришедшая волна записывается с причиной",
    "no-proof": "реестр: строка {n} — {name}: deployed без доказательства",
    "missing": "реестр неполон: нет волн {names} — волна записывается, а не пропадает",
    "twice": "реестр: волна записана дважды — {names}",
    "no-table": "реестр не разобран: нет таблицы | " + " | ".join(COLUMNS) + " |",
    "no-registry": "реестр волн не найден: " + REGISTRY + " — волны не судимы, верни файл из main",
    "pending": "{name}: {what}, волна не развёрнута — разверни или запиши weak/absent/n/a reason=…",
    "ahead": "{name}: триггер сработал, предел — {limit}",
    "later": "{name}: {what} — пока волну судит человек",
    "proof": "{name}: доказательство {path} не найдено в дереве — deployed без улики",
    "rule": "{name}: {what} — без этой улики волна не развёрнута",
    "prompt": "промпт {file} не назван в model_surface — допиши его путь от корня или каталог, пин модели и схему выхода в model_surface STATUS",
    "selftest": "stack-selftest: нет ни в " + ACCEPTANCE + ", ни в STATUS — запиши stack-selftest: external (…) "
    "в STACK-ACCEPTANCE, иначе шаг CI «Canon payload selftest» красный",
    "lessons": "срез {slug}: в " + TASKS + " нет раздела «Уроки» с пунктами — прочитай уроки по путям диффа и сошлись на них",
    "shared": "общий код тронут без объявления: {files} — объяви shared_changes и с кем согласовано",
    "agreed": "shared_changes без «согласовано: …» — назови, с кем согласована правка общего кода",
    "no-base": "нет базы PR (--base): предел «следующий PR продаж» и общий код не судимы — "
    "CI передаёт базу из шага проверки волн в джобе check, pre-push — origin/main",
    "canon": "канон впереди: {layers} — догон отдельным срезом (волна В-обн), не в фиче",
    "git": "contour-waves: git {cmd} не ответил ({why}) — база PR не прочитана",
    "ini": "contour-waves: .importlinter не разобран парсером ini ({why}) — контракт не засчитан",
    "no-sales": "○ продаж в дереве нет — судить нечего",
    "clean": "contour-waves: нарушений нет",
    "warned": "contour-waves: предупреждений {count} — не блокирует; в CI на PR продаж это красное",
    "neighbour": "PR не несёт работы продаж — нарушения волн здесь предупреждение: чужой коммит не роняем",
    "failed": "contour-waves: нарушений {count} — мерж закрыт, пока волна не развёрнута или не записана с причиной",
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Detector:
    """Событие в дереве; `where` — где его искать.

    tree — пути под `sales/` в дереве PR; base — они же в базе PR; text — текст
    `.py` продаж; later — имя назовёт будущий срез; manual — решает человек;
    local — канон рядом, только pre-push; limit — «мерж этого PR».
    """

    where: str
    pattern: str
    says: str
    ahead: str = ""


#: Детекторы — данными, а не ветками судьи: новый детектор — строка здесь.
DETECTORS: dict[str, Detector] = {
    "sales-code": Detector("tree", r"^backend/features/sales/", "в дереве есть " + SALES),
    "sales-in-base": Detector("base", r"^backend/features/sales/", "в базе PR уже есть " + SALES,
                              "следующий PR продаж"),
    "sales-prompt": Detector("tree", r"^backend/features/sales/(?:.+/)?prompts/[^/]+\.md$",
                             "в sales/ есть файл промпта"),
    "sales-llm": Detector("text", r"\bbackend\.shared\.llm\b|\bfrom\s+backend\.shared\s+import\s[^\n]*\bllm\b",
                          "sales/ импортирует клиент модели"),
    "sales-agent-prompt": Detector("later", "", "промпт агента и судьи назовёт срез агента"),
    "sales-segment-prompt": Detector("later", "", "промпт судьи сегмента назовёт его срез"),
    "sales-autosend": Detector("later", "", "маркер автоотправки назовёт срез В4"),
    "sales-thresholds": Detector("later", "", "файл порогов агента назовёт срез агента"),
    "manual": Detector("manual", "", "решает человек"),
    "canon-ahead": Detector("local", "", "канон рядом ушёл вперёд от STACK-ACCEPTANCE"),
    "this-pr": Detector("limit", "", "мерж этого PR"),
}  # fmt: skip


def forbids_sales(config: str) -> bool:
    """Улика В1: секция `importlinter:contract:…`, где `backend.features.sales`
    стоит в `forbidden_modules`. Конфиг читает парсер ini, а не поиск подстроки:
    комментарий со словами модуля — не контракт (находка среза 1.2)."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(config)
    except configparser.Error as exc:
        print(MSG["ini"].format(why=exc), file=sys.stderr)
        return False
    contracts = [name for name in parser.sections() if name.startswith("importlinter:contract:")]
    return any("backend.features.sales" in parser.get(name, "forbidden_modules", fallback="").split()
               for name in contracts)  # fmt: skip


#: Что ещё обязано лежать в дереве у развёрнутой волны: (где, чем узнать, о чём сказать).
EVIDENCE = {
    "В1": (".importlinter", forbids_sales, "в .importlinter нет контракта с backend.features.sales"),
    "В2": ("okf/", re.compile(r"implementation:.*backend/features/sales").search,
           "у понятий okf/ нет implementation: в продажах"),
}  # fmt: skip


@dataclass(frozen=True, slots=True)
class Wave:
    """Строка реестра."""

    name: str
    triggers: tuple[str, ...]
    limit: str
    state: str
    evidence: str


@dataclass(frozen=True, slots=True)
class Tree:
    """Всё, что судье нужно знать о дереве, собранное заранее."""

    sales: tuple[str, ...]
    texts: dict[str, str]
    exists: frozenset[str]
    base: tuple[str, ...] | None = None
    changed: tuple[str, ...] | None = None


#: (нарушение?, текст). Сведение печатается и не краснит.
Finding = tuple[bool, str]


def bad(key: str, **values: object) -> Finding:
    return True, MSG[key].format(**values)


def note(text: str) -> Finding:
    return False, text


# --- Ядро: разбор реестра ---------------------------------------------------


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().removeprefix("|").removesuffix("|").split("|")]


def parse_registry(text: str) -> tuple[list[Wave], list[str]]:
    """Реестр → волны и ошибки. Битая строка — ошибка с номером, а не «волн нет» (A12)."""
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if _cells(line) == COLUMNS), None)
    if start is None:
        return [], [MSG["no-table"]]
    waves: list[Wave] = []
    errors: list[str] = []
    for number, line in enumerate(lines[start + 1 :], start=start + 2):
        if not line.lstrip().startswith("|"):
            break
        cells = _cells(line)
        if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
            continue
        row = _row(cells, number)
        if isinstance(row, str):
            errors.append(row)
        else:
            waves.append(row)
    names = [w.name for w in waves]
    if missing := [w for w in REQUIRED if w not in names]:
        errors.append(MSG["missing"].format(names=", ".join(missing)))
    if twice := sorted({n for n in names if names.count(n) > 1}):
        errors.append(MSG["twice"].format(names=", ".join(twice)))
    return waves, errors


def _row(cells: list[str], n: int) -> Wave | str:
    """Строка таблицы → волна или ошибка с номером строки."""
    if len(cells) != len(COLUMNS):
        return MSG["row"].format(n=n, why=MSG["columns"].format(got=len(cells), need=len(COLUMNS)))
    name, _axis, trigger, limit, state, evidence = cells
    triggers = tuple(re.findall(r"`([^`]+)`", trigger))
    limits = re.findall(r"`([^`]+)`", limit)
    unknown = [d for d in (*triggers, *limits) if d not in DETECTORS]
    if not triggers or len(limits) != 1 or unknown:
        detail = "неизвестные: " + ", ".join(unknown) if unknown else "нет id"
        return MSG["row"].format(n=n, why=MSG["ids"].format(name=name, detail=detail))
    kind = STATE.fullmatch(state)
    if kind is None:
        return MSG["row"].format(n=n, why=MSG["state"].format(state=state))
    if kind.group(1) in ("weak", "absent", "n/a") and not re.search(r"reason=\S", state):
        return MSG["reason"].format(n=n, name=name, kind=kind.group(1))
    if kind.group(1) == "deployed" and evidence in ("", "—", "-", "…"):
        return MSG["no-proof"].format(n=n, name=name)
    return Wave(name, triggers, limits[0], kind.group(1), evidence)


def evidence_paths(evidence: str) -> list[str]:
    """Пути репозитория из колонки «Доказательство»: в обратных кавычках и со слэшем."""
    return re.findall(r"`([\w.][^`\s]*/[^`\s]*)`", evidence)


def field(text: str, name: str) -> str:
    """Поле `- **name:** значение` или `name: значение` со строками-продолжениями."""
    head = rf"(?m)^[ \t]*(?:[-*][ \t]+)?\**{re.escape(name)}\**[ \t]*:\**[ \t]*"
    match = re.search(head + r"(.*(?:\n[ \t]+(?![-*][ \t]).+)*)", text)
    return match.group(1).strip() if match else ""


# --- Ядро: детекторы и судья ------------------------------------------------


def fires(name: str, tree: Tree) -> bool:
    """Сработал ли детектор. `later`, `manual`, `local` и `limit` здесь не судятся."""
    det = DETECTORS[name]
    if det.where == "text":
        return any(re.search(det.pattern, t) for p, t in tree.texts.items() if p.startswith(SALES))
    paths = {"tree": tree.sales, "base": tree.base or ()}.get(det.where, ())
    return any(re.search(det.pattern, path) for path in paths)


def judge(waves: list[Wave], tree: Tree) -> list[Finding]:
    """Волна нарушена, когда её предел наступил, а она не развёрнута и не записана."""
    found: list[Finding] = []
    for wave in waves:
        if wave.state == "pending":
            found += _pending(wave, tree)
        elif wave.state == "deployed":
            found += _evidence(wave, tree)
    return found


def _pending(wave: Wave, tree: Tree) -> list[Finding]:
    hits = [t for t in wave.triggers if fires(t, tree)]
    own = wave.limit == "this-pr"
    reached = bool(hits) if own else fires(wave.limit, tree)
    if reached:
        return [bad("pending", name=wave.name, what=DETECTORS[hits[0] if own else wave.limit].says)]
    limit = DETECTORS[wave.limit]
    if hits:
        return [note(MSG["ahead"].format(name=wave.name, limit=limit.ahead or limit.says))]
    later = [d for d in (*wave.triggers, wave.limit) if DETECTORS[d].where == "later"]
    if later and tree.sales:
        return [note(MSG["later"].format(name=wave.name, what=DETECTORS[later[0]].says))]
    return []


def _evidence(wave: Wave, tree: Tree) -> list[Finding]:
    """Развёрнутая волна предъявляет улику, а не только слово deployed."""
    paths = evidence_paths(wave.evidence)
    found = [bad("proof", name=wave.name, path=p) for p in paths if p not in tree.exists]
    where, holds, what = EVIDENCE.get(wave.name, ("", None, ""))
    texts = [t for p, t in tree.texts.items() if p.startswith(where)]
    if holds and not any(holds(t) for t in texts):
        found.append(bad("rule", name=wave.name, what=what))
    if "sales-prompt" in wave.triggers:
        # Промпт назван, если его покрывает элемент поля: путь, каталог или маска.
        surfaces, _ = declared_surfaces(tree.texts.get(STATUS, ""), "model_surface")
        prompts = [p for p in tree.sales if re.search(DETECTORS["sales-prompt"].pattern, p)]
        found += [
            bad("prompt", file=PurePosixPath(p).name)
            for p in prompts
            if not runtime_touched([p], surfaces)
        ]
    return found


# --- Ядро: объявления среза ---------------------------------------------------


def declarations(tree: Tree, slug: str) -> list[Finding]:
    """Объявления, без которых срез уронит CI или пройдёт мимо уроков."""
    found: list[Finding] = []
    declared = [field(tree.texts.get(doc, ""), "stack-selftest") for doc in (ACCEPTANCE, STATUS)]
    if not any(value and not value.startswith(("<", "…")) for value in declared):
        found.append(bad("selftest"))
    if slug.startswith("sales-"):
        found += _lessons(slug, tree.texts.get(TASKS, ""))
        found += _shared(field(tree.texts.get(STATUS, ""), "shared_changes"), tree)
    return found


def _lessons(slug: str, tasks: str) -> list[Finding]:
    """Раздел «Уроки» с пунктами. Заголовок без пунктов — шаблон, а не чтение."""
    section = re.search(r"(?ms)^(?:#+[ \t]*|\*\*)Уроки\b.*?$(.*?)(?=^#|\Z)", tasks)
    if section and re.search(r"(?m)^[ \t]*[-*][ \t]+[^\s…<]", section.group(1)):
        return []
    return [bad("lessons", slug=slug)]


def _shared(declared: str, tree: Tree) -> list[Finding]:
    """Срез продаж трогает общий код только с объявлением и согласием (A13)."""
    shared = [p for p in tree.changed or () if not any(fnmatchcase(p, m) for m in OWN_PATHS)]
    short = {p: p.removeprefix("backend/features/") for p in shared}
    if unnamed := [s for p, s in short.items() if p not in declared and s not in declared]:
        return [bad("shared", files=", ".join(unnamed))]
    if shared and not re.search(r"(?i)согласовано\s*:?\s*\S", declared):
        return [bad("agreed")]
    return []


def canon_ahead(acceptance: str, canon: dict[str, str]) -> list[Finding]:
    """Канон рядом ушёл вперёд от записи в STACK-ACCEPTANCE (A11, только pre-push)."""
    line = re.search(r"(?m)^\**Stack:\**\s*(.+)$", acceptance)
    recorded = dict(re.findall(r"([a-z-]+)@(\d+(?:\.\d+)*)", line.group(1) if line else ""))
    ahead = [f"{layer} {recorded[layer]} → {version}" for layer, version in sorted(canon.items())
             if layer in recorded and _version(version) > _version(recorded[layer])]  # fmt: skip
    return [bad("canon", layers=", ".join(ahead))] if ahead else []


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split(".") if part.isdigit())


def render(findings: list[Finding], warn: bool) -> tuple[list[str], int]:
    """Строки печати и код выхода. Режим меняет пометку и код, а не содержание."""
    count = sum(1 for is_bad, _ in findings if is_bad)
    mark = "⚠ предупреждение: " if warn else "✗ "
    lines = [(mark if is_bad else "○ ") + text for is_bad, text in findings]
    verdict = "clean" if not count else "warned" if warn else "failed"
    return [*lines, MSG[verdict].format(count=count)], int(count > 0 and not warn)


# --- Вход: файлы и git ----------------------------------------------------------


def _read(path: Path) -> str | None:
    return path.read_text(encoding="utf-8") if path.is_file() else None


def _git(root: Path, *args: str) -> str | None:
    """Вывод git или None — с причиной в stderr, а не молча."""
    git = shutil.which("git")
    if git is None:
        print("contour-waves: git не найден — база PR не прочитана", file=sys.stderr)
        return None
    try:
        # Команда собрана здесь целиком, снаружи приходит только имя ревизии.
        done = subprocess.run(  # noqa: S603 — фиксированная команда, путь к git разрешён
            [git, "-C", str(root), *args], capture_output=True, check=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError) as exc:
        why = getattr(exc, "stderr", "") or exc
        print(MSG["git"].format(cmd=args[0], why=why), file=sys.stderr)
        return None
    return done.stdout


def collect(root: Path, base: str | None, waves: list[Wave]) -> Tree:
    """Единственное место, где читаются файлы и спрашивается git."""
    folder = root / SALES
    found = folder.rglob("*") if folder.is_dir() else ()
    sales = tuple(sorted(p.relative_to(root).as_posix() for p in found
                         if p.is_file() and "__pycache__" not in p.parts))  # fmt: skip
    wanted = [STATUS, TASKS, ACCEPTANCE, ".importlinter", *(p for p in sales if p.endswith(".py"))]
    wanted += [p.relative_to(root).as_posix() for p in sorted((root / "okf").glob("*.md"))]
    texts = {rel: text for rel in wanted if (text := _read(root / rel)) is not None}
    exists = frozenset(p for w in waves for p in evidence_paths(w.evidence) if (root / p).exists())
    listed = _git(root, "ls-tree", "-r", "-z", "--name-only", base, "--", SALES) if base else None
    diff = ("diff", "-z", "--name-only", f"{base}...HEAD")
    changed = _git(root, *diff) if listed is not None else None
    if listed is None or changed is None:
        return Tree(sales, texts, exists)
    split = (tuple(filter(None, out.split("\0"))) for out in (listed, changed))
    return Tree(sales, texts, exists, *split)


def read_canon(folder: Path) -> dict[str, str] | None:
    """Версии канона, лежащего рядом; None — канона рядом нет."""
    if not folder.is_dir():
        return None
    pattern = r"\*\*Canon version:\*\*\s*`{}@([\d.]+)`"
    found = {layer: re.search(pattern.format(layer), _read(folder / name) or "")
             for layer, name in CANON_FILES.items()}  # fmt: skip
    return {layer: match.group(1) for layer, match in found.items() if match}


def _args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Проверка волн контура модуля «Продажи».")
    canon = Path.home() / "Documents" / "Prepare"
    parser.add_argument("--base", help="база PR: CI — цель PR или before, pre-push — origin/main")
    parser.add_argument("--warn", action="store_true", help="pre-push: нарушение — предупреждение")
    parser.add_argument("--root", type=Path, default=ROOT, help="корень дерева")
    parser.add_argument("--canon", type=Path, default=canon, help="канон рядом, только для --warn")
    return parser.parse_args(argv)


def sales_work(tree: Tree, slug: str) -> bool:
    """PR несёт работу продаж. Нет базы — чей PR, не узнать, и судим строго."""
    marks = (*WORK_PATHS, "delivery/active/*") if slug.startswith("sales-") else WORK_PATHS
    return tree.changed is None or any(fnmatchcase(p, m) for p in tree.changed for m in marks)


def findings(
    has_registry: bool, errors: list[str], waves: list[Wave], tree: Tree
) -> tuple[list[Finding], bool]:
    """Находки дерева (кроме сверки с каноном рядом) и строгий ли это PR."""
    slug = (field(tree.texts.get(STATUS, ""), "slug").split() or [""])[0]
    strict = sales_work(tree, slug)
    found: list[Finding] = [(True, error) for error in errors]
    if not has_registry:
        found.append(bad("no-registry") if tree.sales else note("реестра волн нет, продаж нет"))
    if tree.base is None and (tree.sales or slug.startswith("sales-")):
        found.append(bad("no-base"))
    found += judge(waves, tree) + declarations(tree, slug if strict else "")
    return found + ([] if strict else [note(MSG["neighbour"])]), strict


def _canon(args: argparse.Namespace, tree: Tree) -> list[Finding]:
    """Канон рядом сверяется только на pre-push: в CI его нет (A11)."""
    if not args.warn:
        return []
    canon = read_canon(args.canon)
    if canon is None:
        return [note(f"канона рядом нет ({args.canon}) — сверка версий пропущена")]
    return canon_ahead(tree.texts.get(ACCEPTANCE, ""), canon)


def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    root = args.root.resolve()
    registry = _read(root / REGISTRY)
    waves, errors = parse_registry(registry) if registry is not None else ([], [])
    tree = collect(root, args.base, waves)
    found, strict = findings(registry is not None, errors, waves, tree)
    lines, code = render(found + _canon(args, tree), args.warn or not strict)
    mode = "pre-push — нарушение только предупреждение" if args.warn else "CI — нарушение красное"
    states = " · ".join(f"{w.name} {w.state}" for w in waves) or "—"
    sales = f"в дереве продаж: {len(tree.sales)} файл(ов)" if tree.sales else MSG["no-sales"]
    print("\n".join([f"contour-waves: {mode}", f"волны: {states}", sales, *lines]))
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
