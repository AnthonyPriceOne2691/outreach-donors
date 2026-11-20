"""Гейт уязвимых зависимостей: waiver называет находки и потолок, молчание инструмента — не ноль.

Аудит 20.11.2026 (п. 35): одна строка `deps_audit_waiver: reason=… by=human:…` в STATUS
выключала гейт целиком — любой рост, любые critical/high, python-половина тоже, — а npm
audit без ответа реестра давал «0 0 0 → OK». Тесты гоняют сам `check_deps_audit.sh` во
временном репозитории: вместо `npm` и `pip-audit` — подставные, отдающие ответ настоящего
формата, а в PATH нет ничего, кроме них и нужных гейту утилит, — сеть не нужна.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINT = ROOT / "scripts" / "lint"
sys.path.insert(0, str(LINT))

import deps_audit_verdict as verdict

BASH = shutil.which("bash") or "/bin/bash"
HIGH_A = "GHSA-2222-3333-4444"  # находка «через» цепочку пакетов, как была у picomatch
HIGH_B = "GHSA-5555-6666-7777"
MODERATE = "GHSA-8888-9999-aaaa"
TOOLS = ("git", "dirname", "mktemp", "rm")


def advisory(ghsa: str, severity: str, package: str) -> dict:
    return {
        "source": 1100001,
        "name": package,
        "dependency": package,
        "title": "ReDoS",
        "url": f"https://github.com/advisories/{ghsa}",
        "severity": severity,
        "range": "<=4.0.7",
    }


def npm_report(*packages: tuple[str, str, list]) -> dict:
    """Отчёт `npm audit --json` (auditReportVersion 2): (имя, severity, via)."""
    meta = dict.fromkeys(verdict.SEVERITIES, 0)
    vulns = {}
    for name, severity, via in packages:
        vulns[name] = {
            "name": name,
            "severity": severity,
            "isDirect": False,
            "via": via,
            "effects": [],
            "range": "*",
            "nodes": [f"node_modules/{name}"],
            "fixAvailable": False,
        }
        meta[severity] += 1
    meta["total"] = len(vulns)
    return {
        "auditReportVersion": 2,
        "vulnerabilities": vulns,
        "metadata": {"vulnerabilities": meta},
    }


def chain(count: int, ghsa: str = HIGH_A) -> list[tuple[str, str, list]]:
    """Уязвимый пакет и `count - 1` пакетов, уязвимых «через» него, — как picomatch и vite."""
    root = ("picomatch", "high", [advisory(ghsa, "high", "picomatch")])
    return [root] + [(f"tool{i}", "high", ["picomatch"]) for i in range(1, count)]


def moderate(count: int) -> list[tuple[str, str, list]]:
    root = ("minimatch", "moderate", [advisory(MODERATE, "moderate", "minimatch")])
    return [root] + [(f"lint{i}", "moderate", ["minimatch"]) for i in range(1, count)]


def pip_report(*vulns: tuple[str, str, list[str]]) -> dict:
    """Отчёт `pip-audit --format=json`: (пакет, id, псевдонимы)."""
    deps = [{"name": "fastapi", "version": "0.141.1", "vulns": []}]
    for package, vid, aliases in vulns:
        deps.append(
            {
                "name": package,
                "version": "2.14.0",
                "vulns": [
                    {"id": vid, "fix_versions": ["2.15.0"], "aliases": aliases, "description": "…"}
                ],
            }
        )
    return {"dependencies": deps, "fixes": []}


WAIVER = f"- **deps_audit_waiver:** ghsa={HIGH_A} max_critical=0 max_high=7 reason=фикса нет by=human:anthony"
OLD_WAIVER = "- **deps_audit_waiver:** reason=picomatch ReDoS, фикса нет by=human:anthony"


class Lab:
    """Временный репозиторий с копией гейта и подставными инструментами."""

    def __init__(self, root: Path) -> None:
        self.root = root / "repo"
        self.bin = root / "bin"
        lint = self.root / "scripts" / "lint"
        lint.mkdir(parents=True)
        self.bin.mkdir()
        for name in ("check_deps_audit.sh", "deps_audit_verdict.py"):
            shutil.copy(LINT / name, lint / name)
        self.baseline("py_total=0 js_critical=0 js_high=0 js_total=24")
        (self.root / "pyproject.toml").write_text('[project]\nname = "app"\n', encoding="utf-8")
        (self.root / "frontend").mkdir()
        (self.root / "frontend" / "package-lock.json").write_text("{}", encoding="utf-8")
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        # Только то, что гейт зовёт сам: найди он настоящий npm, uv или pip-audit — пошёл бы в сеть.
        for tool in TOOLS:
            found = shutil.which(tool)
            assert found, f"для теста нужен {tool}"
            (self.bin / tool).symlink_to(found)
        (self.bin / "python3").symlink_to(os.path.realpath(sys.executable))
        self.npm(npm_report(*moderate(7)))
        self.pip(pip_report())

    def baseline(self, line: str) -> None:
        (self.root / "scripts" / "lint" / "deps_audit_baseline.txt").write_text(
            f"# снимок\n{line}\n", encoding="utf-8"
        )

    def status(self, *lines: str) -> None:
        path = self.root / "delivery" / "active" / "STATUS.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Active delivery status\n\n" + "\n".join(lines) + "\n", encoding="utf-8")

    def _tool(self, name: str, stdout: str | None, stderr: str = "") -> None:
        body = f"#!{BASH}\n"
        if stdout is not None:
            (self.bin / f"{name}.out").write_text(stdout, encoding="utf-8")
            body += f'printf "%s" "$(<"{self.bin / name}.out")"\n'
        if stderr:
            body += f"printf '%s\\n' '{stderr}' >&2\n"
        body += "exit 1\n"  # оба инструмента выходят ненулём и когда нашли — признак не в коде
        script = self.bin / name
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)

    def npm(self, report: dict | None, *, raw: str | None = None, stderr: str = "") -> None:
        self._tool("npm", raw if report is None else json.dumps(report), stderr)

    def pip(self, report: dict | None, *, raw: str | None = None, stderr: str = "") -> None:
        self._tool("pip-audit", raw if report is None else json.dumps(report), stderr)

    def drop(self, name: str) -> None:
        (self.bin / name).unlink()

    def run(self, *args: str, strict: str = "1") -> tuple[int, str]:
        env = {
            "PATH": str(self.bin),
            "HOME": str(self.root),
            "TMPDIR": str(self.root.parent),
            "STRICT": strict,
            "LINT_BE_DIR": ".",
            "LINT_FE_DIR": "frontend",
            "LINT_VENV": "нет-такого-venv",
        }
        done = subprocess.run(
            [BASH, str(self.root / "scripts" / "lint" / "check_deps_audit.sh"), *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        return done.returncode, done.stdout + done.stderr


@pytest.fixture
def lab(tmp_path: Path) -> Lab:
    return Lab(tmp_path)


def test_clean_tree_is_ok(lab: Lab) -> None:
    code, out = lab.run()

    assert code == 0, out
    assert "deps-audit: OK" in out
    assert "py_total=0 js_critical=0 js_high=0 js_total=14" in out


def test_high_without_waiver_fails_and_names_the_advisory(lab: Lab) -> None:
    lab.npm(npm_report(*chain(7)))

    code, out = lab.run()

    assert code == 1, out
    assert f"{HIGH_A} (high): picomatch, tool1" in out
    assert "js_high: 7 (было 0)" in out
    assert "ghsa=<id>" in out  # ошибка объясняет, как выглядит исключение


def test_soft_mode_reports_but_passes(lab: Lab) -> None:
    lab.npm(npm_report(*chain(7)))

    code, out = lab.run(strict="0")

    assert code == 0, out
    assert "WARNING (STRICT=0)" in out


def test_named_waiver_within_ceiling_passes(lab: Lab) -> None:
    lab.npm(npm_report(*chain(7), *moderate(7)))
    lab.status(WAIVER)

    code, out = lab.run()

    assert code == 0, out
    assert f"waiver из STATUS покрывает {HIGH_A}" in out
    assert "js_high=0 js_total=14" in out  # покрытые пакеты не идут ни в high, ни в снимок


def test_growth_beyond_the_ceiling_fails(lab: Lab) -> None:
    lab.npm(npm_report(*chain(8)))
    lab.status(WAIVER)

    code, out = lab.run()

    assert code == 1, out
    assert "потолок waiver'а превышен: high 8 > max_high=7" in out


def test_an_advisory_missing_from_the_waiver_fails(lab: Lab) -> None:
    other = ("braces", "high", [advisory(HIGH_B, "high", "braces")])
    lab.npm(npm_report(*chain(6), other))
    lab.status(WAIVER)

    code, out = lab.run()

    assert code == 1, out
    assert f"{HIGH_B} (high): braces" in out
    assert f"{HIGH_A} (high)" not in out  # покрытая находка в список нарушений не попадает


def test_a_package_also_hit_by_an_unlisted_high_stays_high(lab: Lab) -> None:
    both = ("tool1", "high", ["picomatch", advisory(HIGH_B, "high", "tool1")])
    lab.npm(npm_report(chain(1)[0], both))
    lab.status(WAIVER)

    code, out = lab.run()

    assert code == 1, out
    assert f"{HIGH_B} (high): tool1" in out


def test_the_waiver_does_not_legalize_moderate_growth(lab: Lab) -> None:
    lab.npm(npm_report(*chain(7), *moderate(13)))
    lab.status(WAIVER)

    code, out = lab.run()

    assert code == 1, out
    assert "js_total: 26 (было 24)" in out


def test_old_format_waiver_no_longer_disables_the_gate(lab: Lab) -> None:
    lab.npm(npm_report(*chain(7)))
    lab.status(OLD_WAIVER)

    code, out = lab.run()

    assert code == 1, out
    assert "старый формат" in out
    assert "max_critical=<N> max_high=<N>" in out


def test_old_format_waiver_with_nothing_to_waive_is_only_a_note(lab: Lab) -> None:
    lab.status(OLD_WAIVER)

    code, out = lab.run()

    assert code == 0, out
    assert "заметка: строка deps_audit_waiver" in out
    assert "старый формат" in out


def test_waiver_without_a_ceiling_is_rejected(lab: Lab) -> None:
    lab.npm(npm_report(*chain(7)))
    lab.status(f"- **deps_audit_waiver:** ghsa={HIGH_A} max_critical=0 reason=… by=human:anthony")

    code, out = lab.run()

    assert code == 1, out
    assert "нет потолка max_high" in out


def test_python_finding_outside_the_waiver_fails_even_with_a_loose_baseline(lab: Lab) -> None:
    lab.baseline("py_total=5 js_critical=0 js_high=0 js_total=24")
    lab.pip(pip_report(("pyjwt", "PYSEC-2026-11", ["CVE-2026-12345", HIGH_B])))

    code, out = lab.run()

    assert code == 1, out
    assert "PYSEC-2026-11: pyjwt 2.14.0 (исправлено в 2.15.0)" in out
    assert "у pip-audit нет severity" in out


def test_python_finding_listed_by_an_alias_passes(lab: Lab) -> None:
    lab.pip(pip_report(("pyjwt", "PYSEC-2026-11", ["CVE-2026-12345"])))
    lab.status(
        "- deps_audit_waiver: ghsa=CVE-2026-12345 max_critical=0 max_high=0 reason=… by=human:anthony"
    )

    code, out = lab.run()

    assert code == 0, out
    assert "waiver из STATUS покрывает CVE-2026-12345" in out


@pytest.mark.parametrize(
    ("raw", "stderr", "said"),
    [
        (
            json.dumps({"error": {"code": "ENOTFOUND", "summary": "request failed"}}),
            "",
            "ENOTFOUND",
        ),
        ("", "npm error code E500", "E500"),
        ('{"auditReportVersion": 2, "vulnerab', "", "не разобран"),
    ],
    ids=["registry-error", "crashed", "truncated"],
)
def test_npm_that_did_not_answer_is_unchecked_not_zero(
    lab: Lab, raw: str, stderr: str, said: str
) -> None:
    lab.npm(None, raw=raw, stderr=stderr)

    strict_code, strict_out = lab.run()
    soft_code, soft_out = lab.run(strict="0")

    assert strict_code == 1, strict_out
    assert "js-половина НЕ проверена" in strict_out
    assert said in strict_out
    assert "deps-audit: OK" not in strict_out
    assert soft_code == 0, soft_out
    assert "WARNING (STRICT=0)" in soft_out


def test_pip_audit_that_did_not_answer_is_unchecked(lab: Lab) -> None:
    lab.pip(None, raw="", stderr="ConnectionError: pypi.org unreachable")

    code, out = lab.run()

    assert code == 1, out
    assert "python-половина НЕ проверена" in out
    assert "ConnectionError" in out


def test_a_missing_tool_next_to_its_manifest_is_unchecked(lab: Lab) -> None:
    lab.drop("npm")

    strict_code, strict_out = lab.run()
    soft_code, _ = lab.run(strict="0")

    assert strict_code == 1, strict_out
    assert "npm не найден" in strict_out
    assert soft_code == 0


def test_without_manifests_the_skip_is_named_and_never_ok(lab: Lab) -> None:
    """Форма, которую судит доктор контура (`_probe_honest_skip`): пропуск назван, OK нет."""
    (lab.root / "pyproject.toml").unlink()
    (lab.root / "frontend" / "package-lock.json").unlink()

    code, out = lab.run()

    assert code == 0, out
    assert "пропущено" in out
    assert "WARNING" in out
    assert "OK" not in out


def test_generate_refuses_an_unchecked_half_and_writes_a_checked_tree(lab: Lab) -> None:
    baseline = lab.root / "scripts" / "lint" / "deps_audit_baseline.txt"
    lab.npm(None, raw="")

    refused, out = lab.run("--generate")

    assert refused == 1, out
    assert "снимок не переписан" in out
    assert "js_total=24" in baseline.read_text(encoding="utf-8")

    lab.npm(npm_report(*moderate(7)))
    written, out = lab.run("--generate")

    assert written == 0, out
    assert "py_total=0 js_critical=0 js_high=0 js_total=14" in baseline.read_text(encoding="utf-8")


# --- строка waiver'а: формы, которые пишут руками --------------------------------------


def _waiver(tmp_path: Path, *lines: str) -> verdict.Waiver:
    status = tmp_path / "STATUS.md"
    status.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return verdict.read_waiver(status)


def test_waiver_forms(tmp_path: Path) -> None:
    w = _waiver(
        tmp_path,
        f"* deps_audit_waiver: ghsa={HIGH_A.lower()},PYSEC-2026-11 max_high=3 "
        "max_critical=1 reason=ждём фикс до 01.12 by=human:anthony",
    )
    assert w.valid, w.problem
    assert w.ids == {HIGH_A, "PYSEC-2026-11"}
    assert (w.max_critical, w.max_high, w.who) == (1, 3, "anthony")


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("- deps_audit_waiver: no", ""),
        ("- **deps_audit_waiver:** <!-- нет -->", ""),
        (OLD_WAIVER, "старый формат"),
        (
            f"- deps_audit_waiver: reason=… ghsa={HIGH_A} max_critical=0 max_high=1 by=human:a",
            "после reason=",
        ),
        (
            f"- deps_audit_waiver: ghsa={HIGH_A}, {HIGH_B} max_critical=0 max_high=1 reason=… by=human:a",
            "без пробелов",
        ),
        (
            "- deps_audit_waiver: ghsa=GHSA-1 max_critical=0 max_high=1 reason=… by=human:a",
            "не id находки",
        ),
        (
            f"- deps_audit_waiver: ghsa={HIGH_A} max_critical=0 max_high=1 reason=… by=agent:a",
            "by=human",
        ),
    ],
    ids=["no", "placeholder", "old", "order", "spaces", "bad-id", "not-human"],
)
def test_waiver_refusals(tmp_path: Path, line: str, problem: str) -> None:
    w = _waiver(tmp_path, line)

    if not problem:
        assert not w.present
    else:
        assert w.present
        assert problem in w.problem, w.problem


def test_two_waiver_lines_are_ambiguous(tmp_path: Path) -> None:
    w = _waiver(tmp_path, WAIVER, WAIVER.replace("7", "9"))

    assert w.present
    assert "ровно одну" in w.problem
