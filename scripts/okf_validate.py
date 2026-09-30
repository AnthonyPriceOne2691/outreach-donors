#!/usr/bin/env python3
"""OKF bundle soft validator (project profile + SPEC §11 subset).

Usage:
  python scripts/okf_validate.py                 # bundle=knowledge/
  python scripts/okf_validate.py path/to/bundle
  OKF_MAX_LINES=600 python scripts/okf_validate.py

Exit 0 = no errors (warnings allowed). Exit 1 = conformance errors.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

RESERVED = {"index.md", "log.md"}
FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
# `[ \t]`, а НЕ `\s`: `\s` включает перевод строки, и на пустом `type:` регулярка
# уходила на следующую строку — `type:` + `title: t` давало type == "title: t".
# Валидатор печатал `0 error(s)` там, где SPEC §11 требует непустой type: ложное
# зелёное. Найдено дописыванием регрессии, а не прогоном (тест на пустой тип был
# первым, который вообще это спросил).
TYPE_RE = re.compile(r"(?m)^type:[ \t]*(.+?)[ \t]*$")


def parse_frontmatter(text: str) -> tuple[dict[str, str] | None, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return None, text
    raw = m.group(1)
    meta: dict[str, str] = {}
    # minimal: only need type; ignore nested YAML complexity
    tm = TYPE_RE.search(raw)
    if tm:
        meta["type"] = tm.group(1).strip().strip("\"'")
    meta["_raw"] = raw
    return meta, text[m.end() :]


def check_body_smells(rel: str, path: Path, body: str,
                      warnings: list[str]) -> None:
    """Мягкие пределы concept'а: строк в теле и байт в файле (§3.3, атомарность).

    Отдельный шов, потому что это единственный читатель порогов из окружения:
    пока они жили в `validate_bundle`, каждый следующий разрез тащил бы их
    через ещё одну сигнатуру. Замер: −4 к цикломатике `check_file`.
    """
    max_lines = int(os.environ.get("OKF_MAX_LINES", "600"))
    max_bytes = int(os.environ.get("OKF_MAX_BYTES", str(80 * 1024)))
    lines = body.count("\n") + (1 if body and not body.endswith("\n") else 0)
    size = path.stat().st_size
    if lines > max_lines:
        warnings.append(f"{rel}: body ~{lines} lines > soft max {max_lines} (split?)")
    if size > max_bytes:
        warnings.append(f"{rel}: {size} bytes > soft max {max_bytes} (split?)")


def check_links(rel: str, root: Path, body: str, warnings: list[str]) -> None:
    """Bundle-абсолютные ссылки ведут в существующий файл (SPEC §6, soft).

    Шов здесь потому, что это единственная проверка, которой нужен КОРЕНЬ
    bundle'а рядом с телом файла, и единственная, что кладёт цикл внутрь цикла:
    вынос забирает у `check_file` сразу два ветвления.
    """
    for link in re.findall(r"\[[^\]]*\]\((/[^)]+?\.md)\)", body):
        if not (root / link.lstrip("/")).is_file():
            warnings.append(f"{rel}: broken bundle link {link}")


def check_file(path: Path, root: Path, errors: list[str],
               warnings: list[str]) -> None:
    """Один markdown bundle'а: кодировка, зарезервированное имя, frontmatter, type.

    Шов по потоку управления: каждый `continue` тела цикла означал «по этому
    файлу всё» — и стал ранним `return` помощника, поэтому смысл через границу
    не изменился, а решение «идти ли дальше» осталось внутри одного файла.
    Свободными в блоке `ast` называл ровно `path`, `root`, `errors`, `warnings`
    (пороги ушли в `check_body_smells` — там их единственный читатель).
    """
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        errors.append(f"{rel}: not UTF-8")
        return

    if path.name in RESERVED:
        if path.name == "log.md" and not re.search(
                r"(?m)^## \d{4}-\d{2}-\d{2}\s*$", text):
            warnings.append(f"{rel}: no ## YYYY-MM-DD headings (SPEC §9 shape)")
        return

    meta, body = parse_frontmatter(text)
    if meta is None:
        errors.append(f"{rel}: missing YAML frontmatter (SPEC §11)")
        return
    if not meta.get("type"):
        errors.append(f"{rel}: frontmatter missing non-empty type (SPEC §11)")

    check_body_smells(rel, path, body, warnings)
    check_links(rel, root, body, warnings)


def check_root_index(root: Path, warnings: list[str]) -> None:
    """Корень bundle'а: `index.md` существует и называет `okf_version` (SPEC §12).

    Шов проведён по данным: блок судил ОДИН файл и отдавал наружу только строки
    `warnings` — `ast` показывает свободными ровно `root` и `warnings`, а имя
    `text` из него ниже никто не читал (в цикле оно связывалось заново). Ветка
    «файла нет» восстановлена ранним `return`, чтобы охрана условия осталась
    частью блока, а не превратилась во вложенность.
    """
    root_index = root / "index.md"
    if not root_index.is_file():
        warnings.append(
            "missing root index.md (optional in SPEC, required by project profile)")
        return
    if "okf_version" not in root_index.read_text(encoding="utf-8"):
        warnings.append("root index.md has no okf_version (SPEC §12 recommended)")


def validate_bundle(root: Path) -> int:
    errors: list[str] = []
    warnings: list[str] = []

    if not root.is_dir():
        print(f"ERROR: bundle root not a directory: {root}", file=sys.stderr)
        return 1

    md_files = sorted(root.rglob("*.md"))
    if not md_files:
        errors.append(f"no markdown files under {root}")

    check_root_index(root, warnings)

    for path in md_files:
        check_file(path, root, errors, warnings)

    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)

    print(
        f"okf_validate: {len(errors)} error(s), {len(warnings)} warning(s), "
        f"files={len(md_files)}, root={root}"
    )
    return 1 if errors else 0


def main() -> int:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "knowledge")
    return validate_bundle(root.resolve())


if __name__ == "__main__":
    sys.exit(main())
