#!/usr/bin/env python3
"""Delivery metrics collector (§9 / A.10).

Usage:
  python scripts/delivery_metrics.py --base origin/main
  python scripts/delivery_metrics.py --base origin/main --write

--write вставляет/обновляет блок "## Harness metrics" в
delivery/active/verify-report.md. Exit 0 всегда: это отчёт, не гейт
(гейт на наличие блока — в delivery_check.py на фазе handoff).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACTIVE = ROOT / "delivery" / "active"
STATUS = ACTIVE / "STATUS.md"
VERIFY = ACTIVE / "verify-report.md"

# Правка любого из этих путей = harness усилен (§5.4): новый oracle / hook / eval.
HARDENING_PATHS = (
    "scripts/lint/",
    "delivery/evals/",
    ".pre-commit-config.yaml",
    ".github/workflows/",
    # Оба хостинга: у контура гейт мержа чисто гитовый, а CI-контракт §10.4
    # описан шагами, поэтому путь конфига — свойство хостинга, не канона.
    ".gitlab-ci.yml",
    "scripts/delivery_check.py",
    "scripts/okf_sync_gate.py",
    # `.claude/` — механика контура (права агента §4.5), а не продуктовый код.
    # Без исключения маячок §12.5 срабатывал на классе «безопасность» из-за
    # слова `permission` в собственном файле настроек прав: контур ругался на
    # свою же машинерию. Найдено на первом прогоне маячка (арена lab-10).
    ".claude/",
)
# НОВЫЙ тест-файл — тоже усиление harness: по словарю §1.1 oracle это «гейты + тесты
# + smoke + фазовые артефакты», и §5.4 считает усилением «добавить oracle». Путей
# тестов в списке не было, поэтому поставка, добавившая два регрессионных оракула
# (включая мета-оракул «проверка, что защита подключена»), получала
# `harness_hardened: no` — а §9.2 по этой метрике советует «добавь oracle», то есть
# добавить уже добавленное. Нашло приёмочное развёртывание.
#
# Считаются только ДОБАВЛЕННЫЕ файлы (`--diff-filter=A`), а не любая правка тестов:
# иначе метрика стала бы всегда `yes` — тесты трогает каждая поставка — и перестала
# бы отвечать на свой вопрос.
# ⚠ Признак — ТОТ ЖЕ, что у §3.1e CQG, и это не совпадение, а одно понятие:
# подстроки промахивались на Maven (`src/test/java/FooTest.java`), Jest
# (`__tests__/foo.tsx`) и xUnit (`FooTests.cs`), то есть метрика снова
# советовала «добавь oracle» тому, кто его добавил, — уже второй раз.
# Согласие с четырьмя реализациями CQG держит `tests/test_what_is_a_test_file.py`.
TEST_PATH_RE = re.compile(
    r"(^|/)(test|tests|__tests__|spec|specs)/|(^|/)conftest\.py$|(^|/)test[_-]"
    r"|[_-](test|spec)\.|(Test|Tests|Spec|Specs)\.|\.(test|spec)\.")
DOC_PREFIXES = ("delivery/", "knowledge/")


def git(*args: str) -> str:
    try:
        out = subprocess.run(["git", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        return ""
    return out.stdout if out.returncode == 0 else ""


def first_commit(
    path: str, needle: str | None = None, rev_range: str | None = None
) -> tuple[str, datetime] | None:
    """Первый коммит, добавивший path (или впервые внёсший needle в path).

    needle — РЕГУЛЯРКА (pickaxe-regex), а не литерал: в STATUS.md поля размечены
    markdown-жирным (`**phase:** handoff`), поэтому поиск литерала "phase: handoff"
    не находил ничего и метрики молча выходили пустыми.

    ⚠ rev_range ОБЯЗАТЕЛЕН для путей внутри `delivery/active/`, и это не
    перестраховка. Путь **переиспользуется каждой поставкой**: `STATUS.md` и
    `spec.md` у всех одни и те же, поэтому «первый в истории» — это первая
    поставка репозитория, а не текущая. lab-12 (нашли ОБЕ арки независимо):
    `rework_after_done` печатал «3 commit(s) after first phase: handoff» на
    поставке, которая handoff не объявляла — счёт шёл от handoff'а bootstrap'а.
    Собственная проба нашла и брата, которого не увидел никто:
    `time_to_accepted_spec` выдавал `4.0d` там, где спека и подпись сделаны в
    один день, — число из ПРОШЛОЙ поставки, и оно выглядит правдоподобно, потому
    что это настоящее число, просто не про эту работу.

    Класс: **переиспользуемый путь + поиск по всей истории = чужой ответ с видом
    своего.** Родня `--diff-filter=A` тому же: «файл добавлен» тоже случилось один
    раз, в первой поставке.
    """
    args = ["log", "--reverse", "--format=%H %aI"]
    if rev_range:
        args.append(rev_range)
    if needle:
        args += [f"-S{needle}", "--pickaxe-regex"]
    else:
        args.append("--diff-filter=A")
    args += ["--", path]
    for line in git(*args).splitlines():
        sha, _, iso = line.partition(" ")
        if sha and iso:
            try:
                return sha, datetime.fromisoformat(iso.strip())
            except ValueError:
                return None
    return None


def hours_between(a: datetime, b: datetime) -> str:
    delta = (b - a).total_seconds() / 3600
    return f"{delta:.1f}h" if delta < 48 else f"{delta / 24:.1f}d"


def _diff_stats(span: str) -> tuple[str, list[str]]:
    """(строка `files_touched / loc_diff`, пути усиления) за один проход по diff'у.

    Шов по данным: наружу блок отдаёт ровно две величины — готовую строку метрики
    и список путей усиления; четыре счётчика за границей мертвы. Здесь же треть
    ветвлений `collect` (было 69/20).
    """
    code_files = doc_files = added = deleted = 0
    hardened: list[str] = []
    for line in git("diff", "--numstat", span).splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        a, d, path = parts
        if path.startswith(HARDENING_PATHS):
            hardened.append(path)
        if path.startswith(DOC_PREFIXES):
            doc_files += 1
            continue
        code_files += 1
        added += int(a) if a.isdigit() else 0
        deleted += int(d) if d.isdigit() else 0
    return (
        f"{code_files} code (+{doc_files} process docs) / "
        f"+{added}/-{deleted} (net {added - deleted:+d})",
        hardened,
    )


def _spec_timings(span: str) -> dict[str, str]:
    """Две метрики §9, обе — про смену фазы в истории `STATUS.md`.

    Шов по данным: сверху блок читает один `span`, вниз отдаёт две готовые
    строки — `spec_created`/`spec_ok`/`handoff` дальше не читает никто. Обе
    величины меряют одно и то же событие, поэтому и режутся вместе.
    """
    spec_created = first_commit("delivery/active/spec.md", rev_range=span)
    spec_ok = first_commit(
        "delivery/active/STATUS.md", r"human_ok_spec:\**[ \t]*yes", rev_range=span
    )
    if spec_created and spec_ok:
        accepted = hours_between(spec_created[1], spec_ok[1])
    elif spec_created:
        accepted = "spec drafted, not yet accepted"
    else:
        accepted = "n/a (no spec.md in history — class S?)"

    handoff = first_commit(
        "delivery/active/STATUS.md", r"phase:\**[ \t]*handoff", rev_range=span
    )
    if handoff:
        after = git("rev-list", "--count", f"{handoff[0]}..HEAD").strip() or "0"
        rework = f"{after} commit(s) after first phase: handoff"
    else:
        rework = "0 (handoff not declared yet)"
    return {"time_to_accepted_spec": accepted, "rework_after_done": rework}


def _hardened_line(span: str, hardened: list[str]) -> str:
    """Значение `harness_hardened`: пути из диффа плюс новые файлы-оракулы.

    Шов по данным: сюда входит список из `_diff_stats`, отсюда выходит строка
    метрики — пополненный список за границей не читает никто.
    """
    # Новые тест-файлы — отдельный источник усиления (см. TEST_PATH_RE).
    found = list(hardened)
    for line in git("diff", "--name-only", "--diff-filter=A", span).splitlines():
        path = line.strip()
        if path and TEST_PATH_RE.search(path):
            found.append(f"{path} (новый оракул)")
    return f"yes — {', '.join(sorted(set(found))[:4])}" if found else "no"


def collect(base: str) -> dict[str, str]:
    m: dict[str, str] = {}

    if not git("rev-parse", "--verify", "--quiet", base).strip():
        m["_error"] = f"ref '{base}' unavailable (shallow clone?)"
        return m
    merge_base = git("merge-base", base, "HEAD").strip() or base

    # Область — ТЕКУЩАЯ поставка, а не вся история: пути в `delivery/active/`
    # переиспользуются, и «первый в истории» отвечает про чужую работу (см.
    # docstring `first_commit`). §5.1 требует ветку на поставку, поэтому
    # `merge_base..HEAD` и есть её граница.
    span = f"{merge_base}..HEAD"

    m["files_touched / loc_diff"], hardened = _diff_stats(span)
    m["commits"] = git("rev-list", "--count", span).strip() or "0"
    m.update(_spec_timings(span))
    m["harness_hardened"] = _hardened_line(span, hardened)
    m["implement_retries"] = "MANUAL — fills from session log"
    m["verify_fails_before_green"] = "MANUAL — count red verify runs (CI run list)"
    m["est_token_or_cost"] = "MANUAL / n/a"
    return m


def render(m: dict[str, str], base: str) -> str:
    if "_error" in m:
        rows = f"| (collection failed) | {m['_error']} |"
    else:
        rows = "\n".join(f"| {k} | {v} |" for k, v in m.items())
    return (
        "## Harness metrics (this shipment)\n\n"
        f"<!-- generated by scripts/delivery_metrics.py --base {base} -->\n\n"
        "| Metric | Value |\n|---|---|\n"
        f"{rows}\n\n"
        "MANUAL-поля заполняет агент/человек на handoff. Если "
        "`verify_fails_before_green >= 2` при `harness_hardened: no` — по §9.2 "
        "добавь oracle/breaker/hook в этой же поставке.\n"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--write", action="store_true", help="upsert block into verify-report.md")
    args = ap.parse_args()

    block = render(collect(args.base), args.base)

    if not args.write:
        print(block)
        return 0

    if not ACTIVE.is_dir():
        print("no delivery/active/ — nothing to write", file=sys.stderr)
        return 0
    text = VERIFY.read_text(encoding="utf-8") if VERIFY.is_file() else "# Verify report\n\n"
    # Заменить существующий блок целиком, чтобы не копить дубли на повторных прогонах.
    pattern = re.compile(r"(?ms)^## Harness metrics.*?(?=^## |\Z)")
    text = pattern.sub("", text).rstrip() + "\n\n" + block
    VERIFY.write_text(text, encoding="utf-8")
    print(f"metrics written to {VERIFY.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
