#!/usr/bin/env python3
"""Delivery harness checker.

Usage:
  python scripts/delivery_check.py
  python scripts/delivery_check.py --require-spec

Exit 0 = OK (warnings allowed). Exit 1 = errors.
"""


from __future__ import annotations

import argparse
import re
import sys
from datetime import date

from delivery_base import (ACTIVE, ARCHIVE, DELIVERY, ROOT,
                           field,
                           is_placeholder, read, read_json)
from delivery_decisions import (permission_block)
from delivery_evidence import check_evidence
from delivery_journals import check_journals
from delivery_limits import check_limits
from delivery_status import check_status_shape

def check_archive_index(args, errors: list[str], warnings: list[str]) -> None:
    """Индекс архива: полнота и просроченное наблюдение (§2.2a, §13.1).

    Шов `main` (`delivery@1.59`). Границы заданы ТЕКСТОМ первой и следующей
    строки, а не маркером: в `main` маркеры стоят на разных отступах и
    внутри веток, поэтому «до следующего маркера» здесь неверно.
    """
    # --- §2.2a: архив без индекса не читается, а отставший индекс врёт о полноте.
    # Обе проверки не зависят от фазы и от наличия diff-base: они про сам архив.
    if ARCHIVE.is_dir():
        # Каталог-ШАБЛОН пропускается, и это не поблажка: канон сам
        # поставляет `archive/<slug>/observed.md` как образец записи
        # наблюдения (§13). Без пропуска `delivery_check` краснеет сразу
        # после развёртывания на ЧИСТОМ проекте — гейт объявляет
        # неиндексированной поставкой файл, который положил сам канон.
        # Найдено развёртыванием в пустой проект (2026-08-17): тот же
        # класс, что `cqg@2.16` — канон не попадает в раскладку, которую
        # сам предписывает.
        shipments = sorted(d.name for d in ARCHIVE.iterdir()
                           if d.is_dir() and not d.name.startswith("<"))
        idx_text = read(ARCHIVE / "INDEX.md")
        if shipments and not idx_text:
            errors.append(
                f"delivery/archive/ содержит {len(shipments)} поставок, а "
                "INDEX.md нет (§2.2a / A.13) — неиндексированный архив никем "
                "не читается"
            )
        elif idx_text:
            unlisted = [s for s in shipments if s not in idx_text]
            if unlisted:
                errors.append(
                    "archive/INDEX.md отстал от архива: не упомянуты "
                    f"{', '.join(unlisted)} (§2.2a) — индекс, который врёт о "
                    "своей полноте, хуже отсутствующего"
                )

        check_overdue_observation(shipments, errors)


def check_permissions(errors: list[str], warnings: list[str]) -> None:
    """Права на действия: объявлено ↔ подключено (§4.5) и песочницы (§4.6).
    """
    # --- §4.5: права на действия. Объявлено (CONSTITUTION) ↔ подключено (настройки).
    settings_path = ROOT / ".claude" / "settings.json"
    declared = permission_block(read(DELIVERY / "CONSTITUTION.md"))
    settings, s_err = read_json(settings_path)
    if s_err:
        errors.append(
            f".claude/settings.json: не парсится ({s_err}) — права агента "
            "не проверяемы (§4.5)"
        )
    perms = settings.get("permissions") or {}
    wired = {k: list(perms.get(k) or []) for k in ("deny", "ask")}

    check_declared_vs_wired(declared, settings_path, s_err, wired, errors, warnings)

    check_sandbox_oracles(declared, errors)
    local, _ = read_json(ROOT / ".claude" / "settings.local.json")
    n_allow = len(perms.get("allow") or []) + len(
        (local.get("permissions") or {}).get("allow") or []
    )
    if n_allow >= 50 and not wired["deny"]:
        warnings.append(
            f"permissions: {n_allow} правил в allow и ни одного в deny (§4.5) — "
            "права накапливались кликами «yes» и ни разу не сужались"
        )


def check_sandbox_oracles(declared, errors: list[str]) -> None:
    """Перевод из HITL в автономию называет СУЩЕСТВУЮЩИЙ оракул (§4.6).

    Шов по СЛОЖНОСТИ (`delivery@1.59`): `check_permissions` уложилась в 77
    строк при cx 23, а §2.1 меряет обе оси.
    """
    # --- §4.6: перевод из HITL в автономию обязан назвать существующий оракул.
    # Объявленная песочница без скрипта — снятая проверка с видом усиления;
    # класс тот же, что «скрипт лежал в репозитории, никто не вызывал» (§3.1a).
    for line in (declared or {}).get("sandbox", []):
        m_or = re.search(r"oracle=([^\s]+)", line)
        if not m_or:
            errors.append(
                f"sandbox без oracle=: {line[:60]} (§4.6) — перевод из HITL "
                "в автономию обязан назвать оракул, заменивший человека"
            )
        elif not (ROOT / m_or.group(1)).exists():
            errors.append(
                f"sandbox: oracle={m_or.group(1)} не существует (§4.6) — "
                "объявленная песочница без скрипта это снятая проверка "
                "с видом усиления"
            )

    # Ратчет прав: длинный allow при пустом deny = права росли только вверх.
    # Считаем и локальные настройки — именно там оседают клики «yes».


def check_declared_vs_wired(declared, settings_path, s_err, wired,
                            errors: list[str], warnings: list[str]) -> None:
    """Объявленные права ↔ подключённые в настройках (§4.5).
    """
    if declared is None:
        msg = (
            "CONSTITUTION.md: нет блока `agent-permissions` (§4.5 / A.1) — контур "
            "контролирует выход и молчит про действия агента"
        )
        if any(wired.values()):
            errors.append(
                msg + "; при этом deny/ask в настройках заданы, то есть границы "
                "действий живут без ревью"
            )
        else:
            warnings.append(msg)
    elif not settings_path.is_file():
        errors.append(
            "CONSTITUTION.md объявляет agent-permissions, а .claude/settings.json "
            "нет (§4.5) — объявленный неработающий запрет хуже отсутствующего: "
            "он выглядит как контроль"
        )
    elif not s_err:
        for bucket in ("deny", "ask"):
            only_canon = [r for r in declared[bucket] if r not in wired[bucket]]
            only_wired = [r for r in wired[bucket] if r not in declared[bucket]]
            if only_canon:
                errors.append(
                    f"permissions.{bucket}: объявлено в CONSTITUTION, нет в "
                    f".claude/settings.json: {', '.join(only_canon)} (§4.5)"
                )
            if only_wired:
                errors.append(
                    f"permissions.{bucket}: есть в .claude/settings.json, нет в "
                    f"CONSTITUTION: {', '.join(only_wired)} (§4.5) — границу "
                    "действий двигали молча"
                )


def check_preconditions(args, errors: list[str], warnings: list[str]) -> bool:
    """Каркас на месте — иначе проверять дальше нечего.

    ⚠ Возвращает ПРОДОЛЖАТЬ ЛИ, а не данные, и это не стиль. Из блока убегало не
    имя, а ПОТОК УПРАВЛЕНИЯ: внутри стоял `return 1` — досрочный выход из `main`.
    Механический вырез унёс его в помощника, где он значил «проверка прошла», и
    75 тестов упали (`delivery@1.59`). Новый класс к процедуре резки: смотреть
    надо не только на имена, но и на `return`/`break`/`continue`, пересекающие
    границу шва.
    """
    if not (DELIVERY / "CONSTITUTION.md").is_file():
        errors.append("missing delivery/CONSTITUTION.md")
    if not ACTIVE.is_dir():
        errors.append("missing delivery/active/")
        for w in warnings:
            print(f"WARNING: {w}")
        for e in errors:
            print(f"ERROR: {e}", file=sys.stderr)
        print(f"delivery_check: {len(errors)} error(s), {len(warnings)} warning(s)")
        return False

    status = read(ACTIVE / "STATUS.md")
    if not status:
        # Пустой active/ — ШТАТНОЕ состояние (§2.3a): предыдущая поставка
        # заархивирована, следующая не начата. Ошибкой это быть не может, иначе
        # контур красный между поставками — причём §13.1 сам загоняет проект в
        # это состояние, требуя архивации на handoff (полевая находка F12).
        # Проверки уровня архива ниже продолжают работать: именно в idle всплывает
        # просроченное наблюдение.
        warnings.append(
            "idle: нет delivery/active/STATUS.md — активной поставки нет (§2.3a). "
            "Начинаешь работу? Создай STATUS по шаблону A.2"
        )
    else:
        ctx = check_status_shape(status, args, errors, warnings)
        check_evidence(status, args, errors, warnings, ctx)
        check_journals(status, args, errors, warnings, ctx)
        check_limits(status, args, errors, warnings, ctx)
    return True


def parse_args():
    """Разбор аргументов — отдельно, чтобы `main` остался диспетчером."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--require-spec", action="store_true")
    ap.add_argument("--require-verify", action="store_true")
    ap.add_argument(
        "--require-ci",
        action="store_true",
        help="fail unless ci-oracles: deployed (§10.4)",
    )
    ap.add_argument(
        "--diff-base",
        metavar="REF",
        help="check circuit breakers §3.4 against REF (e.g. origin/main)",
    )
    return ap.parse_args()


def check_overdue_observation(shipments, errors: list[str]) -> None:
    """Просроченное наблюдение по индексу архива (§13.1).

    Шов по сложности (`delivery@1.59`): `check_archive_index` давала cx 14.
    """
    # --- §13.1: просроченное наблюдение. Лаг в одну поставку — проверка
    # срабатывает тогда, когда человек всё равно смотрит в PR. Дыра названа
    # в каноне: если следующей поставки не будет, никто не проверит; на это
    # тот же периодический прогон, что okf_sync_gate --check-stale.
    today = date.today().isoformat()
    for name in shipments:
        arch_status = read(ARCHIVE / name / "STATUS.md")
        if not arch_status:
            continue
        until = field(arch_status, "observe_until")
        if is_placeholder(until):
            continue
        due = re.match(r"(\d{4}-\d{2}-\d{2})", until)
        if not due or due.group(1) > today:
            continue
        if not (ARCHIVE / name / "observed.md").is_file():
            errors.append(
                f"наблюдение просрочено: {name} — observe_until={due.group(1)} "
                f"(сегодня {today}), а observed.md нет (§13.1 / A.17). "
                "Проверь сигнал в проде и закрой наблюдение, либо сдвинь срок "
                "с причиной"
            )


def check_layer_pointers(warnings: list[str]) -> None:
    """Соседние слои развёрнуты — CONSTITUTION обязан на них указывать."""
    if (ROOT / "CODE_QUALITY_GATES.md").is_file():
        cons = read(DELIVERY / "CONSTITUTION.md")
        if "CODE_QUALITY_GATES" not in cons:
            warnings.append("CQG present but CONSTITUTION.md has no pointer")
    if (ROOT / "OKF_KNOWLEDGE_BUNDLE.md").is_file() or (ROOT / "knowledge").is_dir():
        cons = read(DELIVERY / "CONSTITUTION.md")
        if "OKF" not in cons and "knowledge/" not in cons:
            warnings.append("OKF/knowledge present but CONSTITUTION.md has no pointer")


def main() -> int:
    args = parse_args()

    errors: list[str] = []
    warnings: list[str] = []

    if not check_preconditions(args, errors, warnings):
        return 1

    check_archive_index(args, errors, warnings)

    if args.require_spec and not (ACTIVE / "spec.md").is_file():
        errors.append("--require-spec: active/spec.md missing")
    if args.require_verify and not (ACTIVE / "verify-report.md").is_file():
        errors.append("--require-verify: verify-report.md missing")

    check_permissions(errors, warnings)

    check_layer_pointers(warnings)

    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}", file=sys.stderr)
    print(f"delivery_check: {len(errors)} error(s), {len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
