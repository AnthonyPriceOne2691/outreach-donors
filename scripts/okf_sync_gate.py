#!/usr/bin/env python3
"""OKF code<->canon sync gate + freshness check.

Usage:
  python scripts/okf_sync_gate.py --base origin/main        # sync (PR-режим)
  python scripts/okf_sync_gate.py --staged                  # sync (pre-commit)
  python scripts/okf_sync_gate.py --check-stale             # freshness, без git
  OKF_BUNDLE=brain python scripts/okf_sync_gate.py --base origin/main

Exit 0 = OK (warnings allowed). Exit 1 = drift/staleness errors.

Waiver (осознанный дрейф — например, чистый рефакторинг без смены инварианта):
  ALLOW_CANON_DRIFT=1 python scripts/okf_sync_gate.py --base origin/main
Обязан быть виден и объяснён в PR. STRICT=0 — soft-режим (warning, exit 0).
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

BUNDLE = os.environ.get("OKF_BUNDLE", "knowledge")
STRICT = os.environ.get("STRICT", "1") != "0"
ALLOW_DRIFT = os.environ.get("ALLOW_CANON_DRIFT", "0") == "1"
RESERVED = {"index.md", "log.md"}
DELIVERY_STATUS = "delivery/active/STATUS.md"

FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


def git(*args: str) -> str:
    """git с подавлением ошибок: пустая строка = git не смог (не блокер сам по себе)."""
    try:
        out = subprocess.run(
            ["git", *args], capture_output=True, text=True, check=False
        )
    except FileNotFoundError:
        return ""
    return out.stdout if out.returncode == 0 else ""


def repo_root() -> Path:
    top = git("rev-parse", "--show-toplevel").strip()
    return Path(top) if top else Path.cwd()


def frontmatter(text: str) -> str:
    m = FRONTMATTER_RE.match(text)
    return m.group(1) if m else ""


def parse_list_field(fm: str, name: str) -> list[str] | None:
    """YAML-подмножество: "name:" + block-list, или "name: [a, b]", или "name: []".

    Возвращает None, если поля нет (concept невидим для гейта), и [] если поле
    объявлено пустым (сознательно не привязан к коду).
    """
    inline = re.search(rf"(?m)^{name}:[ \t]*\[(.*?)\][ \t]*$", fm)
    if inline:
        items = [i.strip().strip("\"'") for i in inline.group(1).split(",")]
        return [i for i in items if i]
    block = re.search(rf"(?m)^{name}:[ \t]*$", fm)
    if not block:
        return None
    out: list[str] = []
    for line in fm[block.end() :].splitlines():
        if re.match(r"^[ \t]*-[ \t]*", line):
            out.append(re.sub(r"^[ \t]*-[ \t]*", "", line).strip().strip("\"'"))
        elif line.strip() and not line.startswith((" ", "\t")):
            break  # началось следующее поле верхнего уровня
    return out


def scalar_field(fm: str, name: str) -> str:
    m = re.search(rf"(?m)^{name}:[ \t]*(.+?)[ \t]*$", fm)
    return m.group(1).strip().strip("\"'") if m else ""


def status_waiver(root: Path) -> str:
    """Строка `canon_drift_waiver:` из STATUS активной поставки ("" если нет).

    Waiver обязан жить там, где идёт ревью. env-переменная для этого не годится:
    в CI её иначе как правкой workflow не задать — то есть НАВСЕГДА, а локально
    она не оставляет следа в диффе, и ревьюер обхода не видит. Строка в STATUS
    попадает в дифф, видна в PR и умирает вместе с поставкой (уходит в archive).

    Зачем waiver вообще нужен: гейт видит «файл тронут», а не «инвариант изменён».
    Типизация, формат, логи, переименования трогают код под `implementation:`, не
    меняя смысла. Правка concept'а в таком PR была бы ЛОЖНЫМ «обновлено»: свежий
    `generated.at` создаёт вид пересмотра канона, которого не было.
    """
    p = root / DELIVERY_STATUS
    if not p.is_file():
        return ""
    m = re.search(
        r"(?im)^[ \t]*[-*]?[ \t]*\**canon_drift_waiver\**[ \t]*:\**[ \t]*(.*)$",
        p.read_text(encoding="utf-8"),
    )
    if not m:
        return ""
    val = re.sub(r"<!--.*?-->", "", m.group(1)).strip()
    if not val or val.lower() in {"no", "none", "-", "…"} or val.startswith("<"):
        return ""
    return val


def covers(declared: str, changed: str) -> bool:
    """Совпадение пути: точное, префикс каталога или glob-хвост /**."""
    d = declared.strip().lstrip("./").rstrip()
    if d.endswith("/**"):
        d = d[:-3]
    if d.endswith("/*"):
        d = d[:-2]
    d = d.rstrip("/")
    if not d:
        return False
    return changed == d or changed.startswith(d + "/")


def changed_files(base: str | None, staged: bool) -> tuple[list[str], list[str]]:
    """(файлы, проблемы). Пустой список файлов при проблеме = гейт не судит."""
    if staged:
        out = git("diff", "--name-only", "--cached")
        if not out.strip():
            return [], ["nothing staged"]
        return [f for f in out.splitlines() if f], []
    if not base:
        return [], ["no --base and no --staged"]
    if not git("rev-parse", "--verify", "--quiet", base).strip():
        return [], [f"ref '{base}' unavailable (shallow clone? need full history)"]
    merge_base = git("merge-base", base, "HEAD").strip() or base
    out = git("diff", "--name-only", f"{merge_base}..HEAD")
    return [f for f in out.splitlines() if f], []


def collect_concepts(root, bundle) -> tuple[dict, list]:
    """Карта `concept → объявленные пути` и список просроченных.

    Шов `main` (`okf@1.13`): блок только СОБИРАЕТ данные и ничего не судит,
    поэтому отделяется чисто и возвращает ровно две структуры.
    """
    concepts: dict[str, list[str]] = {}
    stale: list[tuple[str, str]] = []
    today = date.today()
    for path in sorted(bundle.rglob("*.md")):
        if path.name in RESERVED:
            continue
        rel = path.relative_to(root).as_posix()
        fm = frontmatter(path.read_text(encoding="utf-8"))
        if not fm:
            continue
        declared = parse_list_field(fm, "implementation")
        if declared:
            concepts[rel] = declared
        after = scalar_field(fm, "stale_after")
        m = DATE_RE.search(after) if after else None
        if m:
            when = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if when < today:
                stale.append((rel, after))
    return concepts, stale


def check_concept_sync(files, concepts: dict, errors: list[str],
                       warnings: list[str]) -> None:
    """Код тронут, а concept — нет: рассинхрон знания и реализации (§7.2).
    """
    if files:
        touched_bundle = {f for f in files if f.startswith(f"{BUNDLE}/")}
        code = [f for f in files if f not in touched_bundle]
        for rel, declared in sorted(concepts.items()):
            if rel in touched_bundle:
                continue  # concept обновлён — синхронизация заявлена
            hits = sorted(
                {c for c in code for d in declared if covers(d, c)}
            )[:5]
            if hits:
                errors.append(
                    f"{rel}: implementation changed but concept untouched -> "
                    f"{', '.join(hits)}"
                )
        if not concepts:
            warnings.append(
                "no concept declares implementation: — gate is inert; "
                "start filling the field (Приложение A.3)"
            )


def refuse(refusals: list[str]) -> int:
    """Прибор не судил — и это НЕ покрывается ничем (`okf@1.18`, поле).

    Отказ судить и расхождение концепта лежали в одном списке, а ветвь waiver'а
    в `report` стоит ДО всякой классификации и возвращает 0 на любом его
    элементе. Следствие замерено на `local-web-agent` 2026-09-26: push в main
    с пустым диффом был зелёным, потому что в STATUS лежал `canon_drift_waiver`
    ЧУЖОЙ поставки от 17.08 — разрешение на drift концепта погасило сообщение
    о том, что суда не было вовсе.

    Разрешение снимает правило, ради которого написано, и только его. Здесь
    правило другое: «гейт, вышедший 0 и не посмотревший ни одного файла, хуже
    отсутствующего» (Delivery §3.1a). Отменить его waiver'ом нельзя — иначе
    строка в STATUS выключает не проверку, а САМ ПРИБОР, и выключает молча.
    Поэтому ветвь стоит выше waiver'а, `ALLOW_CANON_DRIFT` и `STRICT=0`.
    """
    print(
        f"okf_sync_gate: FAIL — прибор не судил ({len(refusals)} причин(ы)).\n"
        "Это НЕ расхождение concept'а с кодом, и waiver его не покрывает:\n"
        "  • на push в main база, равная HEAD, даёт пустой дифф — бери\n"
        "    `github.event.before` (CQG §8, шаг «Diff base»);\n"
        "  • прогон до коммита — коммить и повтори;\n"
        "  • нет базы вовсе — передай --base <ref> или --staged.\n"
        "Строка `canon_drift_waiver:` разрешает drift concept'а и не может\n"
        "разрешить отказ гейта работать (§4.3a).",
        file=sys.stderr,
    )
    return 1


def soften(args, root, errors: list[str]) -> int | None:
    """Разрешения, снимающие расхождение concept'а с кодом. `None` — не сняли.

    Шов `okf@1.18`: три ступени смягчения (waiver из STATUS, env-обход,
    `STRICT=0`) вынесены из `report` целиком. Причина не в длине — в том, что
    ступени эти про ОДИН предмет: «расхождение есть, и человек его разрешил».
    Пока они стояли вперемешку с печатью итога, к ним по соседству прилип
    четвёртый случай — отказ прибора судить, — и waiver начал гасить его тоже
    (см. `refuse`). Отдельная функция делает предмет разрешения видимым: всё,
    что она смягчает, приходит одним аргументом `errors`.
    """
    # Waiver из STATUS — основной механизм: виден в диффе, живёт одну поставку.
    # На --check-stale не действует: просроченный concept — это не «код тронут без
    # смены смысла», а отдельная проблема (§7.2).
    if not args.check_stale:
        waiver = status_waiver(root)
        if waiver:
            print(
                f"okf_sync_gate: drift разрешён waiver'ом из {DELIVERY_STATUS}: "
                f"{waiver}\n"
                f"({len(errors)} concept(s) не тронуты — waiver виден в PR и умрёт "
                f"с поставкой)"
            )
            return 0

    if ALLOW_DRIFT:
        print(
            f"okf_sync_gate: drift allowed by ALLOW_CANON_DRIFT=1 "
            f"({len(errors)} finding(s)).\n"
            f"⚠ env-обход НЕ виден ревьюеру и в CI задаётся только правкой workflow "
            f"(то есть навсегда). Предпочитай строку `canon_drift_waiver:` в "
            f"{DELIVERY_STATUS}.",
            file=sys.stderr,
        )
        return 0
    if not STRICT:
        print(f"okf_sync_gate: WARNING (STRICT=0) — {len(errors)} finding(s)", file=sys.stderr)
        return 0
    return None


def report(args, root, concepts: dict, errors: list[str],
           warnings: list[str], refusals: list[str]) -> int:
    """Печать итога и код возврата.

    Шов `main` (`okf@1.13`): вывод отделён от суждения — так `main` остаётся
    диспетчером, а советы не мешают читать логику.
    """
    for w in warnings:
        print(f"WARNING: {w}")
    for e in refusals + errors:
        print(f"ERROR: {e}", file=sys.stderr)

    if refusals:
        return refuse(refusals)

    if not errors:
        # ⚠ Слово `OK` — это то, что уезжает в таблицу прогонов verify-report'а,
        # и до `okf@1.16` оно было одинаковым у «проверил и сошлось» и у
        # «проверять было нечем». Пустой дифф теперь ошибка (см. `judge_sync`),
        # а вторая инертность — карта без единого `implementation:` — законна на
        # развёртывании и остаётся warning'ом; но в СТРОКЕ ИТОГА она называется,
        # иначе снова попадёт в отчёт неотличимой от проверки.
        inert = "" if concepts else " — INERT: 0 concepts mapped, судить нечем"
        print(
            f"okf_sync_gate: OK ({len(concepts)} mapped concepts, "
            f"{len(warnings)} warning(s)){inert}"
        )
        return 0

    softened = soften(args, root, errors)
    if softened is not None:
        return softened
    if args.check_stale:
        print(
            f"okf_sync_gate: FAIL — {len(errors)} concept(s) past stale_after.\n"
            "Fix: re-verify the canon and set `verified:` + a new `stale_after`, "
            "or mark the concept `status: deprecated` (§7.2).",
            file=sys.stderr,
        )
    else:
        print(
            f"okf_sync_gate: FAIL — {len(errors)} concept(s) out of sync with code.\n"
            "Варианты, в порядке предпочтения:\n"
            "  1. Инвариант правда изменился → обнови concept + scope log.md (§4.1).\n"
            "  2. Смысл не менялся (типы, формат, логи, переименование) → строка\n"
            f"     `canon_drift_waiver: reason=… by=human:…` в {DELIVERY_STATUS}:\n"
            "     она видна в PR и умрёт вместе с поставкой. Правка concept'а «чтобы\n"
            "     позеленело» — ложное «обновлено», так делать нельзя.\n"
            "  3. Путь больше не описывает этот concept → убери его из "
            "`implementation:`\n"
            "     (или сузь до конкретных файлов, если срабатывания повторяются).",
            file=sys.stderr,
        )
    return 1


def parse_cli() -> argparse.Namespace:
    """Разбор аргументов вместе с фолбэком на `BASE` из окружения.

    Шов по данным: блок отдаёт наружу ровно одно имя — `args`, свободных имён у
    него нет вовсе (`ast`), поэтому граница проходит по нему без остатка. Держать
    фолбэк здесь же обязательно: «чем судить» — часть разбора входа, а не
    логики, и разъехавшись с ним, гейт снова запускался бы без базы.
    """
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="ref to diff against (e.g. origin/main)")
    ap.add_argument("--staged", action="store_true", help="use staged diff")
    ap.add_argument("--check-stale", action="store_true", help="stale_after check")
    args = ap.parse_args()

    # Фолбэк на BASE из окружения: гейты CQG получают базу именно так, и
    # расхождение конвенций (флаг здесь, переменная там) само приводило к
    # запуску без базы — то есть к зелёному гейту, не проверившему ничего.
    if not args.base and not args.staged:
        args.base = os.environ.get("BASE") or None
    return args


def judge_freshness(concepts: dict, stale: list, errors: list[str]) -> None:
    """`--check-stale`: просроченный `stale_after` — ошибка, а не предупреждение.

    Половина развилки `main`, вырезанная целиком: `ast` называет свободными
    ровно `concepts`, `stale`, `errors`, а решение о коде возврата остаётся у
    `report` — через шов не проходит ни `return`, ни `break`.
    """
    for rel, when in stale:
        errors.append(f"{rel}: stale_after {when} is in the past — re-verify or bump")
    if not stale:
        print(f"okf_sync_gate: freshness OK ({len(concepts)} mapped concepts)")


def judge_sync(args, concepts: dict, stale: list, errors: list[str],
               warnings: list[str], refusals: list[str]) -> None:
    """Sync-режим: дифф против базы против карты `implementation:` (§4.1).

    Вторая половина той же развилки. Шов здесь потому, что режимы делят только
    вход и печать: `stale` в этой ветке даёт warning, а в соседней — ошибку, и
    держать оба смысла в одной функции значило хранить развилку дважды. Замер:
    `main` 152/37 → 46/10 после `okf@1.13` и → 18/3 здесь.
    """
    for rel, when in stale:
        warnings.append(f"{rel}: stale_after {when} is in the past (§7.2)")

    files, issues = changed_files(args.base, args.staged)
    for i in issues:
        # ERROR, а не warning: невозможность вычислить дифф в sync-режиме — это
        # неверная конфигурация обязательного входа, а не отсутствие внешнего
        # инструмента. Гейт, вышедший 0 и не посмотревший ни одного файла,
        # хуже отсутствующего (Delivery §3.1a). Найдено развёртыванием: вызов
        # без --base давал WARNING и exit 0, то есть зелёный гейт, проверивший
        # ноль. Отдельно от этого база теперь берётся и из окружения BASE —
        # у гейтов CQG конвенция именно такая, и расхождение конвенций само
        # приводило к «забыл флаг».
        refusals.append(
            f"cannot compute diff: {i} — передай --base <ref> (или BASE=<ref>) "
            "либо --staged; иначе гейт не проверяет ничего"
        )
    if not files and not issues:
        # Пустой дифф = гейт не судил НИЧЕГО, и это ERROR, а не warning
        # (`okf@1.16`, поле). Прежняя редакция честно печатала «inert this run»
        # и выходила 0 — развёртывание прочло зелёное и записало в таблицу
        # прогонов «okf_sync_gate — OK». Warning против этого не работает: в
        # отчёте всё равно остаётся строка гейта, и inert от проверенного там
        # неотличим. Класс здесь был НАЗВАН верно и раньше — прежний комментарий
        # говорил «молчаливый no-op читается как «проверено»» — и лечился
        # надписью о самом себе. Знание класса не заменяет вердикта.
        #
        # Довод написан ветвью ВЫШЕ и применяется дословно: «гейт, вышедший 0 и
        # не посмотревший ни одного файла, хуже отсутствующего» (Delivery
        # §3.1a). Невозможность вычислить дифф и пустой дифф — одна ситуация для
        # читателя отчёта; вторая половина получила вердикт мягче первой только
        # потому, что выглядит штатной.
        #
        # Законный повод (прогон до коммита, push в саму базу) от этого не
        # исчезает — он теперь НАЗЫВАЕТСЯ вместо того, чтобы пройти молча.
        # Ровно так поле и ошиблось: гейт прогнали ДО коммита, дифф против
        # origin/main был пуст, гейт напечатал inert, и два расхождения нашёл
        # потом CI. «Локально зелено» и «CI зелёный» разошлись не окружением, а
        # МОМЕНТОМ прогона, и различить это может только сам гейт.
        refusals.append(
            f"diff vs '{args.base or 'staged'}' is empty — gate judged NOTHING. "
            "Прогон до коммита либо база, совпадающая с HEAD: возьми базу, "
            "против которой поставка мержится (`--base origin/main`), и уже "
            "ПОСЛЕ коммита. Зелёное здесь читалось бы как «проверено», а "
            "проверено ноль файлов"
        )
    check_concept_sync(files, concepts, errors, warnings)


def declared_okf_version(root: Path) -> str:
    """Объявлена ли ось ③ в записях о стеке. "" — не объявлена.

    Читаются те же файлы, что у доктора версий: `Canon stack:` в конституции
    поставки и в приёмке контура. `absent` — законный ответ: «решили не
    разворачивать», а не «забыли».
    """
    for rel in ("delivery/CONSTITUTION.md", "delivery/STACK-ACCEPTANCE.md"):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError:
            continue
        m = re.search(r"okf@([\w.]+)", text)
        if m and m.group(1) != "absent":
            return m.group(1)
    return ""


def main() -> int:
    args = parse_cli()

    root = repo_root()
    bundle = root / BUNDLE
    errors: list[str] = []
    warnings: list[str] = []
    #: Отказы ПРИБОРА судить (пустой дифф, невычислимая база) — отдельно от
    #: расхождений концепта с кодом. Списка два не ради порядка: waiver
    #: разрешает второе и не имеет права разрешать первое (`okf@1.18`).
    refusals: list[str] = []

    if not bundle.is_dir():
        # ⚠ Пропуск законен ТОЛЬКО пока проект не объявил ось ③ своей. Если в
        # записях о стеке стоит `okf@<версия>`, а каталога нет — это не «OKF ещё
        # не развёрнут», а РАСХОЖДЕНИЕ между заявленным и работающим, то есть тот
        # же класс, ради которого во флоте завели маячок (`delivery@1.94`).
        #
        # **Поле, `outreach-donors` 01.10:** канон домена лежит в `okf/`, гейт
        # искал дефолтный `knowledge/`, печатал `skip` и выходил нулём. Доктор
        # при этом показывал `DEAD 0` — он проверяет, что гейт ОТВЕЧАЕТ, а не
        # что ему есть что судить. Ось ③ числилась развёрнутой и не работала.
        # Нашла соседняя сессия чтением логов CI, а не прогон.
        declared = declared_okf_version(root)
        if declared:
            print(
                f"okf_sync_gate: ERROR — проект объявил okf@{declared}, а bundle "
                f"'{BUNDLE}/' не найден. Это не «ещё не развёрнут», а заявленная и "
                f"не работающая ось: гейт не судит ничего, а запись говорит, что "
                f"судит.\nВыходы: назвать каталог через OKF_BUNDLE=<путь> (у "
                f"проекта он может быть `okf/`), развернуть bundle, либо записать "
                f"okf@absent в delivery/CONSTITUTION.md — молчать нельзя.",
                file=sys.stderr,
            )
            return 1
        print(f"okf_sync_gate: no bundle at {BUNDLE}/ — skip (ось ③ не объявлена)")
        return 0

    concepts, stale = collect_concepts(root, bundle)

    if args.check_stale:
        judge_freshness(concepts, stale, errors)
    else:
        judge_sync(args, concepts, stale, errors, warnings, refusals)

    return report(args, root, concepts, errors, warnings, refusals)


if __name__ == "__main__":
    sys.exit(main())
