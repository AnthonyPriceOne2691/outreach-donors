"""Ревью рисковых мест — заметкой ветки, а не общим блоком (адаптация контура 10.11.2026).

Маячок §12.5 искал ревью в одном блоке `verify-report.md`: блок не правили с 08.10, и маячок
поднимался на каждом рисковом PR, а на `handoff` стал бы отказом каждому. Правка общего блока
каждым PR — конфликты параллельных PR. Теперь ревью — заметки `delivery/reviews/*.md`,
которые добавил сам дифф; нет их — канон как есть.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import delivery_base
import delivery_status
from delivery_risk import risk_review_gaps
from delivery_status import pick_review

STALE = "# Отчёт\n\n## Ревью рисковых мест\n\nденьги: `price_of` — риска нет, потому что …\n"
CLASSES = {"цепочка гейтов": {"scripts/lint/"}}
DIFF_IDS = {"pick_review", "review_notes", "check_risky_diff"}
NOTE = (
    "цепочка гейтов: `check_risky_diff` теперь зовёт `pick_review` с заметками `review_notes` — "
    "риска нет, потому что без заметок текст прежний."
)


def test_without_notes_the_canon_block_is_read() -> None:
    assert pick_review(STALE, []) == STALE


def test_the_branch_note_stands_instead_of_a_stale_block() -> None:
    assert risk_review_gaps(STALE, CLASSES, DIFF_IDS)  # старый блок этот дифф не цитирует
    assert risk_review_gaps(pick_review(STALE, [NOTE]), CLASSES, DIFF_IDS) == []


def test_a_note_without_a_heading_gets_one_and_a_note_with_one_keeps_it() -> None:
    assert pick_review(STALE, [NOTE]).startswith("## Ревью рисковых мест\n\n")
    own = "## Risk review\n\n" + NOTE
    assert pick_review(STALE, [own]) == own


def test_an_empty_note_is_still_a_gap() -> None:
    gaps = risk_review_gaps(pick_review(STALE, ["пока пусто"]), CLASSES, DIFF_IDS)
    assert any("цитат" in gap for gap in gaps)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_only_notes_touched_by_the_diff_are_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Заметки прежних веток лежат в папке после слияния — читаются только тронутые окном."""
    repo = tmp_path / "repo"
    reviews = repo / "delivery" / "reviews"
    reviews.mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.test")
    _git(repo, "config", "user.name", "test")
    (reviews / "old-branch.md").write_text("прежняя заметка", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "base")
    (reviews / "new-branch.md").write_text(NOTE, encoding="utf-8")
    (repo / "code.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "branch")
    # Окно меряет `delivery_base.merge_base_of` в своём корне (`git -C ROOT`) — подменяем оба.
    monkeypatch.setattr(delivery_base, "ROOT", repo)
    monkeypatch.setattr(delivery_status, "ROOT", repo)
    monkeypatch.setattr(delivery_status, "REVIEWS", reviews)

    assert delivery_status.review_notes("HEAD~1") == [NOTE]
