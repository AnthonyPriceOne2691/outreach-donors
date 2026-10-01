"""Своя тестовая база у каждого дерева.

Прогон начинается со сноса схемы, и с одной базой на все деревья прогон
в одном дереве сносил её под прогоном в другом: 01.10.2026 pre-push дал
«20 failed, 1 error», а в тишине тот же набор был зелёным.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import make_url
from tests.conftest import own_test_dsn


def test_each_tree_gets_its_own_database() -> None:
    main = make_url(own_test_dsn(Path("/repo"))).database
    tree = make_url(own_test_dsn(Path("/repo/.claude/worktrees/task"))).database
    assert main != tree
    assert main is not None
    assert main.startswith("outreach_test_")


def test_the_same_tree_keeps_its_database() -> None:
    """Иначе каждый прогон заводил бы новую базу, а старые копились бы."""
    assert own_test_dsn(Path("/repo")) == own_test_dsn(Path("/repo"))
