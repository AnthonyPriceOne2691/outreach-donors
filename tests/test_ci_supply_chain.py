"""Цепочка поставки CI: действия — по коммиту, образ — через сканер, SBOM — в артефакт.

Аудит 20.11.2026, п. 34 и 36. Метку `v4` владелец действия может передвинуть на любой
коммит, и следующий прогон исполнит его с токеном джобы: так 19.03.2026 подменили метки
trivy-action. Образ до этой правки уезжал в реестр без скана уязвимостей и без описи
состава. Тесты держат форму: новое действие по метке, скан после публикации или гейт,
который не роняет джобу, — красное здесь, а не находка следующего аудита.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"
TRIVY = "aquasecurity/trivy-action@"

#: `uses: владелец/действие@<40 hex> # vX.Y.Z` — коммит и версия, которую он значит.
PINNED = re.compile(r"^[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+(\.\d+){1,2}$")
USES = re.compile(r"^\s*(?:-\s*)?uses:\s*(.+?)\s*$")


def _uses() -> list[tuple[str, str]]:
    found = []
    for path in sorted(WORKFLOWS.glob("*.yml")):
        for line in path.read_text(encoding="utf-8").splitlines():
            match = USES.match(line)
            if match:
                found.append((path.name, match.group(1)))
    return found


def _docker_steps() -> list[dict[str, Any]]:
    workflow = yaml.safe_load((WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    steps: list[dict[str, Any]] = workflow["jobs"]["docker"]["steps"]
    return steps


def _trivy(steps: list[dict[str, Any]]) -> list[tuple[int, dict[str, Any]]]:
    return [(i, s) for i, s in enumerate(steps) if str(s.get("uses", "")).startswith(TRIVY)]


def test_every_action_is_pinned_to_a_commit_with_its_version() -> None:
    uses = _uses()

    assert len(uses) > 20, "workflow без действий — тест судил бы пустоту"
    loose = [f"{name}: {ref}" for name, ref in uses if not PINNED.match(ref)]
    assert loose == [], "действие по метке, а не по коммиту: " + "; ".join(loose)


def test_one_action_version_means_one_commit() -> None:
    """Одна и та же версия действия — один коммит во всех workflow: иначе один из них ложь."""
    seen: dict[tuple[str, str], set[str]] = {}
    for _name, ref in _uses():
        action, rest = ref.split("@", 1)
        sha, version = rest.split(" # ")
        seen.setdefault((action, version), set()).add(sha)

    assert {key: shas for key, shas in seen.items() if len(shas) > 1} == {}


def test_images_are_scanned_and_described_before_publishing() -> None:
    steps = _docker_steps()
    names = [str(s.get("name", "")) for s in steps]
    login = names.index("Войти в реестр")
    trivy = _trivy(steps)

    scanned = {s["with"]["image-ref"] for _, s in trivy}
    assert scanned == {"outreach-backend:ci", "outreach-web:ci"}
    assert all(i < login for i, _ in trivy), "скан после входа в реестр — образ уехал бы без него"

    sboms = {s["with"]["output"] for _, s in trivy if s["with"].get("format") == "cyclonedx"}
    assert sboms == {"sbom-backend.cdx.json", "sbom-web.cdx.json"}
    upload = next(
        i
        for i, s in enumerate(steps)
        if str(s.get("uses", "")).startswith("actions/upload-artifact@")
    )
    assert upload < login
    assert sboms <= set(steps[upload]["with"]["path"].split())


def test_fixable_critical_fails_the_job_and_high_only_reports() -> None:
    trivy = [s["with"] for _, s in _trivy(_docker_steps())]
    gates = [w for w in trivy if str(w.get("exit-code")) == "1"]
    reports = [w for w in trivy if w.get("format") == "table" and str(w.get("exit-code")) == "0"]

    assert {w["image-ref"] for w in gates} == {"outreach-backend:ci", "outreach-web:ci"}
    for gate in gates:
        assert gate["severity"] == "CRITICAL"
        assert gate["ignore-unfixed"] is True
        assert gate["trivyignores"] == ".trivyignore"
        assert gate["scanners"] == "vuln"
    assert {w["image-ref"] for w in reports} == {"outreach-backend:ci", "outreach-web:ci"}
    assert all(w["severity"] == "HIGH,CRITICAL" for w in reports)
    assert (ROOT / ".trivyignore").is_file()


def test_publishing_token_is_not_left_in_the_checkout() -> None:
    checkout = _docker_steps()[0]

    assert str(checkout["uses"]).startswith("actions/checkout@")
    assert checkout["with"]["persist-credentials"] is False


def test_dependabot_keeps_the_actions_fresh() -> None:
    config = yaml.safe_load((ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    actions = [u for u in config["updates"] if u["package-ecosystem"] == "github-actions"]

    assert config["version"] == 2
    assert len(actions) == 1
    assert actions[0]["directory"] == "/"
    assert actions[0]["schedule"]["interval"] == "weekly"
    assert actions[0]["cooldown"]["default-days"] >= 3
