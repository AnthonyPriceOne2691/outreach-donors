"""Журнал действий словами: у каждого действия сервера — слова на экране.

Сервер пишет журнал кодами (`AuditAction`), экран называет их словами
(`frontend/src/api/auditLabels.ts`) и знает их списком (`frontend/src/api/journal.ts`).
Три копии одного перечня расходятся молча: новое действие без слов стояло бы в журнале
«другим действием (код)», а в фильтре его не было бы вовсе. Сверка — построчно, как
у слов прав (`test_access_words.py`).
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.features.access.journal import SALES_ACTIONS
from backend.features.core.domain import AuditAction

API = Path(__file__).resolve().parent.parent / "frontend" / "src" / "api"


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
