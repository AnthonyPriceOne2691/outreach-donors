#!/usr/bin/env python3
"""Приговор гейта уязвимых зависимостей — часть `check_deps_audit.sh`.

Адаптация проекта (аудит 20.11.2026, п. 35; объявлена в `adapted.json`). Вход гейта
находит и запускает pip-audit и npm audit и кладёт их ответы в файлы; здесь всё, что
дальше: разбор ответов, waiver из STATUS, ратчет по снимку, вердикт и код возврата.

Отдельным файлом, а не строкой `python3 -c` внутри bash, потому что разбор стал
деревом: пакет npm уязвим «через» другой пакет, а waiver покрывает НАХОДКУ, не пакет.
Такое должно читаться и проверяться тестом (`tests/test_deps_audit_gate.py`), а не
жить в кавычках. Только стандартная библиотека, Python 3.9+: гейт зовут системным
`python3`, без окружения проекта.

Синтаксис waiver'а и таблица исходов — в шапке `check_deps_audit.sh`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

RED, YELLOW, GREEN, RESET = "\033[31m", "\033[33m", "\033[32m", "\033[0m"

#: Порядок несущий: по нему берётся худшая находка пакета.
SEVERITIES = ("info", "low", "moderate", "high", "critical")
RANK = {name: i for i, name in enumerate(SEVERITIES)}
#: Что снимком не легализуется (HARD_FAIL_SEVERITY=1) и что покрывает waiver.
HARD = ("critical", "high")
KEYS = ("py_total", "js_critical", "js_high", "js_total")

GHSA = re.compile(r"GHSA(?:-[0-9a-z]{4}){3}", re.IGNORECASE)
ADVISORY_ID = re.compile(r"GHSA(?:-[0-9a-z]{4}){3}|PYSEC-\d{4}-\d+|CVE-\d{4}-\d{4,}", re.IGNORECASE)
# Та же форма строки, что у сестёр (`baseline_growth_waiver`, `canon_drift_waiver`):
# пункт списка, жирное имя, двоеточие внутри или снаружи жирного.
WAIVER_LINE = re.compile(r"(?m)^[ \t]*[-*]?[ \t]*\**deps_audit_waiver\**[ \t]*:(.*)$")
# Значение «нет waiver'а» — не строка waiver'а. `no` когда-то работало как разрешение
# (приёмочное развёртывание: critical=1, а гейт выходил нулём) — здесь это отказ.
NO_WAIVER = {"", "no", "none", "-", "нет", "…"}
SIGNED = re.compile(r"^(?P<reason>.*?)\s+by=human:(?P<who>\S+)\s*$", re.DOTALL)

FORMAT = (
    "deps_audit_waiver: ghsa=<id>[,<id>…] max_critical=<N> max_high=<N> "
    "reason=<почему и когда снять> by=human:<кто решил>"
)
FORMAT_NOTES = (
    "  ghsa= — каждая покрываемая находка поимённо, через запятую без пробелов: GHSA-… (npm и\n"
    "  PyPI), для python-находки без GHSA — её PYSEC-…/CVE-…; max_critical=/max_high= — потолок\n"
    "  числа critical/high в итоге npm audit (пакеты, как их считает npm). Покрывает только\n"
    "  critical/high npm и python-находки: moderate/low судит снимок. ghsa= и max_* — до\n"
    "  reason=, by=human: — в конце строки."
)


def norm(raw: object) -> str:
    """Один вид id: `GHSA-` и строчные (так их пишет GitHub), остальное — заглавными."""
    text = str(raw or "").strip()
    if GHSA.fullmatch(text):
        return "GHSA" + text[4:].lower()
    return text.upper()


def paint(color: str, text: str) -> str:
    return f"{color}{text}{RESET}"


def say(text: str = "") -> None:
    print(text, flush=True)


def shout(text: str) -> None:
    """В stderr — после всего напечатанного в stdout: в логе CI порядок не путается."""
    sys.stdout.flush()
    print(text, file=sys.stderr, flush=True)


# --- waiver -----------------------------------------------------------------


class Waiver:
    """Строка `deps_audit_waiver:` из STATUS: есть ли, принята ли, что покрывает."""

    def __init__(self) -> None:
        self.present = False
        self.problem = ""  # почему не принята; пусто — принята
        self.ids: set[str] = set()
        self.max_critical = 0
        self.max_high = 0
        self.who = ""

    @property
    def valid(self) -> bool:
        return self.present and not self.problem


def _clean(value: str) -> str:
    return value.split("<!--", 1)[0].strip().strip("*").strip()


def read_waiver(status: Path) -> Waiver:
    waiver = Waiver()
    try:
        text = status.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return waiver
    values = [_clean(v) for v in WAIVER_LINE.findall(text)]
    values = [v for v in values if v.lower() not in NO_WAIVER and not v.startswith("<")]
    if not values:
        return waiver
    waiver.present = True
    if len(values) > 1:
        waiver.problem = f"строк deps_audit_waiver {len(values)} — гейт принимает ровно одну"
    else:
        waiver.problem = _parse_waiver(waiver, values[0])
    return waiver


def _parse_waiver(waiver: Waiver, value: str) -> str:
    """Разобрать значение в `waiver`; вернуть, почему оно не принято ("" — принято)."""
    head, has_reason, tail = value.partition("reason=")
    if "ghsa=" not in head:
        if "ghsa=" in tail:
            return "ghsa= и max_* стоят после reason= — их место до reason="
        return (
            "старый формат — нет списка находок ghsa= и потолка max_critical=/max_high=; "
            "с 20.11.2026 такая строка гейт не снимает"
        )
    signed = SIGNED.match(tail) if has_reason else None
    if not signed or not signed.group("reason").strip():
        return "нет reason=<почему> и by=human:<кто решил> в конце строки"
    fields: dict[str, str] = {}
    for token in head.split():
        key, eq, val = token.partition("=")
        if not eq or key not in ("ghsa", "max_critical", "max_high") or key in fields:
            return (
                f"непонятное «{token}» перед reason= (ждём ghsa=<id,id> без пробелов, "
                "max_critical=, max_high=)"
            )
        fields[key] = val
    for key in ("max_critical", "max_high"):
        if not fields.get(key, "").isdigit():
            return f"нет потолка {key}=<число>"
    ids = [i for i in fields["ghsa"].split(",") if i]
    bad = [i for i in ids if not ADVISORY_ID.fullmatch(i)]
    if bad:
        return f"«{bad[0]}» — не id находки (GHSA-xxxx-xxxx-xxxx, PYSEC-…, CVE-…)"
    if not ids:
        return "список ghsa= пуст"
    waiver.ids = {norm(i) for i in ids}
    waiver.max_critical = int(fields["max_critical"])
    waiver.max_high = int(fields["max_high"])
    waiver.who = signed.group("who")
    return ""


# --- ответы инструментов ------------------------------------------------------


def _last_line(path: str) -> str:
    try:
        lines = Path(path).read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError:
        return ""
    return f" — «{lines[-1].strip()[:160]}»" if lines else ""


def _load(path: str) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def read_pip(path: str) -> tuple[list[dict], list[str], int, str]:
    """(находки, пропущенные pip-audit пакеты, сколько проверено, почему не проверено).

    Различать «упал» и «нашёл» по коду возврата нельзя: pip-audit выходит ненулём и
    когда честно нашёл уязвимости. Признак — разобрался ли ответ.
    """
    data = _load(path)
    deps = data.get("dependencies") if isinstance(data, dict) else data
    if not isinstance(deps, list) or not all(isinstance(d, dict) for d in deps):
        return [], [], 0, "ответ pip-audit не разобран (сеть? база уязвимостей? инструмент упал?)"
    found: list[dict] = []
    skipped: list[str] = []
    for dep in deps:
        name = f"{dep.get('name', '?')} {dep.get('version', '')}".strip()
        if dep.get("skip_reason"):
            skipped.append(name)
            continue
        for vuln in dep.get("vulns") or []:
            ids = {norm(vuln.get("id"))} | {norm(a) for a in vuln.get("aliases") or []}
            fix = ", ".join(str(f) for f in vuln.get("fix_versions") or [])
            found.append({"id": norm(vuln.get("id")), "ids": ids - {""}, "pkg": name, "fix": fix})
    if deps and len(skipped) == len(deps):
        return [], skipped, 0, "pip-audit пропустил все пакеты — не проверено ни одного"
    return found, skipped, len(deps) - len(skipped), ""


def read_npm(path: str) -> tuple[dict, str]:
    """(пакеты из `vulnerabilities`, почему не проверено).

    Прежний разбор брал `metadata.vulnerabilities` с умолчанием `{}`: ответ-ошибка
    («реестр недоступен») давал «0 0 0», и гейт печатал OK (аудит 20.11.2026).
    """
    data = _load(path)
    if not isinstance(data, dict):
        return {}, "ответ npm audit не разобран (реестр? сеть? npm упал?)"
    err = data.get("error")
    if err is not None or ("auditReportVersion" not in data and "message" in data):
        what = (err.get("code") or err.get("summary")) if isinstance(err, dict) else (err or data.get("message"))
        return {}, f"npm audit ответил ошибкой, отчёта нет — «{str(what or '?')[:160]}»"
    vulns = data.get("vulnerabilities")
    meta = (data.get("metadata") or {}).get("vulnerabilities")
    if data.get("auditReportVersion") != 2 or not isinstance(vulns, dict) or not isinstance(meta, dict):
        return {}, "ответ npm audit не того формата (нужен auditReportVersion 2: npm 7 и новее)"
    total = meta.get("total")
    if isinstance(total, int) and total != len(vulns):
        return {}, f"разбор не сошёлся с итогом npm: пакетов {len(vulns)}, а total={total}"
    return vulns, ""


def advisory_of(via: dict) -> str:
    """Id находки из записи `via`: GHSA из ссылки, иначе номер npm (waiver'ом не покрыть)."""
    found = GHSA.search(str(via.get("url") or ""))
    return norm(found.group(0)) if found else f"npm-{via.get('source', '?')}"


class NpmTree:
    """Пакеты npm audit и находки за ними: пакет уязвим сам или «через» другой пакет."""

    def __init__(self, vulns: dict) -> None:
        self.vulns = {k: v for k, v in vulns.items() if isinstance(v, dict)}
        self.severity: dict[str, str] = {}  # находка → severity
        for entry in self.vulns.values():
            for via in entry.get("via") or []:
                if isinstance(via, dict):
                    aid, sev = advisory_of(via), str(via.get("severity", "")).lower()
                    if RANK.get(sev, -1) > RANK.get(self.severity.get(aid, ""), -1):
                        self.severity[aid] = sev

    def roots(self, name: str) -> set[str]:
        """Находки, из-за которых пакет уязвим, — с обходом цепочки «через»."""
        out: set[str] = set()
        todo, seen = [name], set()
        while todo:
            current = todo.pop()
            if current in seen:
                continue
            seen.add(current)
            for via in self.vulns.get(current, {}).get("via") or []:
                if isinstance(via, dict):
                    out.add(advisory_of(via))
                elif isinstance(via, str):
                    todo.append(via)
        return out

    def own(self, name: str) -> str:
        """Severity пакета, как её считает npm; незнакомое слово — худшее, а не ноль."""
        sev = str(self.vulns[name].get("severity", "")).lower()
        return sev if sev in RANK else "critical"


# --- приговор -----------------------------------------------------------------


class Half:
    """Половина гейта: применима ли и проверена ли."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.applicable = True
        self.why_unchecked = ""

    @property
    def checked(self) -> bool:
        return self.applicable and not self.why_unchecked


class Verdict:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.hard = os.environ.get("HARD_FAIL_SEVERITY", "1") == "1"
        self.strict = os.environ.get("STRICT", "1") != "0"
        self.waiver = read_waiver(Path(args.status))
        self.listed = self.waiver.ids if self.waiver.valid else set()
        self.used: set[str] = set()
        self.seen: set[str] = set()  # id всех находок, что нашлись
        self.now = dict.fromkeys(KEYS, 0)
        self.fails: list[str] = []
        self.notes: list[str] = []
        self.py, self.js = Half("python"), Half("js")

    def _start(self, half: Half, state: str, missing: str) -> bool:
        """Судить ли половину дальше: «не применима» и «инструмента нет» решаются здесь."""
        if state == "na":
            half.applicable = False
        elif state == "missing":
            half.why_unchecked = missing
        return state == "ran"

    def judge_python(self) -> None:
        if not self._start(self.py, self.args.py, "pip-audit не установлен (pip install pip-audit)"):
            return
        found, skipped, audited, why = read_pip(self.args.py_out)
        if why:
            self.py.why_unchecked = why + _last_line(self.args.py_err)
            return
        if skipped:
            self.notes.append(f"pip-audit пропустил {len(skipped)} пакет(ов): {', '.join(skipped[:5])}")
        rest = []
        for vuln in found:
            self.seen |= vuln["ids"]
            hit = vuln["ids"] & self.listed
            self.used |= hit
            if not hit:
                rest.append(vuln)
        self.now["py_total"] = len(rest)
        say(f"deps-audit: python — проверено пакетов {audited}, находок {len(found)}, вне waiver'а {len(rest)}")
        if rest and self.hard:
            lines = [
                f"       {v['id']}: {v['pkg']}" + (f" (исправлено в {v['fix']})" if v["fix"] else "")
                for v in rest
            ]
            self.fails.append(
                "python-находки вне waiver'а — у pip-audit нет severity, поэтому неназванная\n"
                "     python-находка снимком не легализуется (аудит 20.11.2026):\n" + "\n".join(lines)
            )

    def judge_js(self) -> None:
        if not self._start(self.js, self.args.js, "npm не найден при package-lock.json"):
            return
        vulns, why = read_npm(self.args.js_out)
        if why:
            self.js.why_unchecked = why + _last_line(self.args.js_err)
            return
        tree = NpmTree(vulns)
        self.seen |= set(tree.severity)
        waived = {a for a in self.listed if tree.severity.get(a) in HARD}
        self.used |= waived
        for aid in sorted((self.listed & set(tree.severity)) - waived):
            self.notes.append(
                f"{aid} в waiver'е — {tree.severity[aid]}: waiver покрывает только critical/high, её судит снимок"
            )
        counts = dict.fromkeys(SEVERITIES, 0)  # вне waiver'а
        total = dict.fromkeys(SEVERITIES, 0)  # как в итоге npm audit
        offenders: dict[str, set[str]] = {}  # находка → пакеты critical/high вне waiver'а
        for name in tree.vulns:
            own = tree.own(name)
            total[own] += 1
            roots = tree.roots(name)
            rest = roots - waived
            if roots and not rest:
                continue  # пакет уязвим только находками из waiver'а
            # Покрыта часть находок — severity пакета по оставшимся; не покрыто ничего —
            # ровно как у npm.
            eff = own if rest == roots else max((tree.severity.get(a, own) for a in rest), key=RANK.__getitem__)
            counts[eff] += 1
            if eff in HARD:
                named = {a for a in rest if tree.severity.get(a) in HARD} or rest or {f"пакет {name}"}
                for aid in named:
                    offenders.setdefault(aid, set()).add(name)
        # Единицы снимка — как у прежнего разбора: сумма полей metadata.vulnerabilities
        # вместе с `total`, то есть каждый пакет дважды (снимок 24 = 12 пакетов). Иначе
        # ратчет сравнивал бы с тем же снимком другие числа.
        self.now.update(js_critical=counts["critical"], js_high=counts["high"], js_total=2 * sum(counts.values()))
        say(
            f"deps-audit: npm — пакетов с уязвимостями {len(tree.vulns)} (critical {total['critical']}, "
            f"high {total['high']}, moderate {total['moderate']}, low {total['low'] + total['info']}), "
            f"целиком покрыто waiver'ом {len(tree.vulns) - sum(counts.values())}"
        )
        if offenders and self.hard:
            lines = []
            for aid in sorted(offenders):
                names = sorted(offenders[aid])
                more = f" и ещё {len(names) - 6}" if len(names) > 6 else ""
                sev = tree.severity.get(aid, "severity не названа")
                lines.append(f"       {aid} ({sev}): {', '.join(names[:6])}{more}")
            self.fails.append("critical/high вне waiver'а — снимком не легализуются:\n" + "\n".join(lines))
        if self.waiver.valid:
            for sev, cap in (("critical", self.waiver.max_critical), ("high", self.waiver.max_high)):
                if total[sev] > cap:
                    self.fails.append(
                        f"потолок waiver'а превышен: {sev} {total[sev]} > max_{sev}={cap} — "
                        "уязвимых пакетов больше, чем разрешил человек"
                    )

    def judge_baseline(self) -> None:
        for key in KEYS:
            was = base_value(Path(self.args.baseline), key)
            if self.now[key] > was:
                self.fails.append(f"{key}: {self.now[key]} (было {was})")
            elif self.now[key] < was and self.py.checked and self.js.checked:
                self.notes.append(f"снимок можно опустить: {key} {self.now[key]} < {was} (--generate)")

    def judge_waiver(self) -> None:
        w = self.waiver
        if not w.present:
            return
        if w.problem:
            text = f"строка deps_audit_waiver в {self.args.status} не принята: {w.problem}"
            if self.fails:
                self.fails.append(text)
            else:
                self.notes.append(f"{text} — сейчас снимать нечего: убери строку или перепиши так: {FORMAT}")
            return
        stale = sorted(w.ids - self.seen)
        if stale and self.py.checked and self.js.checked:
            self.notes.append(f"в waiver'е лишнее — таких находок нет: {', '.join(stale)}; убери из строки")
        if self.used:
            say(paint(YELLOW, f"deps-audit: waiver из STATUS покрывает {', '.join(sorted(self.used))} "
                              f"(max_critical={w.max_critical} max_high={w.max_high}, by=human:{w.who})"))

    def unchecked(self) -> list[str]:
        return [f"{h.name}-половина НЕ проверена: {h.why_unchecked}" for h in (self.py, self.js) if h.why_unchecked]

    def current(self) -> str:
        return " ".join(f"{key}={self.now[key]}" for key in KEYS)

    def run(self) -> int:
        self.judge_python()
        self.judge_js()
        if self.args.generate:
            return self.generate()
        self.judge_baseline()
        self.judge_waiver()
        return self.report()

    def generate(self) -> int:
        if self.unchecked():
            for line in self.unchecked():
                shout(paint(RED, f"  ✗  {line}"))
            shout("снимок не переписан: нули непроверенной половины легализовали бы непроверенное")
            return 1
        Path(self.args.baseline).write_text(
            "# deps_audit_baseline.txt — снимок известных уязвимостей в зависимостях.\n"
            "# Снимок только ТАЕТ: обновил либу — пере-сними вниз.\n"
            "# НОВЫЕ critical/high не легализуются снимком (HARD_FAIL_SEVERITY=1).\n"
            f"{self.current()}\n",
            encoding="utf-8",
        )
        say(paint(GREEN, "baseline пересобран:") + f" {self.current()}")
        return 0

    def report(self) -> int:
        for note in self.notes:
            say(f"  · заметка: {note}")
        for line in self.unchecked():
            say(f"  ○ не проверено: {line}")
        for fail in self.fails:
            say(paint(RED, f"  ✗  {fail}"))
        checked = sum(h.checked for h in (self.py, self.js))
        if self.fails:
            shout(paint(RED, "\nERROR") + ": уязвимости в зависимостях выросли, есть critical/high или "
                  "python-находки вне waiver'а.")
            shout("Починка: обновить пакет (npm audit fix / uv lock --upgrade-package …), затем --generate.\n"
                  "Если апстрим фикса не выпустил — одна строка в delivery/active/STATUS.md (§4.3a Delivery:\n"
                  f"видна в PR, живёт одну поставку):\n  {FORMAT}\n{FORMAT_NOTES}")
            if not self.strict:
                shout(paint(YELLOW, "WARNING (STRICT=0)"))
                return 0
            return 1
        if self.unchecked():
            # Ноль непроверенной половины — не доказательство, а отсутствие ответа. Раньше
            # он печатался как «0 0 0 → OK» (аудит 20.11.2026): в CI это красное.
            word = "НЕ ПРОВЕРЕНО" if self.strict else "WARNING (STRICT=0)"
            say(paint(RED if self.strict else YELLOW, f"deps-audit: {word}") + f" — просмотрено {checked} "
                f"манифест(ов), проверено НЕ ВСЁ: {self.current()}")
            say("Ноль здесь не доказательство: инструмент не ответил. Повтори прогон; при STRICT=1 это красное.")
            return 1 if self.strict else 0
        if not (self.py.applicable and self.js.applicable):
            say(paint(YELLOW, "deps-audit: WARNING") + f" — просмотрено {checked} манифест(ов), "
                f"проверено НЕ ВСЁ: {self.current()}")
            say("Половина гейта не применима (см. «пропущено» выше): ноль там не доказательство.")
            return 0
        say(paint(GREEN, "deps-audit: OK") + f" — просмотрено {checked} манифест(ов), {self.current()}")
        return 0


def base_value(baseline: Path, key: str) -> int:
    """Число из снимка; строки-комментарии не читаются (в них бывает `key=` в тексте)."""
    try:
        text = baseline.read_text(encoding="utf-8")
    except OSError:
        return 0
    found = re.search(rf"(?m)^[^#\n]*\b{key}=(\d+)", text)
    return int(found.group(1)) if found else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="приговор гейта уязвимых зависимостей")
    parser.add_argument("--py", choices=("na", "missing", "ran"), required=True)
    parser.add_argument("--py-out", default="")
    parser.add_argument("--py-err", default="")
    parser.add_argument("--js", choices=("na", "missing", "ran"), required=True)
    parser.add_argument("--js-out", default="")
    parser.add_argument("--js-err", default="")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--status", default="delivery/active/STATUS.md")
    parser.add_argument("--generate", action="store_true")
    return Verdict(parser.parse_args(argv)).run()


if __name__ == "__main__":
    sys.exit(main())
