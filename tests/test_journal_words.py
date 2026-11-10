"""Журнал действий словами: у каждого действия сервера — слова на экране.

Сервер пишет журнал кодами (`AuditAction`), экран называет их словами
(`frontend/src/api/auditLabels.ts`) и знает их списком (`frontend/src/api/journal.ts`).
Три копии одного перечня расходятся молча: новое действие без слов стояло бы в журнале
«другим действием (код)», а в фильтре его не было бы вовсе. Сверка — построчно, как
у слов прав (`test_access_words.py`).

Цель записи — так же: у каждого вида цели, который сервер пишет строкой в вызове
(`target=f"queue_autosend:{…}"`, `target="sales_kb"`), есть слова на экране (`TARGETS`,
`WHOLE_TARGETS`). Без них «Над чем» показывало код — «queue_autosend:2» (проверка прода
10.11.2026).
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.features.access.journal import SALES_ACTIONS
from backend.features.core.domain import AuditAction

ROOT = Path(__file__).resolve().parent.parent
API = ROOT / "frontend" / "src" / "api"

#: Цель записи строкой в вызове: вид с номером (`target=f"run:{…}"`) или целиком
#: (`target="sales_kb"`, `target="advertisers:promote"`).
NUMBERED = re.compile(r'\btarget=f"([a-z_]+):\{')
WHOLE = re.compile(r'\btarget="([a-z_:-]+)"')


def _words() -> dict[str, str]:
    """`код: 'слова'` из `AUDIT_ACTIONS` экрана."""
    source = (API / "auditLabels.ts").read_text(encoding="utf-8")
    found = re.search(r"const AUDIT_ACTIONS\b[^=]*=\s*\{(.*?)\n\};", source, re.S)
    assert found is not None, "в auditLabels.ts нет AUDIT_ACTIONS"
    return dict(re.findall(r"^\s*([a-z_]+): '([^']+)',", found.group(1), re.M))


def _union() -> set[str]:
    """Значения типа `AuditAction` экрана."""
    source = (API / "journal.ts").read_text(encoding="utf-8")
    found = re.search(r"export type AuditAction =(.*?);", source, re.S)
    assert found is not None, "в journal.ts нет типа AuditAction"
    return set(re.findall(r"'([a-z_]+)'", found.group(1)))


def _sales_on_screen() -> set[str]:
    source = (API / "auditLabels.ts").read_text(encoding="utf-8")
    found = re.search(r"const SALES_ACTIONS\b.*?\[(.*?)\]", source, re.S)
    assert found is not None, "в auditLabels.ts нет SALES_ACTIONS"
    return set(re.findall(r"'([a-z_]+)'", found.group(1)))


def _screen_keys(name: str) -> set[str]:
    """Ключи словаря целей экрана: `run: { … }`, `'advertisers:promote': '…'`."""
    source = (API / "auditLabels.ts").read_text(encoding="utf-8")
    # До `= {` в конце строки: в типе словаря целей есть своя стрелка `=>`.
    found = re.search(rf"const {name}\b[^\n]*=\s*\{{\n(.*?)\n\}};", source, re.S)
    assert found is not None, f"в auditLabels.ts нет {name}"
    return set(re.findall(r"^\s*'?([a-z_:-]+)'?:", found.group(1), re.M))


def _server_targets() -> tuple[set[str], set[str]]:
    """Виды целей с номером и цели целиком — как их пишет сервер."""
    numbered: set[str] = set()
    whole: set[str] = set()
    for path in (ROOT / "backend").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        numbered.update(NUMBERED.findall(source))
        whole.update(WHOLE.findall(source))
    return numbered, whole


def test_every_action_has_words_on_the_screen() -> None:
    assert set(_words()) == {action.value for action in AuditAction}


def test_screen_knows_every_action_by_type() -> None:
    assert _union() == {action.value for action in AuditAction}


def test_sales_actions_are_the_same_on_both_sides() -> None:
    """Без права «Продажи» их нет ни в журнале (сервер), ни в фильтре (экран)."""
    assert _sales_on_screen() == {action.value for action in SALES_ACTIONS}


def test_words_are_words_not_codes() -> None:
    for code, words in _words().items():
        assert "_" not in words, f"{code}: «{words}» — код, а не слова"
        assert words[0].isupper(), f"{code}: «{words}» — подпись строки начинается с заглавной"


def test_every_target_has_words_on_the_screen() -> None:
    """Цель без слов «Над чем» показывало кодом; находка — у любого вида, а не у одного."""
    numbered, whole = _server_targets()
    assert numbered, "сервер не пишет ни одной цели с номером — разбор вызовов сломан"
    assert numbered <= _screen_keys("TARGETS"), numbered - _screen_keys("TARGETS")
    assert whole <= _screen_keys("WHOLE_TARGETS"), whole - _screen_keys("WHOLE_TARGETS")
